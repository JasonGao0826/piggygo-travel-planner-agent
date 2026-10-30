"""账本的接口、猪猪的记账工具、凭证顺带生成的草稿、报告里的"费用与结算"。用剧本模型，不联网。"""

import json
from decimal import Decimal as D
from pathlib import Path

from fastapi.testclient import TestClient

from tripagent.agent import TripAgent
from tripagent.api import create_app
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.money import MoneyService
from tripagent.report import build_report
from tripagent.service import TripService

TRIP = "nz_2026"


def client(store, places, drive_times, tmp_path, steps=()):
    service = TripService(store, places, drive_times)
    c = TestClient(create_app(service, llm_factory=lambda: ScriptedLLM(list(steps)), uploads_dir=tmp_path))
    c.post("/api/demo")
    return c, service


def expense(**kw):
    return {"title": "皇后镇民宿 4 晚", "category": "accommodation", "payer": "m_leo", "amount": "1600",
            "currency": "AUD", "date": "2026-11-23", **kw}


def test_money_flow_over_api(store, places, drive_times, tmp_path):
    c, service = client(store, places, drive_times, tmp_path)
    s = c.post(f"/api/trips/{TRIP}/money/expenses", json={"member_id": "m_leo", "expense": expense()}).json()
    assert s["plan"][0]["text"] == "Nina → Leo A$400" and s["total_text"] == "A$1,600"
    x = s["expenses"][0]
    assert x["split_text"] == "4 人均分" and x["shares"][0]["text"] == "A$400"

    # 没有汇率服务时，外币要填实际扣款
    r = c.post(f"/api/trips/{TRIP}/money/expenses", json={"member_id": "m_tony", "expense": expense(
        title="晚餐", category="food", payer="m_tony", amount="128", currency="NZD", exclude=["m_tiange"])})
    assert r.status_code == 400 and "实际扣款" in r.json()["detail"]
    s = c.post(f"/api/trips/{TRIP}/money/expenses", json={"member_id": "m_tony", "expense": expense(
        title="晚餐", category="food", payer="m_tony", amount="128", currency="NZD", exclude=["m_tiange"],
        charged_amount="117.30")}).json()
    dinner = next(e for e in s["expenses"] if e["title"] == "晚餐")
    assert dinner["settle_text"] == "A$117.30" and dinner["split_text"] == "Nina、Leo、Tony均分"

    s = c.post(f"/api/trips/{TRIP}/money/expenses/{dinner['id']}/dispute", json={"member_id": "m_nina", "note": "我没点酒"}).json()
    assert any(ch["code"] == "DISPUTED" for ch in s["checks"])
    assert c.delete(f"/api/trips/{TRIP}/money/expenses/{dinner['id']}", params={"member_id": "m_tiange"}).status_code == 400

    s = c.put(f"/api/trips/{TRIP}/money/units", json={"member_id": "m_nina", "units": [["m_nina", "m_leo"]]}).json()
    assert s["units"] == [["m_nina", "m_leo"]]
    s = c.post(f"/api/trips/{TRIP}/money/transfers", json={"member_id": "m_tony", "from_member": "m_tony",
                                                           "to_member": "m_leo", "amount": "100"}).json()
    t = s["transfers"][0]
    assert t["status"] == "sent"
    s = c.post(f"/api/trips/{TRIP}/money/transfers/{t['id']}/confirm", json={"member_id": "m_leo"}).json()
    assert s["transfers"][0]["status"] == "confirmed"

    csv = c.get(f"/api/trips/{TRIP}/money.csv")
    assert csv.headers["content-type"].startswith("text/csv") and "皇后镇民宿" in csv.content.decode("utf-8-sig")
    report = c.get(f"/api/trips/{TRIP}/report").json()
    assert any(sec["title"].endswith("费用与结算") for sec in report["sections"])
    feed = [e["text"] for e in c.get(f"/api/trips/{TRIP}/activity").json()]
    assert "Leo 记了一笔：皇后镇民宿 4 晚 A$1,600，4 人均分" in feed


def test_voucher_with_price_creates_draft(store, places, drive_times, tmp_path):
    extraction = {"bookings": [{"kind": "activity", "title": "Skyline 缆车＋斜坡滑车", "date": "2026-11-23",
                                "price_amount": 260, "price_currency": "AUD", "quotes": []}], "notes": [], "unreadable": False}
    c, service = client(store, places, drive_times, tmp_path, [
        ScriptStep(text=json.dumps(extraction)), ScriptStep(text="已记录。")])
    png = (Path(__file__).parent / "fixtures" / "vouchers" / "realnz_cruise.png").read_bytes()
    up = c.post(f"/api/trips/{TRIP}/evidence", data={"member_id": "m_tony"},
                files={"file": ("skyline.png", png, "image/png")}).json()
    c.post(f"/api/trips/{TRIP}/evidence/{up['upload_id']}/apply", json={"member_id": "m_tony", "extraction": up["extraction"]})
    [draft] = c.get(f"/api/trips/{TRIP}/money").json()["expenses"]
    assert draft["status"] == "draft" and draft["payer"] == "m_tony" and draft["evidence_id"] == "ev_001"
    assert draft["amount_text"] == "A$260" and draft["category"] == "activity"


def test_agent_records_expense_and_answers_balances(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    llm = ScriptedLLM([
        ScriptStep("propose_expense", {"title": "皇后镇晚餐", "category": "food", "amount": 120, "currency": "AUD",
                                       "exclude": ["m_tiange"]}),
        ScriptStep("get_balances", {}),
        ScriptStep(text="记好了草稿，确认后入账。"),
    ])
    reply = TripAgent(service, llm).handle(TRIP, "m_leo", "晚饭我付了 120 澳币，天哥没吃")
    assert [c.error for c in reply.tool_calls] == [None, None]
    rec = reply.tool_calls[0].result
    assert rec["分摊"] == ["Nina A$40", "Leo A$40", "Tony A$40"] and rec["status"].startswith("草稿")
    assert reply.tool_calls[1].result["数据"]["结算方案"] == "大家已经两清了"          # 草稿还没入账
    assert "propose_expense" in [t for t in llm.seen_systems[0].split() if "propose_expense" in t][0]


def test_report_section(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    money = MoneyService(service)
    from tripagent.money import ExpenseIn
    from datetime import date
    money.add(TRIP, ExpenseIn(title="南岛租车", category="car", payer="m_tony", amount=D("800"), currency="AUD",
                              date=date(2026, 11, 23)), "m_tony")
    report = build_report(service.current(TRIP), places, money=money.summary(TRIP))
    section = next(s for s in report.sections if s.title.endswith("费用与结算"))
    rows = section.blocks[0].rows
    assert rows[0].cells[1:5] == ["南岛租车", "租车", "Tony", "A$800"]
    assert section.blocks[3].items == ["Nina → Tony A$200", "Leo → Tony A$200", "天哥 → Tony A$200"]


def test_voucher_draft_is_split_by_people_on_voucher(store, places, drive_times, tmp_path):
    """凭证上写了谁就由谁分摊：一个人的项目不能默认摊给全员（新人体验时发现的问题）。"""
    extraction = {"bookings": [{"kind": "activity", "title": "米尔福德巴士＋游船", "date": "2026-11-25", "pax": 1,
                                "participants": ["Nina Li"], "price_amount": 319, "price_currency": "AUD", "quotes": []}],
                  "notes": [], "unreadable": False}
    service = TripService(store, places, drive_times)
    llm = ScriptedLLM([ScriptStep(text=json.dumps(extraction)), ScriptStep(text="已记录。")])
    c = TestClient(create_app(service, llm_factory=lambda: llm, uploads_dir=tmp_path))
    c.post("/api/demo")
    png = (Path(__file__).parent / "fixtures" / "vouchers" / "realnz_cruise.png").read_bytes()
    up = c.post(f"/api/trips/{TRIP}/evidence", data={"member_id": "m_tony"},
                files={"file": ("cruise.png", png, "image/png")}).json()
    c.post(f"/api/trips/{TRIP}/evidence/{up['upload_id']}/apply", json={"member_id": "m_tony", "extraction": up["extraction"]})
    [draft] = c.get(f"/api/trips/{TRIP}/money").json()["expenses"]
    assert draft["payer"] == "m_tony" and [s["member_id"] for s in draft["splits"]] == ["m_nina"]
    assert "Nina" in draft["note"]
    told = llm.seen_messages[-1][-1].text                                  # 猪猪知道已经生成了草稿，不会再记一次
    assert "系统已按凭证金额生成账目草稿" in told and "Nina 分摊" in told


def test_members_on_voucher(trip):
    from tripagent.money import members_on_voucher
    assert members_on_voucher(trip, ["NINA LI"], 1, "m_tony") == ["m_nina"]          # 英文名按整词对
    assert members_on_voucher(trip, ["天哥"], None, "m_tony") == ["m_tiange"]           # 中文名按包含对
    assert members_on_voucher(trip, ["Nina", "Leo Wu"], 2, "m_tony") == ["m_nina", "m_leo"]
    assert members_on_voucher(trip, [], 1, "m_tony") == ["m_tony"]                       # 没写名字、1 人：上传的人
    assert members_on_voucher(trip, ["Nina"], 2, "m_tony") is None                    # 人数对不上：用默认分摊人
    assert members_on_voucher(trip, ["Somebody Else"], 1, "m_tony") is None            # 名字对不上
    assert members_on_voucher(trip, ["Ninaa"], 1, "m_tony") is None                   # 英文名不按包含对
    assert members_on_voucher(trip, [], 4, "m_tony") is None
