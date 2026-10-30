"""多人协作的数据结构（设计规格第 13 节）：每人的偏好、意见汇总、投票、动态。"""

import datetime as dt
from typing import Literal

from pydantic import AwareDatetime, Field

from .common import Model, Money
from .intake import MustDo, Short

Pace = Literal["relaxed", "balanced", "packed"]
PACE_LABEL = {"relaxed": "悠闲", "balanced": "适中", "packed": "排满"}
THRILL_LABEL = {"low": "低", "mid": "中", "high": "高"}


# ---------- 每人的偏好 ----------

class PreferenceForm(Model):
    """每个人自己填的偏好（设计规格 4.2 节里和玩法有关的部分）。都可以不填。"""

    must_do: list[MustDo] = Field(default_factory=list, max_length=20)
    wishlist: list[Short] = Field(default_factory=list, max_length=30)   # 想去的地方、想玩的项目，可以是小红书链接
    avoid: list[Short] = Field(default_factory=list, max_length=30)      # 不想玩或不敢玩的，例如怕高、晕船
    thrill_level: Literal["low", "mid", "high"] | None = None
    pace: Pace | None = None
    max_daily_drive_hours: float | None = Field(default=None, gt=0, le=14)
    earliest_start: dt.time | None = None               # 早上最早几点出发
    budget_per_person: Money | None = None
    diet: list[Short] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)

    def is_empty(self) -> bool:
        """一项都没填。"""
        return all(getattr(self, name) == info.get_default(call_default_factory=True)
                   for name, info in PreferenceForm.model_fields.items())


class MemberPreferences(PreferenceForm):
    member_id: str
    updated_at: AwareDatetime


# ---------- 意见汇总 ----------

class Position(Model):
    member_ids: list[str]
    stance: str                      # 例如"想去""怕高，不想去""想悠闲"


class PollOptionDraft(Model):
    label: str
    impact: str = ""                 # 选这个会怎样，例如"多开 2 小时车，少去一个景点"


class PollDraft(Model):
    question: str
    options: list[PollOptionDraft]   # 至少两个；模型给的不够时由代码丢掉这个建议（结构化输出不支持 minItems）


class SummaryItem(Model):
    topic: str
    detail: str
    member_ids: list[str] = Field(default_factory=list)


class Divergence(Model):
    id: str
    topic: str
    kind: Literal["activity", "pace", "drive", "start", "budget", "thrill", "other"]
    detail: str
    positions: list[Position] = Field(default_factory=list)
    suggestion: str = ""
    poll: PollDraft | None = None    # 建议的投票，点一下就能发起
    poll_id: str | None = None       # 已经发起的投票


class GroupOption(Model):
    """可以分组：一部分人去玩，其他人同时做别的，代价是多一套交通安排。"""

    topic: str
    detail: str
    member_ids: list[str] = Field(default_factory=list)  # 想去的人
    others: str = ""                                     # 其他人可以做什么


class PreferenceSummary(Model):
    created_at: AwareDatetime
    created_by: str | None = None
    based_on: dict[str, AwareDatetime] = Field(default_factory=dict)  # 汇总时用的是每人哪一次的偏好
    consensus: list[SummaryItem] = Field(default_factory=list)
    divergences: list[Divergence] = Field(default_factory=list)
    groupable: list[GroupOption] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)                  # 还没填偏好的成员
    decision_ids: list[str] = Field(default_factory=list)            # 由必玩项目写进决策账本的决策


# ---------- 投票 ----------

class PollOption(Model):
    id: str
    label: str
    impact: str = ""


class Poll(Model):
    id: str
    question: str
    options: list[PollOption] = Field(min_length=2)
    created_by: str | None = None    # 成员 id；助手发起的写 agent
    created_at: AwareDatetime
    deadline: AwareDatetime | None = None
    status: Literal["open", "closed"] = "open"
    votes: dict[str, str] = Field(default_factory=dict)   # 成员 id → 选项 id，投票公开，可以改票
    result: str | None = None                             # 胜出的选项 id
    decision_id: str | None = None                        # 写进决策账本的决策
    divergence_id: str | None = None
    closed_by: str | None = None
    closed_at: AwareDatetime | None = None
    note: str = ""

    def option(self, option_id: str | None) -> PollOption | None:
        return next((o for o in self.options if o.id == option_id), None)

    def tally(self) -> dict[str, list[str]]:
        """每个选项 → 投给它的成员。"""
        out: dict[str, list[str]] = {o.id: [] for o in self.options}
        for member_id, option_id in self.votes.items():
            out.setdefault(option_id, []).append(member_id)
        return out


# ---------- 动态 ----------

class ActivityEvent(Model):
    id: int | None = None            # 存储时分配，越大越新
    at: AwareDatetime
    actor: str | None = None         # 成员 id；agent 表示助手，system 表示系统
    kind: Literal["created", "version", "evidence", "decision", "preferences", "profile", "summary",
                  "poll_created", "vote", "poll_closed", "expense", "transfer", "guide"]
    text: str
    refs: list[str] = Field(default_factory=list)
