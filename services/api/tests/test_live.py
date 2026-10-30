"""实时数据：解析各来源、R21–R24、LiveService 的联网时机和缓存。全部用样例数据，不连外网。"""

from datetime import date, datetime, timedelta, timezone

import pytest

from tripagent.live.geo import in_polygon, lines_near, parse_wkt, thin
from tripagent.live.holidays import holidays_between
from tripagent.live.models import DayForecast, LiveSnapshot, RoadEvent, WeatherAlert
from tripagent.live.service import LiveService
from tripagent.live.sources import feed_links, parse_cap, parse_nzta, parse_open_meteo
from tripagent.schema import Trip
from tripagent.validator import ValidationContext, validate

from .conftest import nz

# 测试用坐标：民宿、滑翔伞集合点、蒂阿瑙
JACKS = (-45.066, 168.742)
CORONET = (-44.926, 168.736)
TE_ANAU = (-45.414, 167.718)


def find(issues, code):
    return [i for i in issues if i.code == code]


class RouteStub:
    """在静态车程表外面加上路线形状：两点之间连直线，每隔约 1 公里一个点。"""

    def __init__(self, base, coords):
        self.base, self.coords = base, coords

    def estimate(self, a, b):
        return self.base.estimate(a, b)

    def geometry(self, a, b):
        if a not in self.coords or b not in self.coords:
            return None
        (la, oa), (lb, ob) = self.coords[a], self.coords[b]
        return [(la + (lb - la) * k / 40, oa + (ob - oa) * k / 40) for k in range(41)]


@pytest.fixture
def geo_places(places, trip):
    """给测试用到的地点补上坐标。"""
    coords = {"acc_queenstown_jacks_point": JACKS, "nz_skytrek_paragliding": CORONET}
    merged = {**places, **{p.id: p for p in trip.custom_places}}
    return {pid: (p.model_copy(update={"lat": coords[pid][0], "lon": coords[pid][1]}) if pid in coords else p)
            for pid, p in merged.items()}, coords


def live_ctx(trip, geo_places, drive_times, snap):
    places, coords = geo_places
    ctx = ValidationContext(trip, places, RouteStub(drive_times, coords), live=snap)
    ctx.places = places  # 用补了坐标的版本覆盖行程自带地点
    return ctx


def event(**kw) -> RoadEvent:
    base = dict(id="nzta_1", kind="Road Work", impact="Road Closed", title="Coronet Peak Road",
                start=nz("2026-11-24", "06:00"), end=nz("2026-11-24", "12:00"),
                lines=[[(-44.99, 168.739), (-44.98, 168.739)]])
    return RoadEvent(**{**base, **kw})


# ---------------- 解析 ----------------

def test_parse_wkt_point_and_multiline():
    assert parse_wkt("POINT (168.7 -45.0)") == [[(-45.0, 168.7)]]
    lines = parse_wkt("MULTILINESTRING ((168.30 -46.54, 168.29 -46.56), (168.1 -46.1, 168.2 -46.2))")
    assert lines == [[(-46.54, 168.30), (-46.56, 168.29)], [(-46.1, 168.1), (-46.2, 168.2)]]
    assert parse_wkt(None) == []


def test_parse_nzta_event():
    data = {"response": {"roadevent": [{
        "id": 565247, "eventType": "Road Work", "impact": "Road Closed", "locationArea": "SH 6 Haast to Makarora",
        "eventComments": "Overnight closure 8pm–5am. ", "region": {"name": "West Coast"},
        "startDate": "2026-11-24T20:00:00.677+13:00", "endDate": "2026-11-26T05:00:00.677+13:00",
        "planned": True, "geometry": "MULTILINESTRING ((169.04 -44.10, 169.10 -44.20))"}]}}
    [e] = parse_nzta(data)
    assert e.id == "nzta_565247" and e.impact == "Road Closed" and e.region == "West Coast"
    assert e.detail == "Overnight closure 8pm–5am." and e.lines == [[(-44.10, 169.04), (-44.20, 169.10)]]
    assert e.start.utcoffset() == timedelta(hours=13)


CAP = """<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>2.49.0.1.554.0.2026.11.24.0900</identifier>
  <msgType>Alert</msgType>
  <info>
    <language>en-NZ</language>
    <event>Heavy Rain Warning - Orange</event>
    <severity>Moderate</severity>
    <onset>2026-11-24T09:00:00+13:00</onset>
    <expires>2026-11-25T21:00:00+13:00</expires>
    <headline>Heavy Rain Warning for Fiordland</headline>
    <description>Expect 150 to 200 mm of rain.</description>
    <web>https://www.metservice.com/warnings/home</web>
    <area>
      <areaDesc>Fiordland south of George Sound</areaDesc>
      <polygon>-45.0,167.0 -45.0,168.0 -46.0,168.0 -46.0,167.0 -45.0,167.0</polygon>
    </area>
  </info>
</alert>"""


def test_parse_cap_and_feed():
    [a] = parse_cap(CAP, "https://alerts.metservice.com/cap/x")
    assert a.event == "Heavy Rain Warning - Orange" and a.areas == ["Fiordland south of George Sound"]
    assert a.polygons[0][0] == (-45.0, 167.0) and a.expires.day == 25
    assert in_polygon(TE_ANAU, a.polygons[0]) and not in_polygon(JACKS, a.polygons[0])
    cancel = CAP.replace("<msgType>Alert</msgType>", "<msgType>Cancel</msgType>")
    assert parse_cap(cancel) == []
    rss = ("<rss><channel><link>https://www.metservice.com</link>"
           "<item><title>x</title><link>https://alerts.metservice.com/cap/alert?id=1</link></item></channel></rss>")
    assert feed_links(rss) == ["https://alerts.metservice.com/cap/alert?id=1"]


def test_parse_open_meteo_multi_point():
    rows = [{"daily": {"time": ["2026-11-24"], "weather_code": [80], "temperature_2m_max": [17.0],
                       "temperature_2m_min": [9.0], "precipitation_sum": [22.5],
                       "precipitation_probability_max": [90], "wind_gusts_10m_max": [41.0]}},
            {"daily": {"time": ["2026-11-24"], "weather_code": [0]}}]
    a, b = parse_open_meteo(rows, [JACKS, TE_ANAU])
    assert (a.lat, a.lon, a.precip_mm, a.precip_prob) == (*JACKS, 22.5, 90)
    assert b.code == 0 and b.tmax is None


def test_geometry_helpers():
    route = [(-45.0, 168.7), (-45.1, 168.7)]
    assert lines_near([(-45.05, 168.702)], route, 300)       # 约 160 米
    assert not lines_near([(-45.05, 168.72)], route, 300)    # 约 1.6 公里
    assert len(thin([(-45.0, 168.7 + k * 0.0001) for k in range(100)], 200)) < 10


# ---------------- R21 路况 ----------------

def test_r21_closure_on_route(trip, geo_places, drive_times):
    snap = LiveSnapshot(road_events=[event()])
    [issue] = find(validate(live_ctx(trip, geo_places, drive_times, snap)), "R21")
    assert issue.item_ids == ["d2_drive_coronet"] and "封路" in issue.message and "Coronet Peak Road" in issue.message


@pytest.mark.parametrize("change", [
    {"impact": "Caution"},                                                  # 只是提醒注意，不报
    {"lines": [[(-44.99, 168.80), (-44.98, 168.80)]]},                      # 离路线约 5 公里
    {"start": nz("2026-11-24", "10:00"), "end": nz("2026-11-24", "12:00")},  # 开车时还没开始
])
def test_r21_ignores_irrelevant_events(trip, geo_places, drive_times, change):
    snap = LiveSnapshot(road_events=[event(**change)])
    assert find(validate(live_ctx(trip, geo_places, drive_times, snap)), "R21") == []


def test_live_rules_skip_without_snapshot(trip, geo_places, drive_times):
    issues = validate(live_ctx(trip, geo_places, drive_times, None))
    assert not [i for i in issues if i.code in ("R21", "R22", "R23")]


# ---------------- R22 天气预警 ----------------

def test_r22_alert_covers_items(trip, geo_places, drive_times):
    poly = [(-44.8, 168.6), (-44.8, 168.9), (-45.0, 168.9), (-45.0, 168.6)]   # 盖住滑翔伞集合点，不含民宿
    alert = WeatherAlert(id="a1", event="Strong Wind Warning - Orange", areas=["Queenstown Lakes"],
                         polygons=[poly], onset=nz("2026-11-24", "06:00"), expires=nz("2026-11-24", "12:00"))
    [issue] = find(validate(live_ctx(trip, geo_places, drive_times, LiveSnapshot(weather_alerts=[alert]))), "R22")
    assert "Strong Wind Warning" in issue.message and "11/24" in issue.message
    assert "d2_skytrek" in issue.item_ids and "d2_drive_coronet" in issue.item_ids


# ---------------- R23 天气预报 ----------------

def forecast(**kw) -> DayForecast:
    return DayForecast(**{"lat": CORONET[0], "lon": CORONET[1], "date": date(2026, 11, 24), "code": 3,
                          "precip_mm": 0, "precip_prob": 10, "gust_kmh": 20, **kw})


def test_r23_wind_and_rain_on_paragliding(trip, geo_places, drive_times):
    snap = LiveSnapshot(forecasts=[forecast(code=81, precip_mm=18, precip_prob=85, gust_kmh=72)])
    [issue] = find(validate(live_ctx(trip, geo_places, drive_times, snap)), "R23")
    assert issue.item_ids == ["d2_skytrek"]
    assert "降雨约 18 毫米（概率 85%）" in issue.message and "阵风 72 km/h" in issue.message


def test_r23_good_weather_and_snowy_drive(trip, geo_places, drive_times):
    snap = LiveSnapshot(forecasts=[forecast()])
    assert find(validate(live_ctx(trip, geo_places, drive_times, snap)), "R23") == []
    snow = LiveSnapshot(forecasts=[forecast(code=73)])
    issues = find(validate(live_ctx(trip, geo_places, drive_times, snow)), "R23")
    assert any("下雪" in i.message and i.item_ids == ["d2_drive_coronet"] for i in issues)


# ---------------- R24 公众假日 ----------------

def test_holidays_between_nz():
    hs = holidays_between("NZ", date(2026, 11, 28), date(2026, 12, 26))
    xmas = next(h for h in hs if h.name == "Christmas Day")
    assert xmas.shops_closed and xmas.region is None
    west = next(h for h in hs if h.region == "WTC")
    assert west.date == date(2026, 11, 30) and west.region_name == "West Coast"
    assert holidays_between("XX", date(2026, 1, 1), date(2026, 1, 2)) == []


def test_r24_regional_holiday_only_on_that_island(trip, places, drive_times):
    # 11/30 是 West Coast 地区假日；行程这天在北岛，不提示
    assert find(validate(ValidationContext(trip, places, drive_times)), "R24") == []
    south = {pid: p.model_copy(update={"region": "南岛"}) for pid, p in places.items()}
    ctx = ValidationContext(trip, south, drive_times)
    ctx.places = {**ctx.places, **south}
    [issue] = find(validate(ctx), "R24")
    assert "11/30" in issue.message and "West Coast" in issue.message


def test_r24_national_holiday(trip, places, drive_times):
    data = trip.model_dump()
    data["meta"]["start_date"], data["meta"]["end_date"] = date(2026, 12, 24), date(2026, 12, 26)
    data["days"] = [{"date": date(2026, 12, d), "items": []} for d in (24, 25, 26)]
    data["segments"], data["accommodations"], data["todos"] = [], [], []
    xmas = Trip.model_validate(data)
    msgs = [i.message for i in find(validate(ValidationContext(xmas, places, drive_times)), "R24")]
    assert any(m.startswith("12/25 是公众假日 Christmas Day：大部分商店和超市不营业") for m in msgs)
    assert any(m.startswith("12/26 是公众假日 Boxing Day") for m in msgs)


# ---------------- LiveService ----------------

class Fetchers:
    def __init__(self, fail: set[str] = frozenset()):
        self.calls: list[str] = []
        self.fail = fail

    def _maybe_fail(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise TimeoutError("offline")

    def roads(self):
        self._maybe_fail("roads")
        return [event(), event(id="nzta_old", start=nz("2026-11-01", "06:00"), end=nz("2026-11-02", "06:00"))]

    def alerts(self):
        self._maybe_fail("alerts")
        return []

    def forecast(self, points, start, end, tz):
        self._maybe_fail("forecast")
        return [DayForecast(lat=p[0], lon=p[1], date=start + timedelta(days=k), code=0)
                for p in points for k in range((end - start).days + 1)]

    def as_dict(self):
        return {"roads": self.roads, "alerts": self.alerts, "forecast": self.forecast}


def live(today: date, fetchers: Fetchers) -> LiveService:
    return LiveService(today=lambda tz: today, fetchers=fetchers.as_dict())


def test_far_trip_does_not_go_online(trip, geo_places):
    f = Fetchers()
    snap = live(date(2026, 10, 23), f).snapshot(trip, geo_places[0])
    assert f.calls == [] and snap.road_events == [] and snap.forecasts == []
    assert any("NZTA" in n for n in snap.notes) and any("11/08 以后" in n for n in snap.notes)


def test_near_trip_fetches_filters_and_caches(trip, geo_places):
    f = Fetchers()
    service = live(date(2026, 11, 20), f)
    snap = service.snapshot(trip, geo_places[0])
    assert [e.id for e in snap.road_events] == ["nzta_1"]          # 行程开始前就结束的事件不要
    assert set(f.calls) == {"roads", "alerts", "forecast"}
    assert snap.forecasts and all(f_.place_ids for f_ in snap.forecasts)
    assert {f_.date for f_ in snap.forecasts} <= {date(2026, 11, 23) + timedelta(days=k) for k in range(10)}
    assert "roads" in snap.fetched_at
    service.snapshot(trip, geo_places[0])
    assert len(f.calls) == 3                                        # 第二次全部走缓存


def test_failed_source_is_reported_and_not_retried_immediately(trip, geo_places):
    f = Fetchers(fail={"roads"})
    service = live(date(2026, 11, 20), f)
    snap = service.snapshot(trip, geo_places[0])
    assert any("NZTA 路况暂时查不到" in e for e in snap.errors) and snap.road_events == []
    service.snapshot(trip, geo_places[0])
    assert f.calls.count("roads") == 1


def test_finished_trip_is_empty(trip, geo_places):
    f = Fetchers()
    assert live(date(2027, 1, 1), f).snapshot(trip, geo_places[0]) == LiveSnapshot() and f.calls == []


def test_service_validation_includes_live(store, trip, geo_places, drive_times):
    from tripagent.service import TripService
    places, coords = geo_places
    f = Fetchers()
    service = TripService(store, places, RouteStub(drive_times, coords), live=live(date(2026, 11, 20), f))
    service.create_trip(trip)
    assert find(service.validate(trip.meta.id), "R21")


def test_r21_open_ended_incident_only_for_next_two_days(trip, geo_places, drive_times):
    crash = event(kind="Road Hazard", planned=False, end=None, start=nz("2026-11-20", "02:00"))
    far = LiveSnapshot(road_events=[crash], fetched_at={"roads": nz("2026-11-20", "08:00")})
    assert find(validate(live_ctx(trip, geo_places, drive_times, far)), "R21") == []      # 4 天后的行程不报
    near = LiveSnapshot(road_events=[crash], fetched_at={"roads": nz("2026-11-23", "08:00")})
    assert len(find(validate(live_ctx(trip, geo_places, drive_times, near)), "R21")) == 1
    planned = LiveSnapshot(road_events=[event(end=None, planned=True)], fetched_at={"roads": nz("2026-11-01", "08:00")})
    assert len(find(validate(live_ctx(trip, geo_places, drive_times, planned)), "R21")) == 1  # 计划内的照常报


def test_r21_skips_route_lookup_when_no_event_nearby(trip, geo_places, drive_times):
    asked = []
    places, coords = geo_places

    class Counting(RouteStub):
        def geometry(self, a, b):
            asked.append((a, b))
            return super().geometry(a, b)

    ctx = ValidationContext(trip, places, Counting(drive_times, coords),
                            live=LiveSnapshot(road_events=[event(lines=[[(-36.9, 174.8), (-36.8, 174.8)]])]))  # 奥克兰
    ctx.places = places
    assert find(validate(ctx), "R21") == [] and asked == []


def test_r23_skips_short_stops(trip, geo_places, drive_times):
    skytrek = trip.find_item("d2_skytrek")
    skytrek.end = skytrek.start + timedelta(minutes=20)
    snap = LiveSnapshot(forecasts=[forecast(precip_mm=30, precip_prob=95)])
    assert find(validate(live_ctx(trip, geo_places, drive_times, snap)), "R23") == []
