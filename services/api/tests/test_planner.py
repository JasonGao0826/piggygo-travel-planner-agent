"""首次生成行程：用剧本模型给出路线框架和每天安排，检查代码排出的时间线能通过校验。"""

import json

import pytest

from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.planner import NeedsInput, Planner
from tripagent.schema.intake import TripIntake
from tripagent.schema.trip import ItemType

from datetime import date

INTAKE = {
    "destination": "NZ", "regions": ["南岛"], "start_date": "2026-11-23", "end_date": "2026-11-25",
    "origin_city": "Melbourne", "transport": "self_drive", "room_requirements": "两间卧室",
    "existing_bookings": [{"kind": "flight", "description": "QF177 墨尔本 → 皇后镇 11/23 06:40–11:45"}],
    "members": [
        {"name": "Amy", "passport_country": "CN", "visa": "澳洲 500 学生签", "can_drive": True, "age": 30,
         "licence_country": "AU", "licence_type": "full",
         "must_do": [{"what": "鲨鱼艇", "activity_tag": "shark_boat"}]},
        {"name": "Ben", "passport_country": "CN", "visa": "澳洲 500 学生签", "can_drive": True, "age": 27,
         "licence_country": "CN", "licence_type": "full"},
    ],
}

SKELETON = {
    "summary": "皇后镇进出，三天都住皇后镇。",
    "segments": [{"name": "南岛自驾", "transport": "self_drive", "start_date": "2026-11-23", "end_date": "2026-11-25"}],
    "flights": [
        {"number": "QF177", "from_airport": "au_mel_airport", "to_airport": "nz_zqn_airport", "date": "2026-11-23",
         "depart": "06:40", "arrive": "11:45", "booked": True, "international": True},
        {"number": None, "from_airport": "nz_zqn_airport", "to_airport": "au_mel_airport", "date": "2026-11-25",
         "depart": "15:00", "arrive": "16:30", "booked": False, "international": True},
    ],
    "days": [
        {"date": "2026-11-23", "night_place_id": "nz_queenstown_town_centre", "night_city": "皇后镇",
         "route": "墨尔本 ✈ 皇后镇", "focus": "抵达、缆车", "segment": "南岛自驾"},
        {"date": "2026-11-24", "night_place_id": "nz_queenstown_town_centre", "night_city": "皇后镇",
         "route": "皇后镇市区 → 小鹿公园", "focus": "鲨鱼艇、小鹿公园", "segment": "南岛自驾"},
        {"date": "2026-11-25", "night_place_id": None, "night_city": None,
         "route": "皇后镇 ✈ 墨尔本", "focus": "返程", "segment": "南岛自驾"},
    ],
    "assumptions": ["回程航班按下午 3 点的常见班次估算"], "questions": [],
}

PLANS = {
    "days": [
        {"date": "2026-11-23", "depart": None, "stops": [
            {"place_id": "nz_queenstown_town_centre", "kind": "meal", "title": "午餐", "duration_min": 60, "booking": "not_required"},
            {"place_id": "nz_skyline_queenstown", "kind": "activity", "title": "Skyline 缆车", "start": "16:30",
             "duration_min": 120, "booking": "to_book"},
            {"place_id": "nz_queenstown_town_centre", "kind": "meal", "title": "晚餐", "duration_min": 60, "booking": "not_required"},
        ]},
        {"date": "2026-11-24", "depart": "09:00", "stops": [
            {"place_id": "nz_hydro_attack", "kind": "activity", "title": "鲨鱼艇", "start": "10:10",
             "duration_min": 25, "booking": "to_book"},
            {"place_id": "nz_queenstown_town_centre", "kind": "meal", "title": "午餐", "duration_min": 60, "booking": "not_required"},
            {"place_id": "nz_deer_park_heights", "kind": "activity", "title": "小鹿公园", "start": "14:00",
             "duration_min": 120, "booking": "to_book"},
            {"place_id": "nz_queenstown_town_centre", "kind": "meal", "title": "晚餐", "duration_min": 60, "booking": "not_required"},
        ]},
        {"date": "2026-11-25", "depart": "09:00", "stops": [
            {"place_id": "nz_queenstown_town_centre", "kind": "meal", "title": "早午餐", "duration_min": 60, "booking": "not_required"},
        ]},
    ],
    "assumptions": [],
}


def planner(places, drive_times, steps):
    return Planner(ScriptedLLM(steps), places, drive_times, today=date(2026, 10, 22))


def test_generate_builds_a_valid_trip(places, drive_times):
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(PLANS))]) \
        .generate(TripIntake.model_validate(INTAKE), trip_id="trip_test")
    trip = result.trip
    assert result.errors == [], [i.message for i in result.errors]
    assert [d.date.isoformat() for d in trip.days] == ["2026-11-23", "2026-11-24", "2026-11-25"]

    day1 = [i.type for i in trip.days[0].items]
    assert day1[:3] == [ItemType.FLIGHT, ItemType.BUFFER, ItemType.CAR_PICKUP]  # 落地 → 入境 → 取车
    assert day1[-1] == ItemType.CHECKIN
    last = [i.type for i in trip.days[2].items]
    assert last[0] == ItemType.CHECKOUT and last[-2:] == [ItemType.CAR_DROPOFF, ItemType.FLIGHT]

    assert [a.city for a in trip.accommodations] == ["皇后镇"] and trip.accommodations[0].check_out.day == 25
    assert trip.vehicles[0].pickup_place_id == "nz_zqn_airport" and trip.vehicles[0].drivers == ["m_amy", "m_ben"]
    kinds = {t.kind.value for t in trip.todos}
    assert {"visa", "licence_translation", "booking", "payment"} <= kinds
    assert any("Ben" in t.title for t in trip.todos if t.kind == "licence_translation")  # 中国驾照要翻译件
    assert result.decisions[0].requirement.activity_tag == "shark_boat"
    assert "回程航班按下午 3 点的常见班次估算" in trip.assumptions


def test_conflicts_are_sent_back_for_repair(places, drive_times):
    bad = json.loads(json.dumps(PLANS))
    bad["days"][1]["stops"][2]["start"] = "10:20"  # 小鹿公园和鲨鱼艇撞时间
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(bad)),
                                           ScriptStep(text=json.dumps(PLANS))]) \
        .generate(TripIntake.model_validate(INTAKE), trip_id="trip_test")
    assert result.rounds == 1 and result.errors == []


def test_user_feedback_regenerates_skeleton(places, drive_times):
    seen = []

    def review(sk):
        seen.append(sk.summary)
        return "第二天想去格林诺奇" if len(seen) == 1 else None

    revised = dict(SKELETON, summary="按意见改过的框架")
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(revised)),
                                           ScriptStep(text=json.dumps(PLANS))]) \
        .generate(TripIntake.model_validate(INTAKE), trip_id="trip_test", review_skeleton=review)
    assert seen == ["皇后镇进出，三天都住皇后镇。", "按意见改过的框架"]
    assert result.skeleton.summary == "按意见改过的框架"


def test_missing_dates_asks_user(places, drive_times):
    intake = TripIntake.model_validate({**INTAKE, "start_date": None, "end_date": None})
    with pytest.raises(NeedsInput) as exc:
        planner(places, drive_times, []).generate(intake)
    assert "日期" in exc.value.questions[0]


def test_flight_without_arrival_time_is_estimated(places, drive_times):
    sk = json.loads(json.dumps(SKELETON))
    sk["flights"][0]["arrive"] = ""
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(sk)), ScriptStep(text=json.dumps(PLANS))])         .generate(TripIntake.model_validate(INTAKE), trip_id="trip_test")
    flight = result.trip.days[0].items[0]
    assert (flight.end - flight.start).total_seconds() == 3 * 3600
    assert any("没有写到达时间" in a for a in result.trip.assumptions)


def test_assumptions_are_capped(places, drive_times):
    """假设清单太长没人看（新人体验时一次生成了 22 条）：路线框架最多留 6 条，每天安排最多留 3 条，重复的只留一条。"""
    sk = {**SKELETON, "assumptions": [f"框架假设 {i}" for i in range(9)]}
    plans = {**PLANS, "assumptions": ["框架假设 0"] + [f"安排假设 {i}" for i in range(5)]}
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(sk)), ScriptStep(text=json.dumps(plans))]) \
        .generate(TripIntake.model_validate(INTAKE), trip_id="trip_test")
    assert result.trip.assumptions == [f"框架假设 {i}" for i in range(6)] + ["安排假设 0", "安排假设 1"]


def test_midday_flight_between_islands_splits_stops_by_region(places, drive_times):
    """11/29 中午从南岛飞北岛：北岛的景点要排在落地之后，不能排成跨岛开车（端到端测试发现的问题）。"""
    intake = {**INTAKE, "start_date": "2026-11-28", "end_date": "2026-11-30", "regions": ["南岛", "北岛"],
              "existing_bookings": [], "members": [{**m, "must_do": []} for m in INTAKE["members"]]}
    sk = {
        "summary": "基督城进，北岛出", "assumptions": [], "questions": [],
        "segments": [{"name": "南岛自驾", "transport": "self_drive", "start_date": "2026-11-28", "end_date": "2026-11-29"},
                     {"name": "北岛自驾", "transport": "self_drive", "start_date": "2026-11-29", "end_date": "2026-11-30"}],
        "flights": [
            {"number": "QF1", "from_airport": "au_mel_airport", "to_airport": "nz_chc_airport", "date": "2026-11-28",
             "depart": "08:00", "arrive": "13:00", "booked": True, "international": True},
            {"number": None, "from_airport": "nz_chc_airport", "to_airport": "nz_hlz_airport", "date": "2026-11-29",
             "depart": "13:30", "arrive": "14:45", "booked": False},
            {"number": None, "from_airport": "nz_hlz_airport", "to_airport": "au_mel_airport", "date": "2026-11-30",
             "depart": "16:00", "arrive": "17:30", "booked": False, "international": True},
        ],
        "days": [
            {"date": "2026-11-28", "night_place_id": "acc_christchurch_house", "night_city": "基督城", "route": "抵达",
             "focus": "", "segment": "南岛自驾"},
            {"date": "2026-11-29", "night_place_id": "acc_rotorua_inn", "night_city": "罗托鲁瓦",
             "route": "基督城 ✈ 汉密尔顿 → 陶波 → 罗托鲁瓦", "focus": "", "segment": "北岛自驾"},
            {"date": "2026-11-30", "night_place_id": None, "night_city": None, "route": "返程", "focus": "",
             "segment": "北岛自驾"},
        ],
    }
    plans = {"days": [
        {"date": "2026-11-28", "stops": []},
        {"date": "2026-11-29", "stops": [
            {"place_id": "nz_lake_taupo", "kind": "meal", "title": "陶波湖边午餐", "duration_min": 60, "booking": "not_required"},
            {"place_id": "nz_huka_falls", "kind": "activity", "title": "胡卡瀑布", "duration_min": 40, "booking": "not_required"},
        ]},
        {"date": "2026-11-30", "depart": "09:00", "stops": [
            {"place_id": "nz_te_waihou_blue_spring", "kind": "activity", "title": "蓝泉", "duration_min": 90, "booking": "not_required"},
        ]},
    ], "assumptions": []}
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(sk)), ScriptStep(text=json.dumps(plans))]) \
        .generate(TripIntake.model_validate(intake), trip_id="trip_test")
    day2 = result.trip.days[1].items
    types = [i.type for i in day2]
    flight_at = types.index(ItemType.FLIGHT)
    assert types[:flight_at + 1] == [ItemType.CHECKOUT, ItemType.DRIVE, ItemType.CAR_DROPOFF, ItemType.FLIGHT]
    assert types[flight_at + 1:flight_at + 3] == [ItemType.BUFFER, ItemType.CAR_PICKUP]
    titles = [i.title for i in day2]
    assert titles.index("胡卡瀑布") > flight_at and types[-1] == ItemType.CHECKIN
    assert not [i for i in result.errors if i.code in ("R01", "R04", "R05")], [i.message for i in result.errors]
    day3 = [i.type for i in result.trip.days[2].items]
    assert day3.index(ItemType.ACTIVITY) < day3.index(ItemType.FLIGHT)  # 去机场之前先去蓝泉


def test_connecting_flights_pick_up_car_at_final_airport(places, drive_times):
    """基督城 → 奥克兰 → 汉密尔顿转机：北岛的车要在汉密尔顿取，不能在奥克兰（端到端测试发现的问题）。"""
    import inspect
    src = inspect.getsource(test_midday_flight_between_islands_splits_stops_by_region)
    ns = {"INTAKE": INTAKE}
    exec("\n".join(l[4:] for l in src.split("result = planner")[0].splitlines()[2:]), ns)
    sk, plans, intake = ns["sk"], ns["plans"], ns["intake"]
    sk["flights"][1] = {"number": None, "from_airport": "nz_chc_airport", "to_airport": "nz_akl_airport",
                        "date": "2026-11-29", "depart": "11:00", "arrive": "12:20", "booked": False}
    sk["flights"].insert(2, {"number": None, "from_airport": "nz_akl_airport", "to_airport": "nz_hlz_airport",
                             "date": "2026-11-29", "depart": "14:00", "arrive": "14:45", "booked": False})
    result = planner(places, drive_times, [ScriptStep(text=json.dumps(sk)), ScriptStep(text=json.dumps(plans)),
                                           ScriptStep(text=json.dumps(plans)), ScriptStep(text=json.dumps(plans)),
                                           ScriptStep(text=json.dumps(plans))]) \
        .generate(TripIntake.model_validate(intake), trip_id="trip_test")
    north = result.trip.vehicles[1]
    assert north.pickup_place_id == "nz_hlz_airport"
    assert not [i for i in result.issues if i.code == "R10"], [i.message for i in result.issues if i.code == "R10"]
