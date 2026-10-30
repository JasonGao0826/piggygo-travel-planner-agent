"""实时数据来源。都是不需要 key 的公开接口：

- 新西兰交通局（NZTA）路况：封路、施工、事故，覆盖当前和未来约一周；
- MetService 天气预警（CAP 标准格式），CC BY 4.0 授权；
- Open-Meteo 天气预报，未来 16 天。免费版只能非商业使用，正式上线设置 OPEN_METEO_API_KEY 换成付费接口。

这里只负责抓取和解析，比对行程在校验器的 R21–R23 里做。
"""

import os
import xml.etree.ElementTree as ET
from datetime import date, datetime

import httpx

from .geo import parse_wkt
from .models import DayForecast, LatLon, RoadEvent, WeatherAlert

USER_AGENT = "tripagent/0.1 (travel planning beta)"
NZTA_URL = "https://trafficnz.info/service/traffic/rest/4/events/all/10"
METSERVICE_FEED = "https://alerts.metservice.com/cap/rss"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PAID_URL = "https://customer-api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16


def _get(url: str, *, timeout: float = 30, **kw) -> httpx.Response:
    r = httpx.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
                  follow_redirects=True, **kw)
    r.raise_for_status()
    return r


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else None


# ---------------- NZTA 路况 ----------------

def parse_nzta(data: dict) -> list[RoadEvent]:
    rows = (data.get("response") or {}).get("roadevent") or []
    if isinstance(rows, dict):
        rows = [rows]
    events = []
    for e in rows:
        events.append(RoadEvent(
            id=f"nzta_{e.get('id')}",
            kind=e.get("eventType") or e.get("eventDescription") or "",
            impact=e.get("impact") or "",
            title=e.get("locationArea") or "",
            detail=(e.get("eventComments") or "").strip(),
            region=(e.get("region") or {}).get("name"),
            start=_dt(e.get("startDate")),
            end=_dt(e.get("endDate")),
            planned=bool(e.get("planned")),
            lines=parse_wkt(e.get("geometry")),
        ))
    return events


def fetch_nzta() -> list[RoadEvent]:
    return parse_nzta(_get(NZTA_URL, timeout=60).json())


# ---------------- MetService 天气预警 ----------------

_CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"


def feed_links(xml_text: str) -> list[str]:
    """从 RSS 或 Atom 订阅里取出每条预警的 CAP 文件地址。"""
    root = ET.fromstring(xml_text)
    links = [item.findtext("link").strip() for item in root.iter("item") if item.findtext("link")]
    atom = "{http://www.w3.org/2005/Atom}"
    links += [link.get("href") for entry in root.iter(f"{atom}entry")
              for link in entry.findall(f"{atom}link") if link.get("href")]
    return list(dict.fromkeys(links))


def _polygon(text: str) -> list[LatLon]:
    pts = []
    for pair in text.split():
        lat, _, lon = pair.partition(",")
        try:
            pts.append((float(lat), float(lon)))
        except ValueError:
            continue
    return pts


def parse_cap(xml_text: str, url: str | None = None) -> list[WeatherAlert]:
    root = ET.fromstring(xml_text)
    ident = root.findtext(f"{_CAP}identifier") or url or ""
    if (root.findtext(f"{_CAP}msgType") or "").lower() == "cancel":
        return []
    alerts = []
    infos = root.findall(f"{_CAP}info")
    english = [i for i in infos if (i.findtext(f"{_CAP}language") or "en").lower().startswith("en")] or infos
    for n, info in enumerate(english):
        areas = info.findall(f"{_CAP}area")
        alerts.append(WeatherAlert(
            id=f"{ident}#{n}" if len(english) > 1 else ident,
            event=info.findtext(f"{_CAP}event") or "",
            severity=info.findtext(f"{_CAP}severity") or "",
            headline=info.findtext(f"{_CAP}headline") or "",
            description=(info.findtext(f"{_CAP}description") or "").strip(),
            areas=[a.findtext(f"{_CAP}areaDesc") or "" for a in areas],
            polygons=[_polygon(p.text or "") for a in areas for p in a.findall(f"{_CAP}polygon")],
            onset=_dt(info.findtext(f"{_CAP}onset") or info.findtext(f"{_CAP}effective")),
            expires=_dt(info.findtext(f"{_CAP}expires")),
            url=info.findtext(f"{_CAP}web") or url,
        ))
    return alerts


def fetch_metservice() -> list[WeatherAlert]:
    alerts = []
    for link in feed_links(_get(METSERVICE_FEED).text):
        alerts.extend(parse_cap(_get(link).text, link))
    return alerts


# ---------------- Open-Meteo 天气预报 ----------------

def parse_open_meteo(data: dict | list, points: list[LatLon]) -> list[DayForecast]:
    rows = data if isinstance(data, list) else [data]
    out = []
    for (lat, lon), row in zip(points, rows):
        daily = row.get("daily") or {}
        for i, day in enumerate(daily.get("time", [])):
            def pick(name):
                values = daily.get(name) or []
                return values[i] if i < len(values) else None
            out.append(DayForecast(
                lat=lat, lon=lon, date=date.fromisoformat(day), code=pick("weather_code"),
                tmax=pick("temperature_2m_max"), tmin=pick("temperature_2m_min"),
                precip_mm=pick("precipitation_sum"), precip_prob=pick("precipitation_probability_max"),
                gust_kmh=pick("wind_gusts_10m_max"),
            ))
    return out


def fetch_open_meteo(points: list[LatLon], start: date, end: date, timezone: str) -> list[DayForecast]:
    if not points:
        return []
    params = {
        "latitude": ",".join(f"{p[0]:.3f}" for p in points),
        "longitude": ",".join(f"{p[1]:.3f}" for p in points),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_gusts_10m_max",
        "timezone": timezone, "start_date": start.isoformat(), "end_date": end.isoformat(),
    }
    url = OPEN_METEO_URL
    if key := os.getenv("OPEN_METEO_API_KEY"):
        url, params["apikey"] = OPEN_METEO_PAID_URL, key
    return parse_open_meteo(_get(url, params=params).json(), points)


# WMO 天气代码 → 中文
WEATHER_TEXT = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "较强阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷雨", 96: "雷雨伴冰雹", 99: "强雷雨伴冰雹",
}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
STORM_CODES = {95, 96, 99}
