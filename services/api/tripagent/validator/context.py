"""校验器的输入和阈值配置。阈值都是【初定】，放在这里统一调整（设计规格 11.1 节）。"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ..live.models import LiveSnapshot
from ..routing import DriveTimeProvider
from ..schema.memory import Decision, DecisionStatus
from ..schema.place import Place
from ..schema.trip import Item, Trip


@dataclass(frozen=True)
class ValidatorConfig:
    factor_normal: float = 1.1            # 普通道路车程修正系数
    factor_mountain: float = 1.3          # 山路、景观路车程修正系数
    move_buffer_min: int = 10             # 没写开车的隐含移动额外留的缓冲（短距离不超过车程本身）
    domestic_flight_min: int = 90         # 国内航班提前到机场
    international_flight_min: int = 180   # 国际航班提前到机场
    early_flight_hour: int = 9            # 几点前起飞算早班机
    airport_night_max_min: int = 30       # 早班机前一晚住处到机场的车程上限
    max_drive_hard_hours: float = 11      # 每天开车超过这个时长算错误
    heavy_day_drive_hours: float = 7      # 每天开车超过这个时长算高强度
    lunch_window: tuple[time, time] = (time(11, 0), time(14, 30))
    dinner_window: tuple[time, time] = (time(17, 0), time(20, 30))
    rain_mm: float = 10                   # 户外项目当天预报降雨超过这个量（且概率够高）就提醒
    rain_prob: int = 70
    gust_kmh: float = 70                  # 阵风超过这个速度，游船、观光飞行、滑翔伞可能取消
    road_event_m: float = 300             # 道路事件离路线多近算"在路上"
    min_driver_age: int = 21
    young_driver_age: int = 25
    car_desk_hours: tuple[time, time] = (time(7, 0), time(21, 0))   # 不知道租车柜台营业时间时，按这个范围提醒
    # 这些国家的驾照是英文的，在新西兰不需要翻译件
    english_licence_countries: frozenset[str] = frozenset({"AU", "NZ", "GB", "US", "CA", "IE", "ZA", "SG"})


@dataclass
class ValidationContext:
    trip: Trip
    places: dict[str, Place]
    drive_times: DriveTimeProvider
    decisions: list[Decision] = field(default_factory=list)
    config: ValidatorConfig = field(default_factory=ValidatorConfig)
    live: LiveSnapshot | None = None      # 实时数据（路况、预警、预报）；没有时 R21–R23 不检查
    today: date | None = None             # 今天；有了才检查"待确认事项过了截止日期"（R26）

    def __post_init__(self) -> None:
        self.tz = ZoneInfo(self.trip.meta.local_timezone)
        self.places = {**self.places, **{p.id: p for p in self.trip.custom_places}}

    def place(self, place_id: str | None) -> Place | None:
        return self.places.get(place_id) if place_id else None

    def local(self, dt: datetime) -> datetime:
        return dt.astimezone(self.tz)

    def hm(self, dt: datetime) -> str:
        return self.local(dt).strftime("%H:%M")

    def label(self, item: Item) -> str:
        d = self.local(item.start)
        return f"{d.month}/{d.day} {item.title}"

    def drive(self, from_place: str, to_place: str) -> tuple[int, float] | None:
        """返回（纯驾驶分钟数，乘修正系数后的分钟数）；没有车程数据时返回 None。"""
        est = self.drive_times.estimate(from_place, to_place)
        if est is None:
            return None
        factor = self.config.factor_mountain if est.road == "mountain" else self.config.factor_normal
        return est.minutes, est.minutes * factor

    def route(self, from_place: str, to_place: str) -> list[tuple[float, float]] | None:
        """开车路线的形状（纬度, 经度）；车程来源不提供时返回 None。"""
        geometry = getattr(self.drive_times, "geometry", None)
        return geometry(from_place, to_place) if geometry else None

    def timeline(self, member_id: str) -> list[Item]:
        items = [i for i in self.trip.all_items() if member_id in self.trip.participants_of(i)]
        return sorted(items, key=lambda i: (i.start, i.end))

    def active_decisions(self) -> list[Decision]:
        return [d for d in self.decisions if d.status == DecisionStatus.ACTIVE]
