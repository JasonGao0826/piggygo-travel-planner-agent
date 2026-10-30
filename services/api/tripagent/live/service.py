"""为一个行程抓取实时数据，组装成 LiveSnapshot。

只在有意义的时候联网：行程还远时路况和预警都还没发布，就不去查，校验保持离线、快速。
- 路况：NZTA 只发布当前和未来约一周的事件，行程开始前 10 天内才查；
- 天气预警：预警一般提前一两天发布，行程开始前 7 天内才查；
- 天气预报：只有未来 16 天，只查落在这个范围里的日子。
抓取失败记在 errors 里，不影响其他部分；失败后 2 分钟内不重试，避免每次校验都卡住。
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from ..cache import Cache, MemoryCache
from ..schema.place import Place
from ..schema.trip import ItemType, Trip
from .models import DayForecast, LatLon, LiveSnapshot, RoadEvent, WeatherAlert
from .sources import FORECAST_DAYS, fetch_metservice, fetch_nzta, fetch_open_meteo

ROAD_DAYS, ALERT_DAYS = 10, 7
TTL = {"roads": 600, "alerts": 600, "forecast": 3600}
FAIL_TTL = 120
LABELS = {"roads": "NZTA 路况", "alerts": "MetService 天气预警", "forecast": "Open-Meteo 天气预报"}


class LiveService:
    def __init__(self, cache: Cache | None = None, *, today: Callable[[ZoneInfo], date] | None = None,
                 fetchers: dict[str, Callable] | None = None):
        self.cache = cache or MemoryCache()
        self.today = today or (lambda tz: datetime.now(tz).date())
        self.fetchers = {"roads": fetch_nzta, "alerts": fetch_metservice, "forecast": fetch_open_meteo,
                         **(fetchers or {})}

    def _get(self, snap: LiveSnapshot, source: str, key: str, fetch: Callable[[], list]) -> list[dict] | None:
        hit = self.cache.get(key)
        if hit is not None:
            if isinstance(hit.value, dict) and "error" in hit.value:
                snap.errors.append(f"{LABELS[source]}暂时查不到（{hit.value['error']}），稍后会自动重试")
                return None
            snap.fetched_at[source] = hit.fetched_at
            return hit.value
        try:
            rows = [r.model_dump(mode="json") for r in fetch()]
        except Exception as exc:
            self.cache.set(key, {"error": type(exc).__name__}, FAIL_TTL)
            snap.errors.append(f"{LABELS[source]}暂时查不到（{type(exc).__name__}），稍后会自动重试")
            return None
        stored = self.cache.set(key, rows, TTL[source])
        snap.fetched_at[source] = stored.fetched_at
        return rows

    def snapshot(self, trip: Trip, places: dict[str, Place]) -> LiveSnapshot:
        meta = trip.meta
        tz = ZoneInfo(meta.local_timezone)
        today = self.today(tz)
        snap = LiveSnapshot()
        if meta.end_date < today:
            return snap
        start = datetime.combine(meta.start_date, datetime.min.time(), tz)
        end = datetime.combine(meta.end_date + timedelta(days=1), datetime.min.time(), tz)
        days_out = (meta.start_date - today).days

        if meta.destination == "NZ":
            if days_out <= ROAD_DAYS:
                rows = self._get(snap, "roads", "live:nzta", self.fetchers["roads"])
                events = [RoadEvent.model_validate(r) for r in rows or []]
                snap.road_events = [e for e in events if e.active_during(start, end)]
            else:
                snap.notes.append(f"路况：NZTA 只发布未来约一周的施工和封路，出发前 {ROAD_DAYS} 天内开始自动检查")
            if days_out <= ALERT_DAYS:
                rows = self._get(snap, "alerts", "live:metservice", self.fetchers["alerts"])
                alerts = [WeatherAlert.model_validate(r) for r in rows or []]
                snap.weather_alerts = [a for a in alerts if a.active_during(start, end)]
            else:
                snap.notes.append(f"天气预警：出发前 {ALERT_DAYS} 天内开始自动检查 MetService 预警")

        last = today + timedelta(days=FORECAST_DAYS - 1)
        f_start, f_end = max(meta.start_date, today), min(meta.end_date, last)
        if f_start > f_end:
            snap.notes.append(f"天气预报：只有未来 {FORECAST_DAYS} 天的预报，"
                              f"{(meta.start_date - timedelta(days=FORECAST_DAYS - 1)).strftime('%m/%d')} 以后可以看到第一天的预报")
            return snap
        if meta.end_date > last:
            snap.notes.append(f"天气预报：目前能看到 {f_end.month}/{f_end.day} 及以前的预报，之后的日子稍后再看")

        uses = _forecast_points(trip, places, tz, f_start, f_end)
        if uses:
            points = sorted(uses)
            key = "live:fc:" + hashlib.sha256(json.dumps(
                [points, f_start.isoformat(), f_end.isoformat(), meta.local_timezone]).encode()).hexdigest()
            rows = self._get(snap, "forecast", key,
                             lambda: self.fetchers["forecast"](points, f_start, f_end, meta.local_timezone))
            for r in rows or []:
                f = DayForecast.model_validate(r)
                if ids := uses.get((f.lat, f.lon), {}).get(f.date):
                    snap.forecasts.append(f.model_copy(update={"place_ids": sorted(ids)}))
        return snap


def _forecast_points(trip: Trip, places: dict[str, Place], tz: ZoneInfo, start: date,
                     end: date) -> dict[LatLon, dict[date, set[str]]]:
    """预报要查的点：每天用到的地点，坐标取到 0.05 度（约 5 公里）合并。返回 {点: {日期: 地点 id}}。"""
    uses: dict[LatLon, dict[date, set[str]]] = defaultdict(lambda: defaultdict(set))
    all_places = {**places, **{p.id: p for p in trip.custom_places}}
    for item in trip.all_items():
        day = item.start.astimezone(tz).date()
        if item.type == ItemType.FLIGHT or not start <= day <= end:
            continue
        for pid in (item.place_id, item.from_place_id, item.to_place_id):
            p = all_places.get(pid or "")
            if p is None or p.lat is None or p.lon is None:
                continue
            point = (round(round(p.lat / 0.05) * 0.05, 2), round(round(p.lon / 0.05) * 0.05, 2))
            uses[point][day].add(p.id)
    return uses
