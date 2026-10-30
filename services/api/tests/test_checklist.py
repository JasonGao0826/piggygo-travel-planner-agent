"""出发前清单：每天要带的东西、出发前清单、住宿详情、待确认事项（R26）。不联网。"""

from datetime import date

from fastapi.testclient import TestClient

from tripagent.api import create_app
from tripagent.checklist import day_bring, departure_checklist, missing_confirm_todos
from tripagent.llm.fake import ScriptedLLM
from tripagent.report import build_report
from tripagent.report.model import Paragraph, Table
from tripagent.service import TripService
from tripagent.validator import validate

from .conftest import rebuild

TRIP = "nz_2026"


def find(issues, code):
    return [i for i in issues if i.code == code]


def test_day_bring(trip, places):
    bring = day_bring(trip, places)
    d1 = bring[date(2026, 11, 23)]
    assert d1[:3] == ["护照", "登机牌或电子行程单", "NZeTA 和入境申报（NZTD）"]
    assert "驾照原件" in d1 and "中国驾照的英文翻译件" in d1                 # Tony 是中国驾照
    assert "护照原件（项目要求）" in bring[date(2026, 11, 26)]                # 打枪要带护照原件
    assert "晕船药（容易晕船的人）" in bring[date(2026, 11, 25)]
    assert "路上吃的和水（开车 4 小时以上）" in bring[date(2026, 11, 25)]


def test_departure_checklist(trip):
    groups = dict(departure_checklist(trip))
    assert "NZeTA 电子旅行授权和 IVL 旅游税" in groups["证件"]
    assert "中国驾照的英文翻译件：Tony" in groups["证件"]
    assert "转换插头（新西兰、澳洲都是 I 型）" in groups["手机和电子"]


def test_confirm_needs_use_what_is_already_known(trip):
    ids = {n.todo.id for n in missing_confirm_todos(trip)}
    # 基督城：房型说明写了免费停车、备注写了密码钥匙盒，门牌号已经有手写的确认待办
    assert not {"todo_confirm_acc_christchurch_parking", "todo_confirm_acc_christchurch_self_checkin",
                "todo_confirm_acc_christchurch_address", "todo_confirm_acc_christchurch_beds"} & ids
    assert "todo_confirm_acc_queenstown_beds" in ids
    acc = next(a for a in trip.accommodations if a.id == "acc_queenstown")
    acc.beds, acc.parking, acc.self_checkin = "3 卧室 4 张床", True, "门口密码箱"
    ids = {n.todo.id for n in missing_confirm_todos(rebuild(trip))}
    assert not any(i.startswith("todo_confirm_acc_queenstown") for i in ids)


def test_r26_overdue_confirmations(trip, make_ctx):
    ctx = make_ctx(trip)
    ctx.today = date(2026, 11, 21)                      # 基督城门牌号的确认待办 11/20 截止
    late = [i for i in find(validate(ctx), "R26") if "截止日期" in i.message]
    assert late and "向房东确认基督城民宿门牌号" in late[0].message
    ctx.today = date(2026, 10, 1)
    assert not [i for i in find(validate(ctx), "R26") if "截止日期" in i.message]


def test_report_sections(trip, places):
    trip.meta.prefs.room_requirements = "情侣一间，另外两人必须分床"
    report = build_report(rebuild(trip), places)
    stays = next(s for s in report.sections if s.title.endswith("住宿"))
    tables = [b for b in stays.blocks if isinstance(b, Table)]
    assert tables[1].columns == ["住宿", "入住", "最早入住", "退房", "最晚退房", "入住方式"]
    chc = next(r for r in tables[1].rows if r.cells[0] == "三卧室家庭住宅")
    assert chc.cells[5] == "密码钥匙盒自助入住" and chc.cells[2] == "待确认"
    assert tables[2].rows[2].cells[1:3] == ["3 卧室 4 张床 1 卫，免费停车", "见房型说明"]
    assert any(isinstance(b, Paragraph) and "情侣一间" in b.text for b in stays.blocks)
    schedule = next(s for s in report.sections if s.title.endswith("每天具体时间安排"))
    assert isinstance(schedule.blocks[1], Paragraph) and schedule.blocks[1].text.startswith("带上：护照")
    departure = next(s for s in report.sections if s.title.endswith("出发前清单"))
    assert departure.blocks[1].rows[0].cells == ["证件", departure.blocks[1].rows[0].cells[1], "☐"]


def test_confirm_todos_over_api(store, places, drive_times):
    service = TripService(store, places, drive_times)
    c = TestClient(create_app(service, llm_factory=lambda: ScriptedLLM([])))
    c.post("/api/demo")
    needs = c.get(f"/api/trips/{TRIP}/confirm-needs").json()
    assert needs and all(n["kind"] == "confirm" and n["owner_ids"] == ["m_nina"] for n in needs)
    patch = c.post(f"/api/trips/{TRIP}/confirm-todos", json={"member_id": "m_nina"}).json()
    assert patch["can_confirm"] and len(patch["changes"]) == len(needs)
    c.post(f"/api/trips/{TRIP}/patches/{patch['id']}/confirm", json={"member_id": "m_nina"})
    assert c.get(f"/api/trips/{TRIP}/confirm-needs").json() == []
    assert c.post(f"/api/trips/{TRIP}/confirm-todos", json={"member_id": "m_nina"}).status_code == 400
    codes = {i["code"] for i in c.get(f"/api/trips/{TRIP}").json()["issues"]}
    assert "R26" not in codes or all("截止" in i["message"] or "出发" in i["message"]
                                     for i in c.get(f"/api/trips/{TRIP}").json()["issues"] if i["code"] == "R26")
