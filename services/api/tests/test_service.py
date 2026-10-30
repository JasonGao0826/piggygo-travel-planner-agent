"""改动提案与版本：提出 → 校验 → 确认 → 写入新版本；锁定保护；并发冲突；决策账本。"""

import pytest

from tripagent.schema import BookingStatus, Decision, PatchStatus, RejectedOption, Source, Todo
from tripagent.service import ConflictError, ServiceError, TripService
from tripagent.state import apply_ops

from .conftest import nz, rebuild

TRIP = "nz_2026"


def _v3_milford(trip):
    """回到 V3 的状态：峡湾游船还没订，计划 13:55 场，回程相应推后。"""
    cruise = trip.find_item("d3_cruise")
    cruise.start, cruise.end = nz("2026-11-25", "13:55"), nz("2026-11-25", "15:35")
    cruise.booking_status, cruise.locked, cruise.lock_reason = BookingStatus.TO_BOOK, False, None
    cruise.evidence_ids, cruise.source = [], Source.KNOWLEDGE_BASE
    back1 = trip.find_item("d3_drive_back_1")
    back1.start, back1.end = nz("2026-11-25", "15:50"), nz("2026-11-25", "18:15")
    dinner = trip.find_item("d3_dinner")
    dinner.start, dinner.end = nz("2026-11-25", "18:15"), nz("2026-11-25", "19:00")
    back2 = trip.find_item("d3_drive_back_2")
    back2.start, back2.end = nz("2026-11-25", "19:00"), nz("2026-11-25", "21:15")
    trip.todos.append(Todo(id="todo_book_milford", title="预约峡湾游船", kind="booking",
                           item_ids=["d3_cruise"], due="2026-11-01"))
    return rebuild(trip)


@pytest.fixture
def service(places, drive_times, store):
    return TripService(store, places, drive_times)


@pytest.fixture
def v1(service, trip, decisions):
    service.create_trip(trip, decisions)
    return service


# ---------------- 正常流程：回放第 10 步，凭证显示游船是 13:15 ----------------

def test_voucher_moves_cruise_to_1315_and_creates_version_2(service, trip, decisions):
    service.create_trip(_v3_milford(trip), decisions)
    assert [i for i in service.validate(TRIP) if i.severity == "error"] == []

    patch = service.propose(TRIP, [
        {"op": "update", "collection": "items", "id": "d3_cruise", "set": {
            "start": nz("2026-11-25", "13:15").isoformat(), "end": nz("2026-11-25", "14:55").isoformat(),
            "booking_status": "booked", "locked": True, "lock_reason": "evidence",
            "evidence_ids": ["ev_milford"], "source": "evidence"}},
        {"op": "update", "collection": "todos", "id": "todo_book_milford", "set": {"status": "done"}},
    ], summary="按游船凭证改为 13:15 场", evidence_ids=["ev_milford"])

    assert patch.status == PatchStatus.PROPOSED and not patch.has_errors
    version = service.confirm(patch.id, confirmed_by="m_nina")

    assert version.version == 2 and version.state.version == 2
    cruise = service.current(TRIP).find_item("d3_cruise")
    assert cruise.locked and cruise.start == nz("2026-11-25", "13:15")
    assert version.changelog[0] == "按游船凭证改为 13:15 场"
    assert any("开始时间 11/25 13:55 → 11/25 13:15" in line for line in version.changelog)
    assert [v.version for v in service.history(TRIP)] == [1, 2]


def test_item_moves_to_another_day_when_start_changes(v1):
    patch = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_skyline", "set": {
        "start": nz("2026-11-26", "16:30").isoformat(), "end": nz("2026-11-26", "17:45").isoformat()}}],
        summary="Skyline 改到 11/26")
    after = apply_ops(v1.current(TRIP), patch.ops)
    assert "d1_skyline" in [i.id for i in after.days[3].items]
    assert "d1_skyline" not in [i.id for i in after.days[0].items]


# ---------------- 锁定保护 ----------------

def test_changing_locked_item_needs_user_consent(v1):
    ops = [{"op": "update", "collection": "items", "id": "d4_real_guns", "set": {
        "start": nz("2026-11-26", "14:00").isoformat(), "end": nz("2026-11-26", "15:30").isoformat()}}]
    patch = v1.propose(TRIP, ops, summary="打枪改到下午")
    assert patch.has_errors and patch.issues[0].code == "R08"
    with pytest.raises(ServiceError):
        v1.confirm(patch.id, confirmed_by="m_nina")

    allowed = v1.propose(TRIP, ops, summary="打枪改到下午（用户确认）", unlock_item_ids=["d4_real_guns"])
    assert "R08" not in {i.code for i in allowed.issues}


# ---------------- 校验不通过的改动不能写入 ----------------

def test_patch_that_breaks_checkin_is_blocked(v1):
    patch = v1.propose(TRIP, [
        {"op": "update", "collection": "items", "id": "d4_drive_cardrona", "set": {
            "to_place_id": "nz_cardrona_horse_riding",
            "start": nz("2026-11-26", "08:10").isoformat(), "end": nz("2026-11-26", "09:25").isoformat()}},
        {"op": "add", "collection": "items", "value": {
            "id": "d4_cardrona_riding", "type": "activity", "title": "Cardrona 骑马",
            "place_id": "nz_cardrona_horse_riding",
            "start": nz("2026-11-26", "09:30").isoformat(), "end": nz("2026-11-26", "11:30").isoformat()}},
    ], summary="11/26 上午加 Cardrona 骑马")
    assert {"R03", "R16"} <= {i.code for i in patch.issues if i.severity == "error"}
    with pytest.raises(ServiceError):
        v1.confirm(patch.id, confirmed_by="m_nina")


def test_malformed_patch_is_rejected_with_reason(v1):
    patch = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "no_such_item", "set": {"title": "x"}}],
                       summary="写错 id")
    assert patch.status == PatchStatus.REJECTED
    assert patch.issues[0].code == "P00" and "no_such_item" in patch.issues[0].message


def test_only_new_issues_are_reported(v1):
    """V8 本身就有的 3 个提醒不算在新改动头上。"""
    patch = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "皇后镇汉堡午餐"}}],
                       summary="改名")
    assert patch.issues == []
    assert [c.id for c in patch.changes] == ["d1_lunch"]


# ---------------- 并发 ----------------

def test_concurrent_patches_on_different_records_both_apply(v1):
    a = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "A 改的"}}], summary="A")
    b = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d2_dinner", "set": {"title": "B 改的"}}], summary="B")
    v1.confirm(a.id, confirmed_by="m_nina")
    v3 = v1.confirm(b.id, confirmed_by="m_leo")
    assert v3.version == 3
    assert v3.state.find_item("d1_lunch").title == "A 改的" and v3.state.find_item("d2_dinner").title == "B 改的"


def test_concurrent_patches_on_same_record_conflict(v1):
    a = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "A 改的"}}], summary="A")
    b = v1.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "B 改的"}}], summary="B")
    v1.confirm(a.id, confirmed_by="m_nina")
    with pytest.raises(ConflictError):
        v1.confirm(b.id, confirmed_by="m_leo")
    assert v1.store.get_patch(b.id).status == PatchStatus.CONFLICT
    assert v1.current(TRIP).find_item("d1_lunch").title == "A 改的"


# ---------------- 决策账本 ----------------

def test_new_rejection_flags_existing_plan(v1):
    issues = v1.record_decision(TRIP, Decision(
        id="D-030", content="不去 Wai-O-Tapu", kind="rejection",
        rejected_options=[RejectedOption(description="Wai-O-Tapu 地热公园", place_id="nz_wai_o_tapu")]))
    assert [i.code for i in issues] == ["R16"] and issues[0].item_ids == ["d8_waiotapu"]


def test_superseding_decision_marks_old_one(v1):
    v1.record_decision(TRIP, Decision(id="D-031", content="峡湾改回坐大巴", kind="decision", supersedes="D-003"))
    statuses = {d.id: d.status for d in v1.decisions(TRIP)}
    assert statuses["D-003"] == "superseded" and statuses["D-031"] == "active"
