"""结算计算（设计规格 14.6 节）：每人付了多少、应承担多少、净额，以及转账次数最少的方案。

全部由代码完成，模型只读取结果。用十进制精确到分；除不尽的零头按固定规则分配，保证分摊合计正好等于原金额。
"""

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from ..schema.money import Expense, Transfer

CENT = Decimal("0.01")


def cents(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """把 total（已到分）按权重分成几份，每份到分，合计正好等于 total。

    先按比例向零取整到分，剩下的零头一分一分地给小数部分最大的人；一样大时给排在前面的人（固定规则，结果可复现）。
    """
    if not weights:
        return []
    total = cents(total)
    weight_sum = sum(weights)
    if weight_sum == 0:
        raise ValueError("分摊的份数加起来是 0")
    sign = Decimal(-1) if total < 0 else Decimal(1)
    raw = [abs(total) * w / weight_sum for w in weights]
    base = [r.quantize(CENT, rounding=ROUND_DOWN) for r in raw]
    left = int((abs(total) - sum(base)) / CENT)
    order = sorted(range(len(weights)), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:left]:
        base[i] += CENT
    return [sign * b for b in base]


def shares_of(expense: Expense) -> dict[str, Decimal]:
    """这笔账每个分摊人应承担的结算币种金额。"""
    members = [s.member_id for s in expense.splits]
    if expense.split_method == "equal":
        weights = [Decimal(1)] * len(members)
    elif expense.split_method == "shares":
        weights = [s.shares or Decimal(0) for s in expense.splits]
    else:  # exact：逐人指定的是原币金额，按比例换成结算币种
        weights = [s.amount or Decimal(0) for s in expense.splits]
    out: dict[str, Decimal] = {}
    for m, part in zip(members, allocate(expense.settle_amount, weights)):
        out[m] = out.get(m, Decimal(0)) + part
    return out


@dataclass
class Balance:
    member_id: str
    paid: Decimal = Decimal(0)      # 他付出的（含已确认的还款）
    owed: Decimal = Decimal(0)      # 他应承担的（含收到的还款）

    @property
    def net(self) -> Decimal:
        return self.paid - self.owed


@dataclass
class Settlement:
    balances: list[Balance]
    plan: list[tuple[tuple[str, ...], tuple[str, ...], Decimal]]   # (付款的结算单位, 收款的结算单位, 金额)
    unit_of: dict[str, tuple[str, ...]] = field(default_factory=dict)


def balances(member_ids: list[str], expenses: list[Expense], transfers: list[Transfer]) -> list[Balance]:
    """草稿不算；有疑问的照算（页面上会提示）。还款只算收款方已确认的。"""
    rows = {m: Balance(m) for m in member_ids}
    for e in expenses:
        if e.status == "draft":
            continue
        rows.setdefault(e.payer, Balance(e.payer)).paid += e.settle_amount
        for m, part in shares_of(e).items():
            rows.setdefault(m, Balance(m)).owed += part
    for t in transfers:
        if t.status == "confirmed":
            rows.setdefault(t.from_member, Balance(t.from_member)).paid += t.amount
            rows.setdefault(t.to_member, Balance(t.to_member)).owed += t.amount
    total = sum((b.net for b in rows.values()), Decimal(0))
    if total != 0:  # E06 程序自检
        raise AssertionError(f"净额合计不为 0（{total}），账目计算有误")
    return list(rows.values())


def settle(member_ids: list[str], expenses: list[Expense], transfers: list[Transfer],
           units: list[list[str]] = ()) -> Settlement:
    """先算每人净额，再按结算单位合并，最后每次让欠得最多的转给被欠得最多的，直到清零（最多 n−1 笔）。"""
    rows = balances(member_ids, expenses, transfers)
    unit_of: dict[str, tuple[str, ...]] = {}
    for unit in units:
        members = tuple(m for m in unit if m in member_ids)
        for m in members:
            unit_of[m] = members
    for b in rows:
        unit_of.setdefault(b.member_id, (b.member_id,))
    nets: dict[tuple[str, ...], Decimal] = {}
    for b in rows:
        nets[unit_of[b.member_id]] = nets.get(unit_of[b.member_id], Decimal(0)) + b.net
    order = {u: i for i, u in enumerate(dict.fromkeys(unit_of[m] for m in member_ids))}
    debtors = [[u, -n] for u, n in nets.items() if n < 0]
    creditors = [[u, n] for u, n in nets.items() if n > 0]
    plan = []
    while debtors and creditors:
        debtors.sort(key=lambda x: (-x[1], order.get(x[0], 99)))
        creditors.sort(key=lambda x: (-x[1], order.get(x[0], 99)))
        d, c = debtors[0], creditors[0]
        pay = min(d[1], c[1])
        plan.append((d[0], c[0], pay))
        d[1] -= pay
        c[1] -= pay
        debtors = [x for x in debtors if x[1] > 0]
        creditors = [x for x in creditors if x[1] > 0]
    return Settlement(balances=rows, plan=plan, unit_of=unit_of)
