"""记忆层的数据结构（设计规格第 8 节）：决策账本、证据、事实缓存、改动提案、版本、校验问题。"""

import datetime as dt
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field

from .common import Model
from .trip import Trip


# ---------- 决策账本 ----------

class DecisionKind(StrEnum):
    HARD_CONSTRAINT = "hard_constraint"
    PREFERENCE = "preference"
    DECISION = "decision"
    REJECTION = "rejection"


class Priority(StrEnum):
    MUST = "must"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class Requirement(Model):
    """必玩项目的三个标记（设计规格 4.4 节）：填了哪项，哪项就不能换。"""

    activity_tag: str | None = None  # 必须玩这个项目，例如 horse_riding
    place_id: str | None = None      # 必须是这个具体地点 / 商家
    city: str | None = None          # 必须在这个地方，例如 Glenorchy
    date: dt.date | None = None      # 必须在这天


class RejectedOption(Model):
    """被否决的方案。place_id 或"项目类型 + 地方"任一匹配就算命中。"""

    description: str
    place_id: str | None = None
    activity_tag: str | None = None
    city: str | None = None


class Decision(Model):
    id: str
    content: str
    kind: DecisionKind
    priority: Priority = Priority.NORMAL
    scope: Literal["trip", "day", "item", "member"] = "trip"
    refs: list[str] = Field(default_factory=list)
    proposed_by: str | None = None
    agreed_by: list[str] = Field(default_factory=list)
    status: DecisionStatus = DecisionStatus.ACTIVE
    supersedes: str | None = None
    rationale: str | None = None
    requirement: Requirement | None = None
    rejected_options: list[RejectedOption] = Field(default_factory=list)
    source_message_id: str | None = None
    created_at: AwareDatetime | None = None


# ---------- 证据与事实 ----------

class Evidence(Model):
    """一份预订凭证（凭证夹里的一条）。原文件存在服务器上，所有成员都能查看和下载。"""

    id: str
    kind: Literal["flight", "accommodation", "car", "activity", "other"]
    file: str | None = None                  # 说明文字（对话里记的凭证没有原文件时用）
    fields: dict[str, Any] = Field(default_factory=dict)   # 识别出、用户核对过的字段
    confirmed: bool = False
    uploaded_by: str | None = None
    item_ids: list[str] = Field(default_factory=list)
    title: str | None = None                 # 显示名，例如"Skyline 缆车＋斜坡滑车 入场票"
    date: dt.date | None = None              # 凭证对应的日期（排序、打包命名用）
    filename: str | None = None              # 原文件名
    stored_as: str | None = None             # 服务器上的存放位置（相对上传目录）
    mime: str | None = None
    size: int | None = None
    uploaded_at: AwareDatetime | None = None
    notes: str = ""


class Fact(Model):
    place_id: str
    field: str
    value: Any
    valid_from: str | None = None
    valid_to: str | None = None
    source_url: str | None = None
    verified_at: dt.date
    expires_at: dt.date | None = None
    confidence: Literal["official", "third_party", "guide", "inferred"] = "official"


# ---------- 校验结果 ----------

class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Issue(Model):
    code: str
    severity: Severity
    message: str
    item_ids: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    # 涉及某个成员隐私（体重）的问题：详情只给本人看，其他人看到 public_message
    private_to: str | None = None
    public_message: str | None = None

    def view(self, viewer: str | None) -> dict:
        """给某个成员看的样子。viewer 为 None 表示不区分（本地开发）。"""
        data = self.model_dump(mode="json", exclude={"private_to", "public_message"})
        if self.private_to and viewer is not None and viewer != self.private_to:
            data["message"] = self.public_message or "有同行成员不符合这个项目的要求（详情只有本人能看到）"
        return data


# ---------- 改动提案 ----------
# 改动用 id 定位，而不是 JSON Patch 的数组下标：下标会随增删移动，模型很容易写错位置。

Collection = Literal["items", "accommodations", "vehicles", "todos"]


class AddOp(Model):
    op: Literal["add"]
    collection: Collection
    value: dict[str, Any]


class UpdateOp(Model):
    op: Literal["update"]
    collection: Collection
    id: str
    set: dict[str, Any]


class RemoveOp(Model):
    op: Literal["remove"]
    collection: Collection
    id: str


class UpdateDayOp(Model):
    op: Literal["update_day"]
    date: dt.date
    set: dict[str, Any]


class UpdateMetaOp(Model):
    op: Literal["update_meta"]
    set: dict[str, Any]


PatchOp = Annotated[AddOp | UpdateOp | RemoveOp | UpdateDayOp | UpdateMetaOp, Field(discriminator="op")]


class FieldChange(Model):
    field: str
    old: Any = None
    new: Any = None


class Change(Model):
    """一条可读的差异，用于给用户确认和写版本说明。"""

    collection: str
    id: str
    kind: Literal["added", "removed", "changed"]
    fields: list[FieldChange] = Field(default_factory=list)


class PatchStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    CONFLICT = "conflict"


class Patch(Model):
    id: str
    trip_id: str
    base_version: int
    ops: list[PatchOp]
    summary: str
    reason: str = ""
    author: str = "agent"
    decision_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    unlock_item_ids: list[str] = Field(default_factory=list)  # 用户明确同意改动的锁定项
    status: PatchStatus = PatchStatus.PROPOSED
    issues: list[Issue] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    confirmed_by: str | None = None

    @property
    def has_errors(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)


class Version(Model):
    trip_id: str
    version: int
    state: Trip
    patch_id: str | None = None
    changelog: list[str] = Field(default_factory=list)
