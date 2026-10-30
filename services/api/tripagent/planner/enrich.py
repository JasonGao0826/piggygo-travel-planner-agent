"""S6：给预订待办补上真实候选和搜索链接（设计规格第 22 节）。

- 还没订的航班：用 Kiwi.com 查当天、计划时间前后 3 小时内的真实航班，列出最便宜的几个和预订链接；
- 还没订的住宿：附上 Airbnb、Booking.com 的搜索链接（城市、日期、人数都带好）。
只写进待办的备注，不改行程本身：选哪班、订哪家由用户决定。查不到时照常生成，只在说明里提一句。
"""

import math
import re
from zoneinfo import ZoneInfo

from ..flights import flight_rows
from ..links import flight_links, stay_links
from ..mcp_gateway import GatewayError, MCPGateway
from ..schema.place import Place
from ..schema.trip import BookingStatus, ItemType, Todo, Trip

AIRPORT_ID = re.compile(r"^[a-z]{2}_([a-z]{3})_airport$")
WINDOW_H = 3
OPTIONS = 3


def iata(place_id: str | None) -> str | None:
    m = AIRPORT_ID.match(place_id or "")
    return m.group(1).upper() if m else None


def enrich_bookings(trip: Trip, places: dict[str, Place], gateway: MCPGateway | None, *,
                    whole_home: bool = False) -> list[str]:
    """就地更新 trip.todos 的备注，返回给用户看的说明。"""
    notes: list[str] = []
    adults = len(trip.members)
    todos = {i: t for t in trip.todos for i in t.item_ids if t.kind == "booking"}

    def tz_of(place_id: str) -> ZoneInfo:  # 和排程一样：澳洲的机场用出发城市时区，其余用目的地时区
        p = places.get(place_id)
        return ZoneInfo(trip.meta.home_timezone if p and p.region == "澳洲" else trip.meta.local_timezone)

    for item in trip.all_items():
        if item.type != ItemType.FLIGHT or item.booking_status != BookingStatus.TO_BOOK or item.id not in todos:
            continue
        src, dst = iata(item.from_place_id), iata(item.to_place_id)
        if not (src and dst):
            continue
        todo = todos[item.id]
        local = item.start.astimezone(tz_of(item.from_place_id))  # Kiwi 要起飞机场当地的日期和小时
        day = local.date()
        extra = [f"{name}：{url}" for name, url in flight_links(src, dst, day, adults).items()]
        if gateway and gateway.available("kiwi"):
            hour = local.hour
            params = {"flyFrom": src, "flyTo": dst, "departureDate": day.strftime("%d/%m/%Y"), "adults": adults,
                      "currency": trip.meta.settle_currency, "sort": "price", "locale": "en",
                      "dtime_from": max(0, hour - WINDOW_H), "dtime_to": min(23, hour + WINDOW_H)}
            try:
                r = gateway.call("kiwi", "search-flight", params)
                rows = flight_rows(r.data, OPTIONS)
            except GatewayError:
                rows = None
                notes.append(f"{todo.title}：Kiwi.com 暂时查不到航班，先附上搜索链接")
            if rows:
                stamp = r.fetched_at.astimezone(ZoneInfo(trip.meta.home_timezone))
                extra.insert(0, f"Kiwi.com {stamp.month}/{stamp.day} {stamp:%H:%M} 查到（{adults} 人总价，价格会变）：")
                extra[1:1] = [f"{x['航班']}｜{x['出发']} → {x['到达']}｜{x['时长']}｜经停 {x['经停']}｜{x['总价']}｜{x['预订链接']}"
                              for x in rows]
            elif rows is not None:
                notes.append(f"{todo.title}：Kiwi.com 在计划时间前后没有找到航班，可能要换日期或时间")
        _add_notes(todo, extra)

    rooms = 1 if whole_home else max(1, math.ceil(adults / 2))
    country = trip.meta.destination
    for acc in trip.accommodations:
        if acc.booking_status != BookingStatus.TO_BOOK or acc.id not in todos:
            continue
        place = places.get(acc.place_id)
        city = place.city if place and place.city else acc.city  # 搜索网站认英文城市名，知识库地点里有
        links = stay_links(city, country, acc.check_in, acc.check_out, adults, rooms)
        _add_notes(todos[acc.id], [f"{name} 搜索：{url}" for name, url in links.items()])
    return notes


def _add_notes(todo: Todo, extra: list[str]) -> None:
    todo.notes = [*todo.notes, *[x for x in extra if x not in todo.notes]]
