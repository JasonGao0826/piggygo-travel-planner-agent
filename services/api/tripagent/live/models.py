"""实时数据的数据结构：路况、天气预警、天气预报。

一份 LiveSnapshot 是某个时刻从各个来源抓到的数据，校验器的 R21–R23 拿它和行程比对。
坐标一律写成（纬度, 经度）。
"""

from datetime import date, datetime

from pydantic import AwareDatetime, Field

from ..schema.common import Model

LatLon = tuple[float, float]


class RoadEvent(Model):
    """一条道路事件（新西兰交通局 NZTA）：封路、施工、事故、限行……"""

    id: str
    kind: str                     # 事件类型，例如 Road Work、Road Hazard
    impact: str                   # 影响：Road Closed、Delays、Vehicle Restrictions、Caution
    title: str                    # 路段，例如 SH 6 Haast to Makarora
    detail: str = ""
    region: str | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None   # 没有结束时间表示"直到另行通知"
    planned: bool = False
    lines: list[list[LatLon]] = Field(default_factory=list)

    def active_during(self, start: datetime, end: datetime) -> bool:
        return (self.start is None or self.start <= end) and (self.end is None or self.end >= start)


class WeatherAlert(Model):
    """一条天气预警（MetService 的 CAP 预警）。"""

    id: str
    event: str                    # 例如 Heavy Rain Warning - Orange
    severity: str = ""            # CAP 等级：Minor / Moderate / Severe / Extreme
    headline: str = ""
    description: str = ""
    areas: list[str] = Field(default_factory=list)
    polygons: list[list[LatLon]] = Field(default_factory=list)
    onset: AwareDatetime | None = None
    expires: AwareDatetime | None = None
    url: str | None = None

    def active_during(self, start: datetime, end: datetime) -> bool:
        return (self.onset is None or self.onset <= end) and (self.expires is None or self.expires >= start)


class DayForecast(Model):
    """某个地点某一天的天气预报。"""

    lat: float
    lon: float
    date: date
    code: int | None = None       # WMO 天气代码
    tmax: float | None = None
    tmin: float | None = None
    precip_mm: float | None = None
    precip_prob: int | None = None
    gust_kmh: float | None = None
    place_ids: list[str] = Field(default_factory=list)


class LiveSnapshot(Model):
    fetched_at: dict[str, datetime] = Field(default_factory=dict)   # 每个来源的抓取时间
    road_events: list[RoadEvent] = Field(default_factory=list)
    weather_alerts: list[WeatherAlert] = Field(default_factory=list)
    forecasts: list[DayForecast] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)                 # 抓取失败的来源，提示用户"这部分没查到"
    notes: list[str] = Field(default_factory=list)                  # 例如"出发前 16 天内才有天气预报"
