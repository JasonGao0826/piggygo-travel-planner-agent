"""信息清单（问卷）的数据结构（设计规格第 4 节）。用户填的原始信息，agent 在 S0 阶段读取和校验。"""

import datetime as dt
from typing import Annotated, Literal

from pydantic import Field

from .common import Model, Money

# 用户填的文字都有长度和个数上限：这些内容会交给模型看，太长既花钱也可能被用来塞指令
Short = Annotated[str, Field(max_length=200)]


class MustDo(Model):
    """一个必玩项目和它的三个标记（设计规格 4.4 节）。"""

    what: str = Field(max_length=100)   # 用户原话，例如"骑马"
    activity_tag: str | None = Field(default=None, max_length=60)   # agent 归一化后的项目类型，例如 horse_riding
    place_hint: str | None = Field(default=None, max_length=100)    # 例如"格林诺奇"
    date: dt.date | None = None
    fixed_activity: bool = True         # 必须玩这个项目
    fixed_place: bool = False           # 必须在这个地点
    fixed_date: bool = False            # 必须在这天


class MemberIntake(Model):
    name: str = Field(max_length=40)
    passport_country: str = Field(max_length=60)
    visa: str | None = Field(default=None, max_length=100)
    can_drive: bool = False
    age: int | None = None
    licence_country: str | None = Field(default=None, max_length=60)
    licence_type: str | None = Field(default=None, max_length=60)
    must_do: list[MustDo] = Field(default_factory=list, max_length=20)
    wishlist: list[Short] = Field(default_factory=list, max_length=30)  # 想去的地方，或小红书链接
    avoid: list[Short] = Field(default_factory=list, max_length=30)     # 不想玩或不敢玩的
    thrill_level: Literal["low", "mid", "high"] | None = None
    weight_kg: float | None = None
    diet: list[Short] = Field(default_factory=list, max_length=20)
    large_luggage: int | None = None
    payment_note: str | None = Field(default=None, max_length=200)     # 收款方式备注，只显示给同行成员


class ExistingBooking(Model):
    kind: Literal["flight", "accommodation", "car", "activity", "other"]
    description: str = Field(max_length=500)
    file: str | None = Field(default=None, max_length=200)


class TripIntake(Model):
    destination: str = Field(max_length=10)             # NZ / AU
    regions: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)  # 例如 ["南岛", "北岛"]，为空表示请推荐
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    month: str | None = Field(default=None, max_length=10)   # 没有具体日期时，例如 "2026-11"
    days: int | None = None
    origin_city: str = Field(max_length=60)
    party: str = Field(default="", max_length=300)      # 成员关系描述
    couples: list[Annotated[list[Annotated[str, Field(max_length=40)]], Field(max_length=2)]] = Field(default_factory=list, max_length=6)
    transport: Literal["self_drive", "public", "tour", "mixed"]
    budget_per_person: Money | None = None
    room_budget_per_night: Money | None = None
    settle_currency: str = "AUD"
    accommodation_type: Literal["whole_home", "hotel", "any"] = "any"
    room_requirements: str = Field(default="", max_length=300)
    need_parking: bool = False
    max_daily_drive_hours: float | None = None
    earliest_start: dt.time | None = None
    pace: Literal["relaxed", "balanced", "packed"] | None = None
    existing_bookings: list[ExistingBooking] = Field(default_factory=list, max_length=30)
    output_format: Literal["word", "pdf"] = "word"
    members: list[MemberIntake] = Field(max_length=12)
