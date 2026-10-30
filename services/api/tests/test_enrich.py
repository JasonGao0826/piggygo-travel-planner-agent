"""S6 给预订待办补航班候选和住宿链接。用进程内的假 Kiwi 服务，不连外网。"""

from mcp.server import MCPServer

from tripagent.mcp_gateway import MCPGateway, ServerConfig
from tripagent.planner.enrich import enrich_bookings, iata
from tripagent.schema.trip import BookingStatus, Todo

from .conftest import rebuild
from .test_external_tools import KIWI_RESULT


def kiwi_gateway(calls, result=KIWI_RESULT):
    kiwi = MCPServer("kiwi")

    @kiwi.tool(name="search-flight", description="Search flights")
    def search_flight(flyFrom: str, flyTo: str, departureDate: str, adults: int = 1, currency: str = "EUR",
                      sort: str = "price", locale: str = "en", dtime_from: int | None = None,
                      dtime_to: int | None = None) -> dict:
        calls.append((flyFrom, flyTo, departureDate, adults, currency, dtime_from, dtime_to))
        return result

    return MCPGateway([ServerConfig("kiwi", kiwi, ["search-flight"])])


def unbooked(trip):
    """把第一班飞机和皇后镇住宿改成"还没订"，并配上预订待办。"""
    flight = trip.find_item("d1_flight_qf177")
    flight.booking_status, flight.locked, flight.lock_reason = BookingStatus.TO_BOOK, False, None
    acc = trip.accommodations[0]
    acc.booking_status, acc.locked, acc.lock_reason = BookingStatus.TO_BOOK, False, None
    trip.todos += [Todo(id="todo_book_flight", title="预订：QF177", kind="booking", item_ids=[flight.id]),
                   Todo(id="todo_book_acc", title="预订皇后镇住宿", kind="booking", item_ids=[acc.id])]
    return rebuild(trip)


def test_iata():
    assert iata("au_mel_airport") == "MEL" and iata("nz_zqn_airport") == "ZQN" and iata("nz_queenstown_town") is None


def test_flight_options_and_stay_links(trip, places):
    calls = []
    t = unbooked(trip)
    places = {**places, **{p.id: p for p in t.custom_places}}
    notes = enrich_bookings(t, places, kiwi_gateway(calls))
    assert notes == []
    # 06:40 墨尔本起飞：按墨尔本当地时间查前后 3 小时，4 人，结算币种
    assert calls == [("MEL", "ZQN", "23/11/2026", 4, "AUD", 3, 9)]
    todos = {x.id: x for x in t.todos}
    flight_notes = todos["todo_book_flight"].notes
    assert flight_notes[0].startswith("Kiwi.com ") and "4 人总价" in flight_notes[0]
    assert "JQ219 Jetstar Airways｜MEL 11/23 14:50 → ZQN 11/23 20:00｜3小时10分｜经停 0｜1621 AUD｜https://kiwi.com/u/abc" in flight_notes
    assert any(n.startswith("Google Flights：https://www.google.com/travel/flights?") for n in flight_notes)
    acc_notes = todos["todo_book_acc"].notes
    assert any(n.startswith("Airbnb 搜索：https://www.airbnb.com/s/") for n in acc_notes)
    assert any("booking.com" in n and "group_adults=4" in n and "no_rooms=2" in n for n in acc_notes)
    # 已经订好的不动
    assert all("Kiwi" not in n for x in t.todos if x.id not in ("todo_book_flight", "todo_book_acc") for n in x.notes)


def test_no_results_and_no_gateway(trip, places):
    calls = []
    t = unbooked(trip.model_copy(deep=True))
    notes = enrich_bookings(t, places, kiwi_gateway(calls, {**KIWI_RESULT, "itineraries": []}))
    assert notes == ["预订：QF177：Kiwi.com 在计划时间前后没有找到航班，可能要换日期或时间"]
    t2 = unbooked(trip.model_copy(deep=True))
    assert enrich_bookings(t2, places, None) == []
    assert any(n.startswith("Google Flights") for n in next(x for x in t2.todos if x.id == "todo_book_flight").notes)
