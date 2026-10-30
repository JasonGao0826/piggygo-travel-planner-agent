"""校验规则测试：先确认 V8 基准行程没有错误，再逐条构造违规场景，确认规则能发现。"""

from datetime import timedelta

import pytest

from tripagent.routing import StaticDriveTimes
from tripagent.schema import Item
from tripagent.validator import ValidatorConfig, check_locked_changes, errors, validate

from .conftest import nz, rebuild


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def find(issues, code):
    return [i for i in issues if i.code == code]


# ---------------- 基准 ----------------

def test_v8_baseline_has_no_errors(trip, make_ctx):
    issues = validate(make_ctx(trip))
    assert errors(issues) == []


def test_v8_baseline_warnings_are_the_known_ones(trip, make_ctx):
    """V8 本身有五处真实的提醒：住宿的床型、停车、入住方式还没确认（R26）；峡湾回程偏紧、11/25 开车 9 小时多、Tony 不满 25 岁、
    11/29 赶早班机前还车可能在柜台营业时间外（案例 V4 后来才发现，改成营业时间外还车）。"""
    issues = validate(make_ctx(trip))
    assert [(i.code, i.item_ids) for i in issues] == [
        ("R04", ["d3_drive_back_1"]),
        ("R05", []),
        ("R11", ["car_south"]),
        ("R25", ["car_south"]),
        ("R26", ["acc_auckland", "acc_christchurch", "acc_queenstown", "acc_rotorua", "acc_twizel"]),
    ]


# ---------------- R01 时间重叠 ----------------

def test_r01_overlap_for_same_member(trip, make_ctx):
    lunch = trip.find_item("d2_lunch")
    lunch.start, lunch.end = nz("2026-11-24", "12:20"), nz("2026-11-24", "13:10")
    issues = find(validate(make_ctx(rebuild(trip))), "R01")
    assert any({"d2_hydro_1", "d2_lunch"} <= set(i.item_ids) for i in issues)


def test_r01_split_groups_can_run_in_parallel(trip, make_ctx):
    """滑翔伞（Nina、Leo）和 Tony、天哥的自由活动同时进行，不算重叠。"""
    assert "R01" not in codes(validate(make_ctx(trip)))


# ---------------- R02 营业时间 ----------------

def test_r02_after_closing(trip, make_ctx):
    item = trip.find_item("d8_waiotapu")
    item.start, item.end = nz("2026-11-30", "15:00"), nz("2026-11-30", "17:00")
    issues = find(validate(make_ctx(rebuild(trip))), "R02")
    assert issues and "16:30" in issues[0].message


def test_r02_after_last_entry(trip, make_ctx):
    item = trip.find_item("d8_waiotapu")
    item.start, item.end = nz("2026-11-30", "15:10"), nz("2026-11-30", "16:20")
    issues = find(validate(make_ctx(rebuild(trip))), "R02")
    assert issues and "最晚入场时间 15:00" in issues[0].message


# ---------------- R03 签到：重现"Cardrona 骑马 vs 打枪"的冲突 ----------------

def _cardrona_morning(trip):
    drive = trip.find_item("d4_drive_cardrona")
    drive.to_place_id = "nz_cardrona_horse_riding"
    drive.start, drive.end = nz("2026-11-26", "08:10"), nz("2026-11-26", "09:25")
    trip.days[3].items.append(Item(
        id="d4_cardrona_riding", type="activity", title="Cardrona 骑马", place_id="nz_cardrona_horse_riding",
        start=nz("2026-11-26", "09:30"), end=nz("2026-11-26", "11:30"),
    ))
    return rebuild(trip)


def test_r03_cardrona_riding_conflicts_with_real_guns(trip, make_ctx):
    issues = find(validate(make_ctx(_cardrona_morning(trip), with_decisions=False)), "R03")
    assert len(issues) == 1
    assert issues[0].item_ids == ["d4_real_guns"]
    assert "11:45 前签到" in issues[0].message and "11:52" in issues[0].message


def test_r03_arrival_counts_from_when_you_reached_the_place(trip, make_ctx):
    """在机场吃晚饭不影响国际航班的到达时间：人从还车起就已经在机场了。"""
    assert "R07" not in codes(validate(make_ctx(trip)))


# ---------------- R04 车程 ----------------

def test_r04_planned_drive_shorter_than_route(trip, make_ctx):
    drive = trip.find_item("d3_drive_milford")
    drive.end = nz("2026-11-25", "11:00")
    issues = find(validate(make_ctx(rebuild(trip))), "R04")
    assert any(i.severity == "error" and i.item_ids == ["d3_drive_milford"] for i in issues)


def test_r04_implicit_move_without_enough_gap(trip, make_ctx):
    trip.days[1].items = [i for i in trip.days[1].items if i.id != "d2_drive_deer"]
    deer = trip.find_item("d2_deer_park")
    deer.start = nz("2026-11-24", "14:20")
    issues = find(validate(make_ctx(rebuild(trip))), "R04")
    assert any(i.severity == "error" and "d2_deer_park" in i.item_ids for i in issues)


# ---------------- R05 每日驾驶时长 ----------------

def test_r05_over_hard_limit(trip, make_ctx):
    trip.find_item("d3_drive_back_2").end = nz("2026-11-25", "23:59")
    issues = find(validate(make_ctx(rebuild(trip))), "R05")
    assert any(i.severity == "error" and "11/25" in i.message for i in issues)


# ---------------- R06 入住退房 ----------------

def test_r06_checkin_before_allowed_time(trip, make_ctx):
    acc = next(a for a in trip.accommodations if a.id == "acc_queenstown")
    acc.earliest_checkin = nz("2026-11-23", "21:00").time()
    issues = find(validate(make_ctx(rebuild(trip))), "R06")
    assert issues and issues[0].severity == "warning" and "21:00" in issues[0].message


def test_r06_checkin_date_mismatch(trip, make_ctx):
    acc = next(a for a in trip.accommodations if a.id == "acc_twizel")
    acc.check_in = acc.check_in.replace(day=26)
    issues = find(validate(make_ctx(rebuild(trip))), "R06")
    assert any(i.severity == "error" and i.item_ids == ["d5_checkin"] for i in issues)


# ---------------- R07 航班 ----------------

def test_r07_too_late_for_early_flight(trip, make_ctx):
    trip.find_item("d7_checkout").start, trip.find_item("d7_checkout").end = nz("2026-11-29", "05:40"), nz("2026-11-29", "05:55")
    trip.find_item("d7_drive_airport").start, trip.find_item("d7_drive_airport").end = nz("2026-11-29", "05:55"), nz("2026-11-29", "06:15")
    trip.find_item("d7_car_dropoff").start, trip.find_item("d7_car_dropoff").end = nz("2026-11-29", "06:15"), nz("2026-11-29", "06:25")
    issues = find(validate(make_ctx(rebuild(trip))), "R07")
    assert any(i.severity == "error" and "05:45 前到机场" in i.message for i in issues)


def test_r07_early_flight_far_from_airport(trip, make_ctx, drive_times):
    far = StaticDriveTimes([{"from": "acc_christchurch_house", "to": "nz_chc_airport", "minutes": 45}])

    class Combined:
        def estimate(self, a, b):
            return far.estimate(a, b) or drive_times.estimate(a, b)

    issues = find(validate(make_ctx(trip, drive=Combined())), "R07")
    assert any(i.severity == "warning" and "早班机" in i.message for i in issues)


# ---------------- R08 锁定项 ----------------

def test_r08_locked_item_changed(trip):
    after = trip.model_copy(deep=True)
    after.find_item("d3_cruise").start = nz("2026-11-25", "13:55")
    after.find_item("d3_cruise").end = nz("2026-11-25", "15:35")
    issues = check_locked_changes(trip, after)
    assert len(issues) == 1 and issues[0].item_ids == ["d3_cruise"]
    assert check_locked_changes(trip, after, allowed={"d3_cruise"}) == []


def test_r08_locked_item_removed(trip):
    after = trip.model_copy(deep=True)
    after.days[4].items = [i for i in after.days[4].items if i.id != "d5_riding"]
    issues = check_locked_changes(trip, after)
    assert issues and "被删除" in issues[0].message


def test_r08_locked_accommodation_changed(trip):
    after = trip.model_copy(deep=True)
    after.accommodations[1].check_out = after.accommodations[1].check_out.replace(day=29)
    assert [i.item_ids for i in check_locked_changes(trip, after)] == [["acc_twizel"]]


# ---------------- R09 每晚住宿 ----------------

def test_r09_missing_night(trip, make_ctx):
    trip.accommodations = [a for a in trip.accommodations if a.id != "acc_twizel"]
    issues = find(validate(make_ctx(trip)), "R09")
    assert [i.message for i in issues] == ["11/27 晚上没有住宿"]


# ---------------- R10 租车 ----------------

def test_r10_drive_after_car_returned(trip, make_ctx):
    car = next(v for v in trip.vehicles if v.id == "car_south")
    car.dropoff_time = nz("2026-11-29", "05:00")
    issues = find(validate(make_ctx(trip)), "R10")
    assert any("d7_drive_airport" in i.item_ids for i in issues)


# ---------------- R11 司机资格 ----------------

def test_r11_missing_licence_translation(trip, make_ctx):
    trip.todos = [t for t in trip.todos if t.id != "todo_licence_tony"]
    issues = find(validate(make_ctx(trip)), "R11")
    assert any(i.severity == "error" and "翻译件" in i.message for i in issues)


def test_r11_driver_too_young(trip, make_ctx):
    trip.member("m_tony").age = 20
    issues = find(validate(make_ctx(trip)), "R11")
    assert any(i.severity == "error" and "最低年龄" in i.message for i in issues)


# ---------------- R12 每段开车的司机 ----------------

def test_r12_driver_not_in_group(trip, make_ctx):
    trip.find_item("d2_drive_down").driver_id = "m_leo"
    issues = find(validate(make_ctx(trip)), "R12")
    assert issues and issues[0].item_ids == ["d2_drive_down"]


def test_r12_driver_not_on_rental_contract(trip, make_ctx):
    trip.find_item("d1_drive_town").driver_id = "m_nina"
    issues = find(validate(make_ctx(trip)), "R12")
    assert any("驾驶员名单" in i.message for i in issues)


# ---------------- R13 天气备用 ----------------

def test_r13_weather_without_backup(trip, make_ctx):
    trip.find_item("d2_skytrek").backup = None
    issues = find(validate(make_ctx(trip)), "R13")
    assert [i.item_ids for i in issues] == [["d2_skytrek"]]


# ---------------- R14 项目限制 ----------------

def test_r14_weight_limit(trip, make_ctx):
    trip.member("m_leo").weight_kg = 110
    issues = find(validate(make_ctx(trip)), "R14")
    assert issues and issues[0].item_ids == ["d5_riding"] and "105kg" in issues[0].message


# ---------------- R15 预订待办 ----------------

def test_r15_to_book_without_todo(trip, make_ctx):
    trip.todos = [t for t in trip.todos if t.id != "todo_book_hobbiton"]
    issues = find(validate(make_ctx(trip)), "R15")
    assert [i.item_ids for i in issues] == [["d9_hobbiton"]]


def test_r15_due_before_booking_window_opens(trip, make_ctx):
    todo = next(t for t in trip.todos if t.id == "todo_book_deer_park")
    todo.due = todo.due.replace(day=10)
    issues = find(validate(make_ctx(trip)), "R15")
    assert issues and issues[0].severity == "warning" and "11/17" in issues[0].message


# ---------------- R16 否决方案 ----------------

def test_r16_rejected_cardrona_riding(trip, make_ctx):
    issues = find(validate(make_ctx(_cardrona_morning(trip))), "R16")
    assert issues and issues[0].item_ids == ["d4_cardrona_riding"] and "D-001" in issues[0].message


def test_r16_rejected_glowworm_caves(trip, make_ctx):
    trip.days[6].items.append(Item(
        id="d7_waitomo", type="activity", title="怀托摩萤火虫洞", place_id="nz_waitomo_glowworm_caves",
        start=nz("2026-11-29", "17:00"), end=nz("2026-11-29", "17:45"),
    ))
    issues = find(validate(make_ctx(rebuild(trip))), "R16")
    assert issues and "D-004" in issues[0].message


# ---------------- R17 必玩项目 ----------------

def test_r17_must_do_removed(trip, make_ctx):
    trip.days[4].items = [i for i in trip.days[4].items if i.id != "d5_riding"]
    issues = find(validate(make_ctx(trip)), "R17")
    assert issues and "D-001" in issues[0].message


def test_r17_another_glenorchy_operator_still_satisfies(trip, make_ctx):
    """"必须在格林诺奇骑马"只锁地点不锁商家：换成同在格林诺奇的 High Country Horses 仍然满足。"""
    trip.find_item("d5_riding").place_id = "nz_high_country_horses"
    trip.find_item("d5_drive_glenorchy").to_place_id = "nz_high_country_horses"
    assert "R17" not in codes(validate(make_ctx(trip)))


# ---------------- R18 地点存在 ----------------

def test_r18_unknown_place(trip, make_ctx):
    trip.find_item("d1_lunch").place_id = "nz_unknown_cafe"
    issues = find(validate(make_ctx(trip)), "R18")
    assert issues and issues[0].item_ids == ["d1_lunch"]


# ---------------- R19 连续高强度 ----------------

def test_r19_two_heavy_days_in_a_row(trip, make_ctx):
    trip.find_item("d4_drive_home").end = nz("2026-11-26", "22:00")
    issues = find(validate(make_ctx(rebuild(trip))), "R19")
    assert issues and "11/25 和 11/26" in issues[0].message


# ---------------- R20 用餐 ----------------

@pytest.mark.parametrize("meal_id,expected", [("d4_lunch", "11/26 11:00–14:30 之间没有安排午餐"),
                                              ("d6_dinner", "11/28 17:00–20:30 之间没有安排晚餐")])
def test_r20_missing_meal(trip, make_ctx, meal_id, expected):
    for d in trip.days:
        d.items = [i for i in d.items if i.id != meal_id]
    assert [i.message for i in find(validate(make_ctx(trip)), "R20")] == [expected]


def test_r20_only_while_on_the_trip(trip, make_ctx):
    """还没落地、或者已经要去机场回家了，不提醒没安排午餐晚餐（新人体验时，中午起飞的回程航班被误报）。"""
    later = timedelta(hours=3, minutes=15)                   # 去程推迟到 15:00 才落地，也没安排午餐
    trip.days[0].items = [i.model_copy(update={"start": i.start + later, "end": i.end + later})
                          for i in trip.days[0].items if i.id != "d1_lunch"]
    last = trip.days[-1]                                     # 回程改成 12:05 起飞，起飞前只退房
    last.items = [i for i in last.items if i.id == "d10_checkout"] + [trip.find_item("d10_flight_qf206").model_copy(
        update={"start": nz("2026-12-02", "12:05"), "end": nz("2026-12-02", "15:15")})]
    assert [i.message for i in find(validate(make_ctx(rebuild(trip))), "R20")] == ["11/23 17:00–20:30 之间没有安排晚餐"]


def test_r10_car_must_start_where_it_was_parked(trip, make_ctx):
    """车停在 Kaituna，却从民宿出发去 Wai-O-Tapu：车不在那里。"""
    trip.find_item("d8_drive_waiotapu").from_place_id = "acc_rotorua_inn"
    issues = find(validate(make_ctx(trip)), "R10")
    assert any("车还停在 Kaituna" in i.message and i.item_ids == ["d8_drive_waiotapu"] for i in issues)


# ---------------- R25 取还车在柜台营业时间内 ----------------

def test_r25_car_desk_hours(trip, make_ctx):
    [issue] = find(validate(make_ctx(trip)), "R25")
    assert issue.message.startswith("11/29 05:") and "还车" in issue.message and "营业时间以外" in issue.message
    car = next(v for v in trip.vehicles if v.id == "car_south")
    car.dropoff_desk_hours = "05:00-23:00"                                   # 合同写了 5 点开门
    assert find(validate(make_ctx(rebuild(trip))), "R25") == []
    car.dropoff_desk_hours, car.after_hours_return = "07:00-21:00", "钥匙投进还车箱"
    [issue] = find(validate(make_ctx(rebuild(trip))), "R25")
    assert "柜台营业时间 07:00-21:00" in issue.message and issue.suggestions == ["按合同里的营业时间外还车方式办理"]


def test_optional_items_are_suggested_first(trip, make_ctx):
    # 11/25 开车超过 11 小时（错误）时，建议里先提当天的备选：镜湖
    photo = trip.find_item("d3_mirror_lakes")
    photo.optional, photo.optional_reason = True, "时间不够就跳过"
    ctx = make_ctx(rebuild(trip))
    ctx.config = ValidatorConfig(max_drive_hard_hours=8)
    [issue] = find(validate(ctx), "R05")
    assert issue.severity == "error" and issue.suggestions[0] == "先去掉当天的备选：镜湖短暂停留"
