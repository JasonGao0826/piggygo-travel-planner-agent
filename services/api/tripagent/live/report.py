"""把实时数据整理成网页和对话工具用的格式：和行程有关的提醒、每天的天气预报、数据时间。"""

from collections import defaultdict

from ..live.models import DayForecast
from ..live.sources import WEATHER_TEXT
from ..schema.memory import Issue
from ..service import TripService

LIVE_CODES = {"R21", "R22", "R23", "R24"}


def live_issues(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.code in LIVE_CODES]


def _max(values):
    values = [v for v in values if v is not None]
    return max(values) if values else None


def daily_summary(forecasts: list[DayForecast], names: dict[str, str]) -> list[dict]:
    """每天一行：当天用到的地点里最差的天气（WMO 代码越大越恶劣）、最低和最高气温、最大降雨和阵风。"""
    by_day: dict = defaultdict(list)
    for f in forecasts:
        by_day[f.date].append(f)
    rows = []
    for day, fs in sorted(by_day.items()):
        code = _max(f.code for f in fs)
        places = list(dict.fromkeys(names.get(p, p) for f in fs for p in f.place_ids))
        mins = [f.tmin for f in fs if f.tmin is not None]
        rows.append({"date": day.isoformat(), "places": places, "code": code,
                     "weather": WEATHER_TEXT.get(code, "未知") if code is not None else "未知",
                     "tmin": min(mins) if mins else None, "tmax": _max(f.tmax for f in fs),
                     "precip_mm": _max(f.precip_mm for f in fs), "precip_prob": _max(f.precip_prob for f in fs),
                     "gust_kmh": _max(f.gust_kmh for f in fs)})
    return rows


def live_report(service: TripService, trip_id: str) -> dict:
    trip = service.current(trip_id)
    snap = service.live_snapshot(trip)
    issues = live_issues(service.validate(trip_id))
    out = {"enabled": snap is not None, "issues": [i.model_dump(mode="json") for i in issues],
           "days": [], "forecasts": [], "notes": [], "errors": [], "fetched_at": {}}
    if snap is None:
        return out
    names = {pid: p.name_zh for pid, p in service.places_for(trip).items()}
    out["forecasts"] = [{
        "date": f.date.isoformat(), "places": [names.get(p, p) for p in f.place_ids],
        "weather": WEATHER_TEXT.get(f.code, "未知") if f.code is not None else "未知", "code": f.code,
        "tmin": f.tmin, "tmax": f.tmax, "precip_mm": f.precip_mm, "precip_prob": f.precip_prob, "gust_kmh": f.gust_kmh,
    } for f in sorted(snap.forecasts, key=lambda f: (f.date, f.place_ids))]
    out["days"] = daily_summary(snap.forecasts, names)
    out.update(notes=snap.notes, errors=snap.errors,
               fetched_at={k: v.isoformat(timespec="minutes") for k, v in snap.fetched_at.items()})
    return out
