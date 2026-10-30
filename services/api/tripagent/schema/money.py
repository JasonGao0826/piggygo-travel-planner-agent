"""账目与 AA 结算的数据结构（设计规格第 14 节）。金额一律用十进制，精确到分。"""

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from .common import Model

Category = Literal["accommodation", "flight", "car", "fuel", "parking", "activity", "food", "grocery", "visa", "other"]
CATEGORY_LABEL = {
    "accommodation": "住宿", "flight": "机票", "car": "租车", "fuel": "油费", "parking": "停车", "activity": "活动",
    "food": "餐饮", "grocery": "超市", "visa": "签证与入境", "other": "其他",
}
# 这几类默认全员均分（14.4 节）
SHARED_BY_ALL = {"accommodation", "car", "fuel", "parking"}


class Split(Model):
    """一个分摊人。均分时只有 member_id；按份数时填 shares；逐人指定金额时填 amount（原币）。"""

    member_id: str
    shares: Decimal | None = None
    amount: Decimal | None = None


class Fx(Model):
    """原币怎么折算成结算币种（14.5 节）。"""

    method: Literal["same", "charged", "cash", "estimate"]
    # same：原币就是结算币种；charged：按银行实际扣款；cash：现金按当天汇率；estimate：刷卡还没出账，先按汇率暂估
    charged_amount: Decimal | None = None      # 实际扣款金额
    charged_currency: str | None = None        # 实际扣款的币种（用人民币卡付时是 CNY），默认结算币种
    rate: Decimal | None = None                # 1 单位原币 = 多少结算币种（cash / estimate 用；charged 但扣款币种不是结算币种时，是扣款币种的汇率）
    rate_date: dt.date | None = None
    source: str | None = None                  # 汇率来源，例如 Frankfurter（各国央行参考价）


class ExpenseChange(Model):
    at: AwareDatetime
    by: str
    summary: str


class Expense(Model):
    id: str
    title: str
    category: Category = "other"
    payer: str
    amount: Decimal                            # 原币金额；退款记负数
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    settle_amount: Decimal                     # 折算成结算币种后的金额
    fx: Fx
    date: dt.date
    split_method: Literal["equal", "shares", "exact"] = "equal"
    splits: list[Split]
    item_id: str | None = None
    evidence_id: str | None = None
    refund_of: str | None = None
    status: Literal["draft", "confirmed", "disputed"] = "draft"
    note: str = ""
    dispute_note: str = ""
    created_by: str
    created_at: AwareDatetime
    history: list[ExpenseChange] = Field(default_factory=list)

    @property
    def estimated(self) -> bool:
        return self.fx.method == "estimate"


class Transfer(Model):
    """一笔还款：付款方标"已转"，收款方确认后才计入余额（14.6 节第 4 条）。"""

    id: str
    from_member: str
    to_member: str
    amount: Decimal                            # 结算币种
    status: Literal["sent", "confirmed"] = "sent"
    created_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    note: str = ""


class MoneySettings(Model):
    """结算单位：例如情侣合并成一方结算（14.4 节）。没列进来的人各自一个单位。"""

    units: list[list[str]] = Field(default_factory=list)
