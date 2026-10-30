"""景点知识库的数据结构（设计规格第 16 节）。"""

from datetime import date, time
from typing import Literal

from pydantic import Field, field_validator

from .common import Model, Money

PlaceKind = Literal[
    "activity",       # 需要参加的项目：滑翔伞、游船、骑马……
    "attraction",     # 景点：地热公园、教堂……
    "trailhead",      # 徒步起点
    "viewpoint",      # 观景点、拍照点
    "town",           # 城镇（加油、吃饭、休息）
    "restaurant",
    "airport",
    "accommodation",
    "other",
]


class OpeningRule(Model):
    """一条营业时间规则。from_md / to_md 是"月-日"，可以跨年（例如 11-01 到 03-31）。"""

    season: str | None = None
    from_md: str | None = Field(default=None, pattern=r"^\d{2}-\d{2}$")
    to_md: str | None = Field(default=None, pattern=r"^\d{2}-\d{2}$")
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))  # 0 = 周一
    open: time
    close: time
    last_entry: time | None = None

    def applies_to(self, day: date) -> bool:
        if day.weekday() not in self.weekdays:
            return False
        if self.from_md is None or self.to_md is None:
            return True
        md = day.strftime("%m-%d")
        if self.from_md <= self.to_md:
            return self.from_md <= md <= self.to_md
        return md >= self.from_md or md <= self.to_md


class Restriction(Model):
    """参加限制。code 用于校验器判断（例如 max_weight_kg），text 用于报告展示。"""

    code: Literal[
        "max_weight_kg", "min_weight_kg", "min_age", "max_age", "min_height_cm",
        "id_document", "no_photo", "english_required", "fitness", "other",
    ]
    value: float | str | None = None
    text: str


class BookingInfo(Model):
    required: bool = False
    lead_days: int | None = None  # 提前多少天开放预约，例如小鹿公园 7 天
    url: str | None = None
    notes: str | None = None


class PriceInfo(Model):
    label: str
    money: Money


class Place(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name_zh: str
    name_en: str
    kind: PlaceKind
    region: str                 # 例如"南岛""北岛"
    city: str | None = None     # 英文城镇名，用于"必须在这个地点"的匹配，例如 Glenorchy
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    tags: list[str] = Field(default_factory=list)  # 项目类型，例如 horse_riding、paragliding
    always_open: bool = False
    hours: list[OpeningRule] = Field(default_factory=list)  # 为空且 always_open=False 表示未知
    booking: BookingInfo = Field(default_factory=BookingInfo)
    prices: list[PriceInfo] = Field(default_factory=list)
    duration_min: int | None = None
    duration_max: int | None = None
    checkin_before_min: int | None = None
    restrictions: list[Restriction] = Field(default_factory=list)
    weather_dependent: bool = False
    official_url: str | None = None
    guide_links: list[str] = Field(default_factory=list)
    intro: str | None = None
    notes: list[str] = Field(default_factory=list)       # 给用户看的实用提示，会印进报告
    data_notes: list[str] = Field(default_factory=list)  # 内部备注：数据来源、推算方式、未核实项、某天查到的余位，不进报告
    sources: list[str] = Field(default_factory=list)
    verified_at: date | None = None

    @field_validator("tags")
    @classmethod
    def _tags_lowercase(cls, tags: list[str]) -> list[str]:
        return [t.lower() for t in tags]

    def hours_on(self, day: date) -> OpeningRule | None:
        """返回某天适用的营业时间；没有匹配规则时返回 None。"""
        for rule in self.hours:
            if rule.applies_to(day):
                return rule
        return None
