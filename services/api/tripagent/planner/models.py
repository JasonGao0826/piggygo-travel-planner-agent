"""首次生成行程时模型输出的数据结构（设计规格第 5 节 S2、S4）。

模型只做选择（住哪、去哪、什么顺序、固定场次），时间线由代码排（timeline.py），再由校验器把关。
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from ..schema.common import Model


def at_most(n: int) -> AfterValidator:
    """模型多写了只留前 n 条：假设清单太长没人看（提示里也写了上限）。"""
    return AfterValidator(lambda items: items[:n])


class FlightPlan(Model):
    number: str | None = Field(default=None, description="航班号；还没订的可以留空")
    from_airport: str = Field(description="出发机场的地点 id")
    to_airport: str = Field(description="到达机场的地点 id")
    date: str = Field(description="起飞日期 YYYY-MM-DD（出发地当地）")
    depart: str = Field(description="起飞时间 HH:MM（出发地当地）")
    arrive: str = Field(description="到达时间 HH:MM（到达地当地）")
    arrive_next_day: bool = False
    booked: bool = Field(description="用户已经订好的为 true，建议新订的为 false")
    international: bool = False


class SegmentPlan(Model):
    name: str = Field(description="例如 南岛自驾")
    transport: Literal["self_drive", "public", "tour"]
    start_date: str
    end_date: str


class SkeletonDay(Model):
    date: str
    night_place_id: str | None = Field(description="当晚住在哪个城镇（知识库里的城镇地点 id）；最后一天为 null")
    night_city: str | None = Field(description="当晚住宿城市的中文名，例如 皇后镇")
    route: str = Field(description="当天路线，例如 皇后镇 → 格林诺奇 → 特威泽尔")
    focus: str = Field(description="当天重点，一句话")
    segment: str = Field(description="属于哪一段，填 segments 里的 name")


class Skeleton(Model):
    """S2 路线框架。"""

    summary: str = Field(description="两三句话说明整体思路：从哪进、从哪出、为什么这样分段")
    segments: list[SegmentPlan]
    flights: list[FlightPlan]
    days: list[SkeletonDay]
    assumptions: Annotated[list[str], at_most(6)] = Field(description="用户没说、按默认处理、需要大家核对的地方；最多 6 条，每条一句话")
    questions: list[str] = Field(description="必须先问用户才能继续的问题；没有就留空")


class Stop(Model):
    place_id: str = Field(description="知识库里的地点 id")
    kind: Literal["activity", "meal", "free_time", "buffer"]
    title: str = Field(description="中文名称，例如 Skytrek 双人滑翔伞")
    start: str | None = Field(default=None, description="固定开始时间 HH:MM（已订场次或必须赶上的场次）；不固定就留空，由程序按车程排")
    duration_min: int = Field(description="停留分钟数，参考知识库的建议时长")
    booking: Literal["booked", "to_book", "not_required"]
    notes: list[str] = Field(default_factory=list)
    weather_backup: str | None = Field(default=None, description="受天气影响的项目写备用安排")
    optional: bool = Field(default=False, description="备选：时间不够、天气不好或太累时可以直接跳过的顺路点")
    optional_reason: str | None = Field(default=None, description="备选的说明，例如 时间不够就跳过、天气好再去")


class DayPlan(Model):
    date: str
    depart: str | None = Field(default=None, description="早上从住处出发的时间 HH:MM；当天有到达航班时留空")
    stops: list[Stop] = Field(description="按顺序列出要去的地点，包括午餐和晚餐")


class DayPlans(Model):
    """S4 每天的安排。"""

    days: list[DayPlan]
    assumptions: Annotated[list[str], at_most(3)] = Field(default_factory=list, description="路线框架里没写过的新假设；最多 3 条")
