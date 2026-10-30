"""回放测试的数据结构（设计规格 18.1 节 T2、附录 A）。

回放文件描述"用户依次说了什么、上传了什么"，以及每一步之后行程和决策账本应该满足的条件。
第 1 阶段先把数据定下来并保证格式正确；接入模型之后，由评测程序逐步喂给 agent 并检查。
"""

import datetime as dt
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field

from .schema.common import Model
from .schema.intake import TripIntake
from .schema.memory import RejectedOption, Requirement


class MessageInput(Model):
    kind: Literal["message"]
    member: str
    text: str


class UploadInput(Model):
    kind: Literal["upload"]
    member: str
    evidence_kind: Literal["flight", "accommodation", "car", "activity", "other"]
    description: str
    fields: dict[str, Any] = Field(default_factory=dict)  # 模拟凭证解析出来的字段


StepInput = Annotated[MessageInput | UploadInput, Field(discriminator="kind")]


class ItemMatch(Model):
    place_id: str | None = None
    activity_tag: str | None = None
    city: str | None = None
    date: dt.date | None = None


class DecisionCheck(Model):
    check: Literal["decision"]
    kind: Literal["hard_constraint", "preference", "decision", "rejection"]
    requirement: Requirement | None = None
    rejected: RejectedOption | None = None
    supersedes_about: str | None = None   # 被取代的旧决策内容里应包含的关键词


class ItemCheck(Model):
    check: Literal["item"]
    match: ItemMatch
    start: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    locked: bool | None = None
    booking_status: str | None = None


class NoItemCheck(Model):
    check: Literal["no_item"]
    match: ItemMatch
    persistent: bool = True               # 之后每一步都要继续满足


class TodoCheck(Model):
    check: Literal["todo"]
    kind: str
    owner: str | None = None
    title_contains: list[str] = Field(default_factory=list)


class OnlyDaysChangedCheck(Model):
    check: Literal["only_days_changed"]
    dates: list[dt.date]


class WarningCheck(Model):
    check: Literal["warning"]
    code: str


class AnswerCheck(Model):
    check: Literal["answer_mentions"]
    all_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)


class ConsistentValueCheck(Model):
    check: Literal["consistent_everywhere"]
    item: ItemMatch
    value: str                            # 报告所有章节里这个事件都应显示这个值
    forbidden: list[str] = Field(default_factory=list)


Check = Annotated[
    DecisionCheck | ItemCheck | NoItemCheck | TodoCheck | OnlyDaysChangedCheck | WarningCheck | AnswerCheck
    | ConsistentValueCheck,
    Field(discriminator="check"),
]


class ReplayStep(Model):
    step: int
    note: str = ""
    input: StepInput
    expect: list[Check] = Field(default_factory=list)


class ReplayCase(Model):
    id: str
    title: str
    intake: TripIntake
    every_step: list[str] = Field(default_factory=list)  # 每一步之后都要满足的通用检查
    steps: list[ReplayStep]


def load_replay(path: str | Path) -> ReplayCase:
    return ReplayCase.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
