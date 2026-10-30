"""账目与 AA 结算（设计规格第 14 节）：分摊零头、多币种折算、最少转账、情侣合并、校验、还款、权限。"""

from datetime import date
from decimal import Decimal as D

import pytest

from tripagent.money import ExpenseIn, MoneyService, fmt
from tripagent.money.settle import allocate
from tripagent.schema.money import Split
from tripagent.service import ServiceError, TripService

TRIP = "nz_2026"
DAY = date(2026, 11, 24)
RATES = {("NZD", "AUD"): D("0.9100"), ("CNY", "AUD"): D("0.2100")}


@pytest.fixture
def money(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    return MoneyService(service, rates=lambda src, dst, day: RATES[(src, dst)])


def add(money, by="m_leo", **kw) -> object:
    base = dict(title="晚餐", category="food", payer=by, amount=D("120"), currency="AUD", date=DAY)
    return money.add(TRIP, ExpenseIn(**{**base, **kw}), by)


# ---------------- 分摊零头 ----------------

def test_allocate_keeps_every_cent():
    assert allocate(D("100"), [D(1)] * 3) == [D("33.34"), D("33.33"), D("33.33")]
    assert allocate(D("0.05"), [D(1)] * 4) == [D("0.02"), D("0.01"), D("0.01"), D("0.01")]
    assert allocate(D("-10"), [D(1)] * 3) == [D("-3.34"), D("-3.33"), D("-3.33")]
    parts = allocate(D("700"), [D(2), D(1), D(1)])
    assert parts == [D("350.00"), D("175.00"), D("175.00")] and sum(parts) == D("700")


# ---------------- 规格 14.7 的例子 ----------------

def test_spec_example_three_transfers(money):
    add(money, by="m_leo", title="皇后镇民宿 4 晚", category="accommodation", amount=D("1600"))
    add(money, by="m_tony", title="南岛租车", category="car", amount=D("800"))
    add(money, by="m_nina", title="滑翔伞 2 人", category="activity", amount=D("700"), item_id="d2_skytrek")
    add(money, by="m_tiange", title="晚餐", amount=D("120"))
    s = money.summary(TRIP)
    nets = {b["name"]: b["net"] for b in s["balances"]}
    assert nets == {"Nina": "-280.00", "Leo": "620.00", "Tony": "170.00", "天哥": "-510.00"}
    assert [p["text"] for p in s["plan"]] == ["天哥 → Leo A$510", "Nina → Tony A$170", "Nina → Leo A$110"]
    assert s["text"].splitlines()[1:] == ["天哥 → Leo A$510", "Nina → Tony A$170", "Nina → Leo A$110"]
    assert s["total_text"] == "A$3,220"


def test_couples_settle_as_one_unit(money):
    add(money, by="m_leo", amount=D("400"))
    money.set_units(TRIP, [["m_nina", "m_leo"]], "m_nina")
    plan = money.summary(TRIP)["plan"]
    # Nina 和 Leo 合成一方：净额 +200，Tony 和天哥各 -100
    assert [p["text"] for p in plan] == ["Tony → Nina和Leo A$100", "天哥 → Nina和Leo A$100"]
    with pytest.raises(ServiceError, match="只有发起人"):
        money.set_units(TRIP, [], "m_tony")


# ---------------- 默认分摊人 ----------------

def test_default_splits(money):
    e = add(money, by="m_nina", title="滑翔伞", category="activity", amount=D("738"), currency="NZD",
            item_id="d2_skytrek", charged_amount=D("672.50"))
    assert [s.member_id for s in e.splits] == ["m_nina", "m_leo"]       # 事件的参加人
    e = add(money, title="晚饭", amount=D("128"), exclude=["m_tiange"])
    assert [s.member_id for s in e.splits] == ["m_nina", "m_leo", "m_tony"]  # "天哥没吃"
    e = add(money, title="油费", category="fuel", amount=D("90"), item_id="d2_skytrek")
    assert len(e.splits) == 4                                                 # 油费默认全员


# ---------------- 多币种 ----------------

def test_fx_methods(money):
    same = add(money, amount=D("50"))
    assert same.fx.method == "same" and same.settle_amount == D("50.00")
    charged = add(money, amount=D("128"), currency="NZD", charged_amount=D("118.42"))
    assert charged.fx.method == "charged" and charged.settle_amount == D("118.42")
    est = add(money, amount=D("128"), currency="NZD")
    assert est.fx.method == "estimate" and est.settle_amount == D("116.48") and est.fx.rate == D("0.91")
    cash = add(money, amount=D("20"), currency="NZD", fx_method="cash")
    assert cash.fx.method == "cash" and cash.settle_amount == D("18.20")
    cny = add(money, amount=D("128"), currency="NZD", charged_amount=D("560"), charged_currency="CNY")
    assert cny.settle_amount == D("117.60") and cny.fx.charged_currency == "CNY"
    actual = money.set_actual(TRIP, est.id, D("117.95"), "m_leo")
    assert actual.fx.method == "charged" and actual.settle_amount == D("117.95")
    assert "折算金额 116.48 → 117.95" in actual.history[-1].summary


def test_no_rate_asks_for_charged_amount(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    money = MoneyService(service, rates=None)
    with pytest.raises(ServiceError, match="实际扣款"):
        add(money, amount=D("10"), currency="NZD")


# ---------------- 校验 ----------------

def test_validation_errors(money):
    with pytest.raises(ServiceError, match="E01"):
        add(money, split_method="exact", splits=[Split(member_id="m_leo", amount=D("50")),
                                                 Split(member_id="m_tony", amount=D("60"))])
    with pytest.raises(ServiceError, match="E02"):
        add(money, by="m_leo", payer="m_nobody")
    with pytest.raises(ServiceError, match="退款"):
        add(money, amount=D("-20"))
    original = add(money, amount=D("100"))
    refund = add(money, amount=D("-20"), refund_of=original.id, title="退押金")
    assert refund.settle_amount == D("-20.00")


def test_checks(money):
    add(money, by="m_nina", title="滑翔伞", category="activity", amount=D("700"), item_id="d2_skytrek",
        splits=[Split(member_id=m) for m in ("m_nina", "m_leo", "m_tony", "m_tiange")])
    add(money, title="晚饭", amount=D("88"))
    add(money, title="晚饭（重复）", amount=D("88"))
    add(money, title="超市", amount=D("40"), currency="NZD")
    add(money, title="还没确认", amount=D("10"), status="draft")
    codes = {c["code"]: c for c in money.checks(TRIP)}
    assert set(codes) == {"E04", "E05", "E07", "DRAFT"}
    assert "Leo" in codes["E07"]["message"]
    s = money.summary(TRIP)
    assert "暂估" in s["text"] and not any(e["title"] == "还没确认" for e in s["expenses"] if e["status"] != "draft")


def test_drafts_do_not_count_until_confirmed(money):
    draft = add(money, amount=D("400"), status="draft")
    assert all(b["net"] == "0.00" for b in money.summary(TRIP)["balances"])
    with pytest.raises(ServiceError, match="只有记账人"):
        money.confirm(TRIP, draft.id, "m_tony")
    money.confirm(TRIP, draft.id, "m_leo")
    assert next(b for b in money.summary(TRIP)["balances"] if b["name"] == "Leo")["net"] == "300.00"


def test_dispute_edit_delete_permissions(money):
    e = add(money, amount=D("100"))
    money.dispute(TRIP, e.id, "m_tony", "我那天没吃")
    assert money.expense(TRIP, e.id).status == "disputed"
    assert any(c["code"] == "DISPUTED" for c in money.checks(TRIP))
    with pytest.raises(ServiceError):
        money.update(TRIP, e.id, money.to_input(e).model_copy(update={"exclude": ["m_tony"], "splits": None}), "m_tony")
    fixed = money.update(TRIP, e.id, money.to_input(e).model_copy(update={"exclude": ["m_tony"], "splits": None}), "m_nina")
    assert [s.member_id for s in fixed.splits] == ["m_nina", "m_leo", "m_tiange"] and fixed.status == "disputed"
    money.confirm(TRIP, e.id, "m_leo")
    assert money.expense(TRIP, e.id).status == "confirmed"
    money.delete(TRIP, e.id, "m_leo")
    assert money.expenses(TRIP) == []
    texts = [a.text for a in money.service.activity(TRIP, 10)]
    assert texts[0] == "Leo 删除了一笔账：晚餐 A$100" and "Tony 对「晚餐」有疑问：我那天没吃" in texts


# ---------------- 还款 ----------------

def test_transfers_count_once_confirmed(money):
    add(money, by="m_leo", amount=D("400"))
    t = money.add_transfer(TRIP, "m_tony", "m_leo", D("100"), "m_tony")
    assert t.status == "sent" and len(money.summary(TRIP)["plan"]) == 3     # 还没确认，不算
    with pytest.raises(ServiceError, match="收款的人"):
        money.confirm_transfer(TRIP, t.id, "m_tony")
    money.confirm_transfer(TRIP, t.id, "m_leo")
    plan = [p["text"] for p in money.summary(TRIP)["plan"]]
    assert plan == ["Nina → Leo A$100", "天哥 → Leo A$100"]
    direct = money.add_transfer(TRIP, "m_nina", "m_leo", D("100"), "m_leo")   # 收款人自己记，直接算确认
    assert direct.status == "confirmed"


def test_fmt_and_csv(money):
    assert fmt(D("1600"), "AUD") == "A$1,600" and fmt(D("128.5"), "NZD") == "NZ$128.50" and fmt(D("-20"), "AUD") == "-A$20"
    add(money, amount=D("100"))
    csv = money.csv(TRIP)
    assert csv.startswith("﻿日期,账目") and "晚餐" in csv and "Leo,100.00,25.00,75.00" in csv
