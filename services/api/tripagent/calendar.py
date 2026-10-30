"""把行程导出成日历文件（.ics），导入手机日历或 Google 日历后，每个安排都有提醒。

只导出要按时间到场的事件（航班、开车、项目、用餐、入住退房、取还车），自由活动和缓冲不导出。
时间统一写成 UTC，日历软件会按手机所在时区显示。
"""

from datetime import datetime, timedelta, timezone

from .schema.place import Place
from .schema.trip import ItemType, Trip

SKIP = {ItemType.FREE_TIME, ItemType.BUFFER}
REMIND_MIN = {ItemType.FLIGHT: 60, ItemType.ACTIVITY: 60, ItemType.CAR_PICKUP: 30, ItemType.CHECKOUT: 30}  # 在需要到场的时间之前再提前多久提醒


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: str) -> str:
    # 单独的回车也算换行（有的日历程序按回车断行，会被当成新的一行属性）；其他控制字符去掉
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(ch for ch in value if ch >= " " or ch == "\n")
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """iCalendar 每行最多 75 个字节，超出的折到下一行（以空格开头）。"""
    out, cur = [], ""
    for ch in line:
        limit = 75 if not out else 74
        if len((cur + ch).encode("utf-8")) > limit:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return [out[0], *(" " + x for x in out[1:])]


def trip_ics(trip: Trip, places: dict[str, Place], *, now: datetime | None = None) -> str:
    places = {**places, **{p.id: p for p in trip.custom_places}}
    stamp = _utc(now or datetime.now(timezone.utc))

    def where(pid: str | None) -> str:
        p = places.get(pid or "")
        if p is None:
            return ""
        return f"{p.name_zh} / {p.name_en}" + (f", {p.address}" if p.address else "")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tripagent//travel planner//ZH", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_text(trip.meta.title)}", f"X-WR-TIMEZONE:{_text(trip.meta.local_timezone)}"]
    for item in trip.all_items():
        if item.type in SKIP:
            continue
        start = item.start
        loc = where(item.place_id) or where(item.from_place_id)
        desc = []
        if item.checkin_before_min:
            desc.append(f"需要提前 {item.checkin_before_min} 分钟到场")
        if item.from_place_id and item.to_place_id:
            desc.append(f"{where(item.from_place_id)} → {where(item.to_place_id)}")
        if item.booking_status.value == "to_book":
            desc.append("还没预订")
        desc += item.notes
        if item.backup:
            desc.append(f"备用安排：{item.backup}")
        lines += ["BEGIN:VEVENT", f"UID:{item.id}@{trip.meta.id}.tripagent", f"DTSTAMP:{stamp}",
                  f"DTSTART:{_utc(start)}", f"DTEND:{_utc(max(item.end, start + timedelta(minutes=5)))}",
                  f"SUMMARY:{_text(item.title)}"]
        if loc:
            lines.append(f"LOCATION:{_text(loc)}")
        if desc:
            lines.append(f"DESCRIPTION:{_text(chr(10).join(desc))}")
        if (remind := REMIND_MIN.get(item.type)) is not None:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_text(item.title)}",
                      f"TRIGGER:-PT{remind + item.checkin_before_min}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(folded for line in lines for folded in _fold(line)) + "\r\n"
