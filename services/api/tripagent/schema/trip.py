"""行程状态：整个系统的唯一真相源（设计规格第 7 节）。

报告、看板、校验都从这里读取。模型不能直接改它，只能提出改动提案（见 tripagent/patches.py）。
"""

from datetime import date, time
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .common import Model, Money, Source
from .place import Place


class ItemType(StrEnum):
    FLIGHT = "flight"
    DRIVE = "drive"
    TRANSFER = "transfer"          # 非自驾的移动：步行、接驳车、渡轮……
    ACTIVITY = "activity"
    MEAL = "meal"
    CHECKIN = "checkin"            # 住宿入住
    CHECKOUT = "checkout"          # 住宿退房
    CAR_PICKUP = "car_pickup"
    CAR_DROPOFF = "car_dropoff"
    FREE_TIME = "free_time"
    BUFFER = "buffer"              # 签到、排队、休息等缓冲


MOVEMENT_TYPES = {ItemType.FLIGHT, ItemType.DRIVE, ItemType.TRANSFER}


class BookingStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    TO_BOOK = "to_book"
    BOOKED = "booked"
    CANCELLED = "cancelled"


class LockReason(StrEnum):
    EVIDENCE = "evidence"  # 有预订凭证
    USER = "user"          # 用户要求固定


class FlightInfo(Model):
    number: str
    international: bool = False


class Item(Model):
    """行程中的一个事件。移动类事件（航班、开车、接驳）用 from/to 表示起止地点，其他事件用 place_id。"""

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    type: ItemType
    title: str
    place_id: str | None = None
    from_place_id: str | None = None
    to_place_id: str | None = None
    start: AwareDatetime
    end: AwareDatetime
    checkin_before_min: int = 0
    participants: list[str] | None = None  # None 表示全员；分组时只列参加的成员
    driver_id: str | None = None
    vehicle_id: str | None = None
    accommodation_id: str | None = None
    flight: FlightInfo | None = None
    booking_status: BookingStatus = BookingStatus.NOT_REQUIRED
    locked: bool = False
    lock_reason: LockReason | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    source: Source = Source.AGENT_INFERRED
    cost: Money | None = None
    notes: list[str] = Field(default_factory=list)
    weather_dependent: bool = False
    backup: str | None = None  # 受天气影响时的备用安排
    optional: bool = False     # 备选：时间不够、天气不好或太累时可以直接跳过（案例 V4 的做法）
    optional_reason: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "Item":
        if self.end < self.start:
            raise ValueError(f"{self.id}：结束时间早于开始时间")
        if self.type in MOVEMENT_TYPES:
            if not (self.from_place_id and self.to_place_id):
                raise ValueError(f"{self.id}：移动类事件必须有 from_place_id 和 to_place_id")
            if self.place_id is not None:
                raise ValueError(f"{self.id}：移动类事件不用 place_id")
        elif self.from_place_id or self.to_place_id:
            raise ValueError(f"{self.id}：只有移动类事件才能有 from/to 地点")
        if self.type == ItemType.FLIGHT and self.flight is None:
            raise ValueError(f"{self.id}：航班需要 flight 信息")
        if self.locked and self.lock_reason is None:
            raise ValueError(f"{self.id}：锁定的事件必须写明 lock_reason")
        return self

    @property
    def is_movement(self) -> bool:
        return self.type in MOVEMENT_TYPES

    @property
    def start_place(self) -> str | None:
        return self.from_place_id if self.is_movement else self.place_id

    @property
    def end_place(self) -> str | None:
        return self.to_place_id if self.is_movement else self.place_id


class Day(Model):
    date: date
    segment_id: str | None = None
    night_city: str | None = None  # 当晚住在哪个城市；最后一天为空
    route: str = ""                # 当天路线摘要，例如"皇后镇 → 格林诺奇 → 特威泽尔"
    items: list[Item] = Field(default_factory=list)


class Member(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    role: Literal["owner", "editor", "member"] = "member"
    passport_country: str | None = None
    visa: str | None = None
    can_drive: bool = False
    age: int | None = None
    licence_country: str | None = None  # ISO 国家代码，例如 AU、CN
    licence_type: str | None = None     # full / provisional
    weight_kg: float | None = None      # 可选，只用于校验项目限制，不展示给其他成员


class MemberProfile(Model):
    """成员本人能改的资料（加入行程后自己补充、修改）。称呼和角色不在这里：只有本人的事实才让本人改。
    请求里没写的字段保持原样。"""

    passport_country: str | None = Field(default=None, max_length=60)
    visa: str | None = Field(default=None, max_length=100)
    can_drive: bool = False
    age: int | None = Field(default=None, ge=1, le=120)
    licence_country: str | None = Field(default=None, max_length=60)
    licence_type: str | None = Field(default=None, max_length=60)
    weight_kg: float | None = Field(default=None, gt=0, le=500)

    @field_validator("passport_country", "visa", "licence_country", "licence_type", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        """去掉前后空格；空字符串当作没填。"""
        if isinstance(v, str):
            return v.strip() or None
        return v


class Segment(Model):
    id: str
    name: str
    transport: Literal["self_drive", "public", "tour", "mixed"]
    start_date: date
    end_date: date
    vehicle_id: str | None = None


class Accommodation(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    city: str
    place_id: str
    address: str | None = None
    check_in: date
    check_out: date
    earliest_checkin: time | None = None
    latest_checkout: time | None = None
    rooms: str = ""
    host: str | None = None
    beds: str | None = None              # 房型和床，例如"3 卧室：1 张大床 + 2 张单人床 + 1 张大床"（案例 V4）
    parking: bool | None = None          # 有没有停车位；None 表示还不知道
    self_checkin: str | None = None      # 入住方式，例如"门口密码箱自助入住"（具体密码不写进来）
    booking_status: BookingStatus = BookingStatus.TO_BOOK
    locked: bool = False
    lock_reason: LockReason | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    source: Source = Source.AGENT_INFERRED
    cost: Money | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_dates(self) -> "Accommodation":
        if self.check_out <= self.check_in:
            raise ValueError(f"{self.id}：退房日期必须晚于入住日期")
        return self


class Vehicle(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    company: str | None = None
    model: str | None = None
    pickup_place_id: str
    pickup_time: AwareDatetime
    dropoff_place_id: str
    dropoff_time: AwareDatetime
    drivers: list[str] = Field(default_factory=list)
    booking_status: BookingStatus = BookingStatus.TO_BOOK
    locked: bool = False
    lock_reason: LockReason | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    cost: Money | None = None
    notes: list[str] = Field(default_factory=list)
    # 租车合同细节（案例 V3 待办、V4 对比表），不知道的留空，报告里显示"待确认"
    insurance: str | None = None            # 例如"全险（零自付额）"
    excess: Money | None = None             # 自付额
    deposit: Money | None = None            # 押金（信用卡预授权）
    one_way_fee: Money | None = None        # 异地还车费
    pickup_desk_hours: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")   # 取车柜台营业时间，例如 07:00-22:00
    dropoff_desk_hours: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")
    after_hours_return: str | None = None   # 营业时间外怎么还车，例如"钥匙投进还车箱"
    road_restrictions: str | None = None    # 合同不允许开的路，例如"禁止开非铺装路"


class TodoKind(StrEnum):
    BOOKING = "booking"
    VISA = "visa"
    LICENCE_TRANSLATION = "licence_translation"
    PAYMENT = "payment"
    CONFIRM = "confirm"
    OTHER = "other"


class Todo(Model):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    kind: TodoKind = TodoKind.OTHER
    owner_ids: list[str] = Field(default_factory=list)  # 为空表示全员
    due: date | None = None
    status: Literal["open", "done"] = "open"
    item_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TripPrefs(Model):
    max_daily_drive_hours: float = 5.0
    earliest_departure: time = time(7, 0)
    room_requirements: str = ""          # 例如"情侣一间，另外两人必须分床"（信息清单里填的）


class TripMeta(Model):
    id: str
    title: str
    destination: str                     # ISO 国家代码，例如 NZ
    start_date: date
    end_date: date
    origin_city: str
    home_timezone: str = "Australia/Melbourne"
    local_timezone: str = "Pacific/Auckland"
    local_currency: str = "NZD"
    settle_currency: str = "AUD"
    prefs: TripPrefs = Field(default_factory=TripPrefs)


class Trip(Model):
    meta: TripMeta
    members: list[Member]
    segments: list[Segment] = Field(default_factory=list)
    days: list[Day] = Field(default_factory=list)
    accommodations: list[Accommodation] = Field(default_factory=list)
    vehicles: list[Vehicle] = Field(default_factory=list)
    todos: list[Todo] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)  # 用户没说、agent 先按默认处理的地方，报告里列出来请用户确认
    custom_places: list[Place] = Field(default_factory=list)  # 只属于这个行程的地点，例如订好的民宿；同 id 时优先于知识库
    version: int = 1

    @model_validator(mode="after")
    def _check_ids(self) -> "Trip":
        seen: set[str] = set()
        for item in self.all_items():
            if item.id in seen:
                raise ValueError(f"事件 id 重复：{item.id}")
            seen.add(item.id)
        member_ids = self.member_ids()
        for item in self.all_items():
            for m in item.participants or []:
                if m not in member_ids:
                    raise ValueError(f"{item.id}：参加人 {m} 不是行程成员")
        return self

    def all_items(self) -> list[Item]:
        return [item for day in self.days for item in day.items]

    def find_item(self, item_id: str) -> Item | None:
        return next((i for i in self.all_items() if i.id == item_id), None)

    def member_ids(self) -> set[str]:
        return {m.id for m in self.members}

    def member(self, member_id: str) -> Member | None:
        return next((m for m in self.members if m.id == member_id), None)

    def participants_of(self, item: Item) -> list[str]:
        return item.participants if item.participants is not None else [m.id for m in self.members]

    def local_date(self, item: Item) -> date:
        """事件开始时刻在目的地时区的日期，用来决定它属于哪一天。"""
        return item.start.astimezone(ZoneInfo(self.meta.local_timezone)).date()

    def normalized(self) -> "Trip":
        """把每个事件放到它开始日期所在的那一天，并按开始时间排序。改动之后调用。"""
        items = self.all_items()
        days = {d.date: d.model_copy(update={"items": []}) for d in self.days}
        for item in items:
            day = self.local_date(item)
            if day not in days:
                raise ValueError(f"{item.id}：开始日期 {day} 不在行程日期内")
            days[day].items.append(item)
        for d in days.values():
            d.items.sort(key=lambda i: (i.start, i.end))
        ordered = [days[k] for k in sorted(days)]
        return self.model_copy(update={"days": ordered})
