"""账本（设计规格第 14 节）：记账、折算、校验、还款、结算方案。

金额计算、汇率折算、结算方案全部由代码完成，模型只负责听懂"晚饭我付了 NZ$128，天哥没吃"这类话并生成草稿。
"""

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal

from pydantic import Field

from ..schema.common import Model
from ..schema.money import CATEGORY_LABEL, SHARED_BY_ALL, Category, Expense, ExpenseChange, Fx, MoneySettings, Split, Transfer
from ..service import ServiceError, TripService
from .settle import cents, settle, shares_of

RateFn = Callable[[str, str, date], Decimal]   # (原币, 目标币种, 日期) → 1 单位原币等于多少目标币种
SYMBOL = {"AUD": "A$", "NZD": "NZ$", "CNY": "¥", "USD": "US$", "EUR": "€", "GBP": "£"}
CAN_EDIT_ANY = ("owner",)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def fmt(amount: Decimal, currency: str) -> str:
    """A$1,600 / NZ$128.50 / -A$20"""
    sign = "-" if amount < 0 else ""
    a = abs(cents(amount))
    text = f"{a:,.0f}" if a == a.to_integral() else f"{a:,.2f}"
    return f"{sign}{SYMBOL.get(currency, currency + ' ')}{text}"


class ExpenseIn(Model):
    """记一笔账时填的内容。没填的按默认规则补（14.4、14.5 节）。"""

    title: str = Field(min_length=1, max_length=100)
    category: Category = "other"
    payer: str
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    date: date
    split_method: Literal["equal", "shares", "exact"] = "equal"
    splits: list[Split] | None = Field(default=None, max_length=12)   # 不填就用默认分摊人
    exclude: list[str] = Field(default_factory=list, max_length=12)   # 默认分摊人里去掉谁，例如"天哥没吃"
    fx_method: Literal["auto", "charged", "cash", "estimate"] = "auto"
    charged_amount: Decimal | None = None
    charged_currency: str | None = None
    item_id: str | None = None
    evidence_id: str | None = None
    refund_of: str | None = None
    note: str = Field(default="", max_length=500)
    status: Literal["draft", "confirmed"] = "confirmed"


def _norm(name: str) -> str:
    return re.sub(r"[\s·.,，、/()（）_-]+", " ", name).strip().lower()


def members_on_voucher(trip, participants: list[str], pax: int | None, uploader: str) -> list[str] | None:
    """凭证上写的是谁，用来给凭证生成的账目草稿定分摊人：
    - 写了名字、都能对上行程成员（英文名按整词对，中文名按包含对），人数也对得上：就是这些人；
    - 没写名字、只有 1 人：就是上传凭证的人；
    - 对不上或人数不符：返回 None，用默认分摊人，提醒用户核对。"""
    found: list[str] = []
    for raw in participants:
        text = _norm(raw)
        if not text:
            continue
        for m in trip.members:
            name = _norm(m.name)
            if not name:
                continue
            hit = (name == text or name in text.split()) if name.isascii() else name in text.replace(" ", "")
            if hit and m.id not in found:
                found.append(m.id)
                break
    named = len([p for p in participants if _norm(p)])
    if found:
        if len(found) < named or (pax is not None and pax != len(found)):
            return None
        return found
    if pax == 1 and not named:
        return [uploader]
    return None


def gateway_rates(gateway) -> RateFn:
    """用 Frankfurter（各国央行参考价）查汇率。未来的日期查不到，用最新汇率。"""
    def rate(src: str, dst: str, day: date) -> Decimal:
        args = {"amount": 1000, "from": src, "to": dst}
        if day <= date.today():
            args["date"] = day.isoformat()
        data = gateway.call("frankfurter", "convert", args).data
        if not isinstance(data, dict) or "amount" not in data:
            raise ValueError(f"汇率结果看不懂：{str(data)[:80]}")
        return Decimal(str(data["amount"])) / 1000
    return rate


class MoneyService:
    def __init__(self, service: TripService, rates: RateFn | None = None):
        self.service = service
        self.store = service.store
        self.rates = rates

    # ---------- 读取 ----------

    def expenses(self, trip_id: str) -> list[Expense]:
        return sorted(self.store.expenses(trip_id), key=lambda e: (e.date, e.created_at))

    def expense(self, trip_id: str, expense_id: str) -> Expense:
        e = next((x for x in self.store.expenses(trip_id) if x.id == expense_id), None)
        if e is None:
            raise ServiceError(f"找不到账目 {expense_id}")
        return e

    def settle_currency(self, trip_id: str) -> str:
        return self.service.current(trip_id).meta.settle_currency

    def name(self, trip_id: str, member_id: str) -> str:
        return self.service.name_of(trip_id, member_id)

    # ---------- 记账 ----------

    def _rate(self, src: str, dst: str, day: date) -> Decimal:
        if self.rates is None:
            raise ServiceError(f"现在查不到 {src}→{dst} 的汇率，请填银行卡实际扣款金额")
        try:
            return self.rates(src, dst, day)
        except Exception as exc:
            raise ServiceError(f"汇率暂时查不到（{type(exc).__name__}），请填银行卡实际扣款金额") from exc

    def _fx(self, trip_id: str, data: ExpenseIn) -> tuple[Fx, Decimal]:
        settle_cur = self.settle_currency(trip_id)
        if data.currency == settle_cur:
            return Fx(method="same"), cents(data.amount)
        method = data.fx_method
        if method == "auto":
            method = "charged" if data.charged_amount is not None else "estimate"
        if method == "charged":
            if data.charged_amount is None:
                raise ServiceError("选了按实际扣款折算，但没填扣款金额（E03）")
            charged_cur = data.charged_currency or settle_cur
            if charged_cur == settle_cur:
                return Fx(method="charged", charged_amount=data.charged_amount, charged_currency=settle_cur), cents(data.charged_amount)
            rate = self._rate(charged_cur, settle_cur, data.date)
            return (Fx(method="charged", charged_amount=data.charged_amount, charged_currency=charged_cur, rate=rate,
                       rate_date=data.date, source="Frankfurter（各国央行参考价）"),
                    cents(data.charged_amount * rate))
        rate = self._rate(data.currency, settle_cur, data.date)
        return (Fx(method=method, rate=rate, rate_date=data.date, source="Frankfurter（各国央行参考价）"),
                cents(data.amount * rate))

    def default_splits(self, trip_id: str, data: ExpenseIn) -> list[Split]:
        """关联了行程事件的，默认由参加人分摊；其余默认全员（14.4 节）。"""
        trip = self.service.current(trip_id)
        members = [m.id for m in trip.members]
        if data.item_id and data.category not in SHARED_BY_ALL:
            item = trip.find_item(data.item_id)
            if item is not None:
                members = [m for m in members if m in trip.participants_of(item)]
        return [Split(member_id=m) for m in members if m not in data.exclude]

    def build(self, trip_id: str, data: ExpenseIn, by: str, *, expense_id: str, old: Expense | None = None) -> Expense:
        trip = self.service.current(trip_id)
        members = {m.id for m in trip.members}
        if data.payer not in members:
            raise ServiceError(f"付款人 {data.payer} 不是行程成员（E02）")
        if data.amount == 0:
            raise ServiceError("金额不能是 0")
        if data.amount < 0 and not data.refund_of:
            raise ServiceError("负数金额只用于退款，请写明是哪一笔的退款")
        if data.item_id and trip.find_item(data.item_id) is None:
            raise ServiceError(f"行程里没有安排 {data.item_id}")
        splits = data.splits if data.splits else self.default_splits(trip_id, data)
        if not splits:
            raise ServiceError("没有分摊人")
        if bad := [s.member_id for s in splits if s.member_id not in members]:
            raise ServiceError(f"分摊人不是行程成员：{'、'.join(bad)}（E02）")
        if len({s.member_id for s in splits}) != len(splits):
            raise ServiceError("分摊人有重复")
        if data.split_method == "shares" and any((s.shares or 0) <= 0 for s in splits):
            raise ServiceError("按份数分摊时，每人的份数要大于 0")
        if data.split_method == "exact":
            if any(s.amount is None for s in splits):
                raise ServiceError("逐人指定金额时，每个人都要填金额")
            total = sum((s.amount for s in splits), Decimal(0))
            if cents(total) != cents(data.amount):
                raise ServiceError(f"逐人金额加起来是 {fmt(total, data.currency)}，和总额 {fmt(data.amount, data.currency)} 对不上（E01）")
        fx, settle_amount = self._fx(trip_id, data)
        return Expense(
            id=expense_id, title=data.title.strip(), category=data.category, payer=data.payer, amount=cents(data.amount),
            currency=data.currency, settle_amount=settle_amount, fx=fx, date=data.date, split_method=data.split_method,
            splits=splits, item_id=data.item_id, evidence_id=data.evidence_id, refund_of=data.refund_of,
            status=data.status if old is None or old.status == "draft" else old.status, note=data.note.strip(),
            dispute_note=old.dispute_note if old else "", created_by=old.created_by if old else by,
            created_at=old.created_at if old else _now(), history=list(old.history) if old else [],
        )

    def _split_text(self, trip_id: str, e: Expense) -> str:
        n = len(e.splits)
        everyone = {m.id for m in self.service.current(trip_id).members}
        if e.split_method == "equal":
            if {s.member_id for s in e.splits} == everyone:
                return f"{n} 人均分"
            return "、".join(self.name(trip_id, s.member_id) for s in e.splits) + ("均分" if n > 1 else "承担")
        return {"shares": "按份数分摊", "exact": "逐人指定金额"}[e.split_method]

    def _payer_ok(self, trip_id: str, payer: str, by: str) -> bool:
        """付款人本人或发起人记的账可以直接入账；替别人记的，要付款人（或发起人）确认。"""
        member = self.service.current(trip_id).member(by)
        return by == payer or (member is not None and member.role in CAN_EDIT_ANY)

    def add(self, trip_id: str, data: ExpenseIn, by: str) -> Expense:
        existing = self.store.expenses(trip_id)
        if data.status == "confirmed" and not self._payer_ok(trip_id, data.payer, by):
            data = data.model_copy(update={"status": "draft"})
        e = self.build(trip_id, data, by, expense_id=f"x{len(existing) + 1:03d}")
        while any(x.id == e.id for x in existing):
            e = e.model_copy(update={"id": f"x{int(e.id[1:]) + 1:03d}"})
        self.store.save_expense(trip_id, e)
        verb = "记了一笔草稿" if e.status == "draft" else "记了一笔"
        self.service.log(trip_id, by, "expense",
                         f"{self.name(trip_id, by)} {verb}：{e.title} {fmt(e.amount, e.currency)}，{self._split_text(trip_id, e)}", [e.id])
        return e

    def _can_edit(self, trip_id: str, e: Expense, by: str) -> None:
        member = self.service.current(trip_id).member(by)
        if member is None or (by not in (e.created_by, e.payer) and member.role not in CAN_EDIT_ANY):
            raise ServiceError("只有记账人、付款人或发起人能修改这笔账")

    def update(self, trip_id: str, expense_id: str, data: ExpenseIn, by: str) -> Expense:
        old = self.expense(trip_id, expense_id)
        self._can_edit(trip_id, old, by)
        new = self.build(trip_id, data, by, expense_id=old.id, old=old)
        money_changed = any(getattr(old, f) != getattr(new, f) for f in ("amount", "currency", "payer", "settle_amount",
                                                                         "split_method", "fx", "splits"))
        if not self._payer_ok(trip_id, new.payer, by) and (old.status == "draft" or money_changed):
            new = new.model_copy(update={"status": "draft"})   # 草稿还是草稿；别人改了金额、分摊也回到草稿，等付款人确认
        changes = []
        for field, label in (("title", "名称"), ("amount", "金额"), ("currency", "币种"), ("payer", "付款人"),
                             ("date", "日期"), ("settle_amount", "折算金额"), ("category", "类别")):
            a, b = getattr(old, field), getattr(new, field)
            if a != b:
                if field == "payer":
                    a, b = self.name(trip_id, a), self.name(trip_id, b)
                changes.append(f"{label} {a} → {b}")
        if [s.model_dump() for s in old.splits] != [s.model_dump() for s in new.splits] or old.split_method != new.split_method:
            changes.append(f"分摊 → {self._split_text(trip_id, new)}")
        if old.fx.method != new.fx.method:
            changes.append("折算方式 → " + {"same": "同币种", "charged": "实际扣款", "cash": "现金按汇率", "estimate": "暂估"}[new.fx.method])
        summary = "；".join(changes) or "没有变化"
        new.history.append(ExpenseChange(at=_now(), by=by, summary=summary))
        self.store.save_expense(trip_id, new)
        self.service.log(trip_id, by, "expense", f"{self.name(trip_id, by)} 修改了「{new.title}」：{summary}", [new.id])
        return new

    def set_actual(self, trip_id: str, expense_id: str, charged_amount: Decimal, by: str,
                   charged_currency: str | None = None) -> Expense:
        """暂估的账出账后，补填实际扣款金额（14.5 节）。"""
        e = self.expense(trip_id, expense_id)
        data = self.to_input(e).model_copy(update={"fx_method": "charged", "charged_amount": charged_amount,
                                                     "charged_currency": charged_currency})
        return self.update(trip_id, expense_id, data, by)

    def to_input(self, e: Expense) -> ExpenseIn:
        return ExpenseIn(title=e.title, category=e.category, payer=e.payer, amount=e.amount, currency=e.currency,
                         date=e.date, split_method=e.split_method, splits=e.splits,
                         fx_method="auto" if e.fx.method == "same" else e.fx.method,
                         charged_amount=e.fx.charged_amount, charged_currency=e.fx.charged_currency, item_id=e.item_id,
                         evidence_id=e.evidence_id, refund_of=e.refund_of, note=e.note,
                         status="draft" if e.status == "draft" else "confirmed")

    def confirm(self, trip_id: str, expense_id: str, by: str) -> Expense:
        """草稿入账，或者把"有疑问"的账恢复正常。"""
        e = self.expense(trip_id, expense_id)
        self._can_edit(trip_id, e, by)
        if e.status in ("draft", "disputed") and not self._payer_ok(trip_id, e.payer, by):
            payer = self.name(trip_id, e.payer)
            what = "确认入账" if e.status == "draft" else "处理疑问"
            raise ServiceError(f"这笔账写的是 {payer} 付的，要 {payer} 本人或发起人{what}")
        was = e.status
        e = e.model_copy(update={"status": "confirmed", "dispute_note": ""})
        e.history.append(ExpenseChange(at=_now(), by=by, summary="入账" if was == "draft" else "疑问已解决"))
        self.store.save_expense(trip_id, e)
        self.service.log(trip_id, by, "expense",
                         f"{self.name(trip_id, by)} {'确认入账' if was == 'draft' else '解决了疑问'}：{e.title} {fmt(e.amount, e.currency)}", [e.id])
        return e

    def dispute(self, trip_id: str, expense_id: str, by: str, note: str) -> Expense:
        """任何成员都可以把一笔账标成"有疑问"并留言（14.9 节）。"""
        if self.service.current(trip_id).member(by) is None:
            raise ServiceError(f"行程里没有成员 {by}")
        e = self.expense(trip_id, expense_id)
        if e.status == "draft":
            raise ServiceError("草稿还没入账，直接修改或删除就好")
        e = e.model_copy(update={"status": "disputed", "dispute_note": note.strip() or "有疑问"})
        e.history.append(ExpenseChange(at=_now(), by=by, summary=f"标成有疑问：{e.dispute_note}"))
        self.store.save_expense(trip_id, e)
        self.service.log(trip_id, by, "expense", f"{self.name(trip_id, by)} 对「{e.title}」有疑问：{e.dispute_note}", [e.id])
        return e

    def delete(self, trip_id: str, expense_id: str, by: str) -> None:
        e = self.expense(trip_id, expense_id)
        self._can_edit(trip_id, e, by)
        self.store.delete_expense(trip_id, expense_id)
        self.service.log(trip_id, by, "expense", f"{self.name(trip_id, by)} 删除了一笔账：{e.title} {fmt(e.amount, e.currency)}", [e.id])

    # ---------- 还款、结算单位 ----------

    def add_transfer(self, trip_id: str, from_member: str, to_member: str, amount: Decimal, by: str, note: str = "") -> Transfer:
        members = {m.id for m in self.service.current(trip_id).members}
        if from_member not in members or to_member not in members or from_member == to_member:
            raise ServiceError("付款人和收款人要是两个不同的行程成员")
        if by not in (from_member, to_member):
            raise ServiceError("只有付款或收款的人能记录这笔还款")
        if amount <= 0:
            raise ServiceError("还款金额要大于 0")
        existing = self.store.transfers(trip_id)
        t = Transfer(id=f"t{len(existing) + 1:03d}", from_member=from_member, to_member=to_member, amount=cents(amount),
                     created_at=_now(), note=note.strip(),
                     status="confirmed" if by == to_member else "sent", confirmed_at=_now() if by == to_member else None)
        self.store.save_transfer(trip_id, t)
        cur = self.settle_currency(trip_id)
        done = "，已确认收到" if t.status == "confirmed" else f"，等 {self.name(trip_id, to_member)} 确认收到"
        self.service.log(trip_id, by, "transfer",
                         f"{self.name(trip_id, from_member)} → {self.name(trip_id, to_member)} 还款 {fmt(t.amount, cur)}{done}", [t.id])
        return t

    def confirm_transfer(self, trip_id: str, transfer_id: str, by: str) -> Transfer:
        t = next((x for x in self.store.transfers(trip_id) if x.id == transfer_id), None)
        if t is None:
            raise ServiceError(f"找不到还款 {transfer_id}")
        if by != t.to_member:
            raise ServiceError("要由收款的人确认收到")
        t = t.model_copy(update={"status": "confirmed", "confirmed_at": _now()})
        self.store.save_transfer(trip_id, t)
        self.service.log(trip_id, by, "transfer",
                         f"{self.name(trip_id, by)} 确认收到 {self.name(trip_id, t.from_member)} 的还款 "
                         f"{fmt(t.amount, self.settle_currency(trip_id))}", [t.id])
        return t

    def delete_transfer(self, trip_id: str, transfer_id: str, by: str) -> None:
        t = next((x for x in self.store.transfers(trip_id) if x.id == transfer_id), None)
        if t is None:
            raise ServiceError(f"找不到还款 {transfer_id}")
        if by not in (t.from_member, t.to_member):
            raise ServiceError("只有付款或收款的人能撤销这笔还款")
        self.store.delete_transfer(trip_id, transfer_id)

    def set_units(self, trip_id: str, units: list[list[str]], by: str) -> MoneySettings:
        """结算单位，例如情侣合并成一方结算。"""
        trip = self.service.current(trip_id)
        member = trip.member(by)
        if member is None or member.role not in ("owner", "editor"):
            raise ServiceError("只有发起人或编辑者能设置合并结算")
        members = {m.id for m in trip.members}
        seen: set[str] = set()
        clean = []
        for unit in units:
            unit = [m for m in unit if m in members]
            if len(unit) < 2:
                continue
            if seen & set(unit):
                raise ServiceError("一个人只能在一个结算单位里")
            seen |= set(unit)
            clean.append(unit)
        settings = MoneySettings(units=clean)
        self.store.save_money_settings(trip_id, settings)
        text = "；".join("、".join(self.name(trip_id, m) for m in u) for u in clean) or "取消合并"
        self.service.log(trip_id, by, "expense", f"{self.name(trip_id, by)} 设置了合并结算：{text}")
        return settings

    def seed_units(self, trip_id: str, couples: list[list[str]], names_to_ids: dict[str, str]) -> None:
        """从信息清单带入情侣合并结算（清单里写的是称呼）。"""
        units = [[names_to_ids[n] for n in pair if n in names_to_ids] for pair in couples]
        units = [u for u in units if len(u) >= 2]
        if units:
            self.store.save_money_settings(trip_id, MoneySettings(units=units))

    # ---------- 校验和汇总 ----------

    def checks(self, trip_id: str) -> list[dict]:
        """E04 疑似重复、E05 分摊人和事件参加人不一致、E07 还有暂估；另外提示草稿和有疑问的账。"""
        trip = self.service.current(trip_id)
        rows = self.expenses(trip_id)
        out = []
        seen: dict[tuple, Expense] = {}
        for e in rows:
            key_ev = ("ev", e.evidence_id) if e.evidence_id else None
            key_same = ("same", e.payer, e.amount, e.currency, e.date)
            for key in filter(None, (key_ev, key_same)):
                if key in seen:
                    out.append({"code": "E04", "severity": "warning", "expense_ids": [seen[key].id, e.id],
                                "message": f"「{e.title}」可能和「{seen[key].title}」重复记了"})
                    break
                seen[key] = e
            if e.item_id and (item := trip.find_item(e.item_id)):
                who = set(trip.participants_of(item))
                if {s.member_id for s in e.splits} != who:
                    out.append({"code": "E05", "severity": "warning", "expense_ids": [e.id],
                                "message": f"「{e.title}」的分摊人和「{item.title}」的参加人不一致"})
        estimates = [e for e in rows if e.estimated and e.status != "draft"]
        if estimates:
            who = "、".join(dict.fromkeys(self.name(trip_id, e.payer) for e in estimates))
            out.append({"code": "E07", "severity": "warning", "expense_ids": [e.id for e in estimates],
                        "message": f"还有 {len(estimates)} 笔是按汇率暂估的，出账后请 {who} 补填实际扣款金额"})
        drafts = [e for e in rows if e.status == "draft"]
        if drafts:
            out.append({"code": "DRAFT", "severity": "info", "expense_ids": [e.id for e in drafts],
                        "message": f"有 {len(drafts)} 笔草稿还没入账，确认后才算进结算"})
        disputed = [e for e in rows if e.status == "disputed"]
        if disputed:
            out.append({"code": "DISPUTED", "severity": "warning", "expense_ids": [e.id for e in disputed],
                        "message": f"有 {len(disputed)} 笔账有疑问，结算前先说清楚"})
        return out

    def summary(self, trip_id: str) -> dict:
        trip = self.service.current(trip_id)
        cur = trip.meta.settle_currency
        member_ids = [m.id for m in trip.members]
        rows = self.expenses(trip_id)
        transfers = self.store.transfers(trip_id)
        units = self.store.money_settings(trip_id).units
        result = settle(member_ids, rows, transfers, units)
        names = {m.id: m.name for m in trip.members}

        def unit_name(unit: tuple[str, ...]) -> str:
            return "和".join(names.get(m, m) for m in unit)

        plan = [{"from": list(f), "to": list(t), "from_name": unit_name(f), "to_name": unit_name(t),
                 "pay_member": f[0], "receive_member": t[0], "amount": str(a), "text": f"{unit_name(f)} → {unit_name(t)} {fmt(a, cur)}"}
                for f, t, a in result.plan]
        items = {i.id: i.title for i in trip.all_items()}
        expense_views = []
        for e in rows:
            parts = shares_of(e)
            expense_views.append({
                **e.model_dump(mode="json"), "category_label": CATEGORY_LABEL[e.category],
                "payer_name": names.get(e.payer, e.payer), "created_by_name": self.name(trip_id, e.created_by),
                "amount_text": fmt(e.amount, e.currency), "settle_text": fmt(e.settle_amount, cur),
                "split_text": self._split_text(trip_id, e), "item_title": items.get(e.item_id or ""),
                "shares": [{"member_id": m, "name": names.get(m, m), "amount": str(a), "text": fmt(a, cur)} for m, a in parts.items()],
            })
        by_cat: dict[str, Decimal] = {}
        for e in rows:
            if e.status != "draft":
                by_cat[e.category] = by_cat.get(e.category, Decimal(0)) + e.settle_amount
        balances = [{"member_id": b.member_id, "name": names.get(b.member_id, b.member_id),
                     "paid": str(cents(b.paid)), "owed": str(cents(b.owed)), "net": str(cents(b.net)),
                     "paid_text": fmt(b.paid, cur), "owed_text": fmt(b.owed, cur),
                     "net_text": ("+" if b.net > 0 else "") + fmt(b.net, cur)} for b in result.balances]
        lines = [f"【{trip.meta.title}】AA 结算（{cur}）"] + [f"{p['text']}" for p in plan]
        if not plan:
            lines.append("现在大家已经两清了")
        checks = self.checks(trip_id)
        if any(c["code"] == "E07" for c in checks):
            lines.append("（有几笔是按汇率暂估的，补填实际扣款后金额会微调）")
        return {
            "settle_currency": cur, "units": units, "expenses": expense_views, "balances": balances, "plan": plan,
            "transfers": [{**t.model_dump(mode="json"), "from_name": names.get(t.from_member), "to_name": names.get(t.to_member),
                           "text": fmt(t.amount, cur)} for t in transfers],
            "checks": checks, "text": "\n".join(lines),
            "by_category": [{"category": c, "label": CATEGORY_LABEL[c], "amount": str(a), "text": fmt(a, cur)}
                            for c, a in sorted(by_cat.items(), key=lambda kv: -kv[1])],
            "total_text": fmt(sum(by_cat.values(), Decimal(0)), cur),
        }

    def csv(self, trip_id: str) -> str:
        """导出账目（Excel 能直接打开）。"""
        import csv
        import io
        s = self.summary(trip_id)
        buf = io.StringIO()
        w = _SafeWriter(csv.writer(buf))
        w.writerow(["日期", "账目", "类别", "付款人", "原币金额", "币种", f"折算（{s['settle_currency']}）", "折算方式", "分摊", "状态", "备注"])
        method = {"same": "同币种", "charged": "实际扣款", "cash": "现金按汇率", "estimate": "暂估"}
        status = {"draft": "草稿", "confirmed": "已入账", "disputed": "有疑问"}
        for e in s["expenses"]:
            w.writerow([e["date"], e["title"], e["category_label"], e["payer_name"], e["amount"], e["currency"], e["settle_amount"],
                        method[e["fx"]["method"]], "；".join(f"{x['name']} {x['text']}" for x in e["shares"]),
                        status[e["status"]], e["note"]])
        w.writerow([])
        w.writerow(["成员", "付了", "应承担", "净额"])
        for b in s["balances"]:
            w.writerow([b["name"], b["paid"], b["owed"], b["net"]])
        w.writerow([])
        w.writerow(["结算方案"])
        for p in s["plan"]:
            w.writerow([p["text"]])
        return "﻿" + buf.getvalue()   # 带 BOM，Excel 打开中文不乱码


class _SafeWriter:
    """Excel 会把 = + - @ 开头的格子当公式执行：成员写的文字前面加一个单引号。数字照常写。"""

    def __init__(self, writer):
        self.writer = writer

    def writerow(self, row):
        self.writer.writerow([v if not isinstance(v, str) or not v[:1] in ("=", "+", "-", "@", "\t", "\r") or _is_number(v)
                              else "'" + v for v in row])


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
        return True
    except (InvalidOperation, ValueError):
        return False


def parse_decimal(text) -> Decimal:
    try:
        return Decimal(str(text).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ServiceError(f"金额看不懂：{text}") from exc
