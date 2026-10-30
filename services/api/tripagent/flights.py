"""Kiwi.com 机票搜索结果的整理，对话工具和生成行程（S6）共用。"""

from datetime import datetime


def _hm(seconds: int | None) -> str:
    if not seconds:
        return ""
    h, m = divmod(seconds // 60, 60)
    return f"{h}小时{m:02d}分" if h else f"{m}分钟"


def _local(value: str | None) -> str:
    if not value:
        return ""
    dt = datetime.fromisoformat(value)
    return f"{dt.month}/{dt.day} {dt:%H:%M}"


def flight_rows(data: dict, limit: int = 6) -> list[dict]:
    """把 Kiwi 的搜索结果压缩成几行，给模型和网页用。时间是各机场当地时间，价格是全部乘客的总价。"""
    rows = []
    for it in (data.get("itineraries") or [])[:limit]:
        out = it.get("outbound") or {}
        segs = out.get("segments") or []
        rows.append({
            "航班": " + ".join(f"{s.get('flightNumber', '')} {s.get('carrierName', '')}".strip() for s in segs),
            "出发": f"{out.get('from', '')} {_local(out.get('departureTime'))}",
            "到达": f"{out.get('to', '')} {_local(out.get('arrivalTime'))}",
            "时长": _hm(out.get("durationSeconds") or it.get("totalDurationSeconds")),
            "经停": out.get("stops", max(len(segs) - 1, 0)),
            "经停机场": out.get("route", [])[1:-1],
            "总价": it.get("priceFormatted") or it.get("price"),
            "行李": it.get("baggage"),
            "预订链接": it.get("bookingUrl"),
        })
    return rows
