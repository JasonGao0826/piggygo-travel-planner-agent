"""行程数据的读写。

YAML 里的事件时间可以只写 "HH:MM"，读取时按当天日期和目的地时区补全（会正确处理夏令时）；
跨时区的事件（例如墨尔本出发的航班）写完整的 ISO 时间。
"""

import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .schema.memory import Decision
from .schema.trip import Trip

_HHMM = re.compile(r"^\d{2}:\d{2}$")


def _expand_time(value, day: date, tz: ZoneInfo):
    if isinstance(value, str) and _HHMM.match(value):
        hh, mm = map(int, value.split(":"))
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz).isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def trip_from_dict(data: dict) -> Trip:
    tz = ZoneInfo(data["meta"].get("local_timezone", "Pacific/Auckland"))
    for day in data.get("days", []):
        d = day["date"] if isinstance(day["date"], date) else date.fromisoformat(day["date"])
        for item in day.get("items", []):
            for key in ("start", "end"):
                item[key] = _expand_time(item[key], d, tz)
    return Trip.model_validate(data)


def load_trip(path: str | Path) -> Trip:
    return trip_from_dict(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def load_decisions(path: str | Path) -> list[Decision]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [Decision.model_validate(d) for d in data.get("decisions", [])]
