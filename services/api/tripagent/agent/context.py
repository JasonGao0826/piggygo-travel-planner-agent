"""把记忆层整理成给模型看的简短文字（设计规格 8.3 节：每轮从数据库重新组装上下文）。"""

from datetime import date

from ..report.build import STATUS, ReportBuilder
from ..schema.memory import Decision, Patch
from ..schema.place import Place
from ..schema.trip import BookingStatus, Item, ItemType, Trip


class Formatter(ReportBuilder):
    """复用报告里的日期、时间、地点格式，保证模型看到的和报告里写的一致。"""

    def __init__(self, trip: Trip, places: dict[str, Place]):
        super().__init__(trip, places, generated_on=date.today())

    def item_line(self, item: Item) -> str:
        where = (f"{self.place_name(item.from_place_id)} → {self.place_name(item.to_place_id)}"
                 if item.is_movement else self.place_name(item.place_id))
        flags = []
        if item.locked:
            flags.append("锁定")
        if status := STATUS[item.booking_status]:
            flags.append(status)
        who = self.who(item)
        if who != "全员":
            flags.append(who)
        suffix = f"（{'，'.join(flags)}）" if flags else ""
        return f"{self.time_range(item)} {item.title} @ {where}{suffix} [{item.id}]"

    def item_dict(self, item: Item) -> dict:
        return {
            "id": item.id, "type": item.type.value, "time": self.time_range(item), "title": item.title,
            "place_id": item.place_id, "from_place_id": item.from_place_id, "to_place_id": item.to_place_id,
            "start": item.start.isoformat(), "end": item.end.isoformat(),
            "participants": self.who(item), "driver_id": item.driver_id, "vehicle_id": item.vehicle_id,
            "booking_status": item.booking_status.value, "locked": item.locked,
            "checkin_before_min": item.checkin_before_min, "notes": item.notes, "backup": item.backup,
        }


def trip_summary(trip: Trip, places: dict[str, Place]) -> str:
    f = Formatter(trip, places)
    meta = trip.meta
    members = "、".join(f"{m.name}({m.id})" for m in trip.members)
    lines = [
        f"行程：{meta.title}（系统版本 V{trip.version}），{meta.start_date} 至 {meta.end_date}，时区 {meta.local_timezone}",
        f"成员：{members}",
        f"整体路线：{f.route()}",
        "",
        "每天概况（只列航班、活动和锁定项；完整安排用 get_day 查询）：",
    ]
    for n, day in enumerate(trip.days, 1):
        hours = f.drive_hours(day)
        lines.append(f"D{n} {f.md(day.date)} {f.wd(day.date)}｜{day.route}｜住：{day.night_city or '—'}｜开车约 {hours:.1f} 小时")
        for item in day.items:
            if item.type in (ItemType.FLIGHT, ItemType.ACTIVITY) or item.locked:
                lines.append(f"  - {f.item_line(item)}")
    lines.append("")
    lines.append("住宿：")
    for a in trip.accommodations:
        lines.append(f"  - {f.md(a.check_in)}–{f.md(a.check_out)} {a.city} {a.name}"
                     f"（{STATUS[a.booking_status] or '—'}{'，锁定' if a.locked else ''}）[{a.id}]")
    lines.append("租车：")
    for v in trip.vehicles:
        lines.append(f"  - {f.place_name(v.pickup_place_id)} {f.md(f.local(v.pickup_time).date())} {f.hm(v.pickup_time)} 取车 → "
                     f"{f.place_name(v.dropoff_place_id)} {f.md(f.local(v.dropoff_time).date())} {f.hm(v.dropoff_time)} 还车，"
                     f"司机 {'、'.join(v.drivers)}（{STATUS[v.booking_status] or '—'}）[{v.id}]")
    open_todos = [t for t in trip.todos if t.status == "open"]
    to_book = [i for i in trip.all_items() if i.booking_status == BookingStatus.TO_BOOK]
    lines.append(f"待办：{len(open_todos)} 项未完成；还没订的项目 {len(to_book)} 个（用 list_todos 查看）")
    if trip.assumptions:
        lines.append("目前的假设：" + "；".join(trip.assumptions))
    return "\n".join(lines)


DECISIONS_TEXT_MAX = 5000   # 系统提示里决策账本最多多少字（投票结果等会越积越多）


def decisions_text(decisions: list[Decision], budget: int | None = None) -> str:
    """决策账本的文字。budget 是总字数上限（系统提示里用；必须做到的、高优先级的排前面）；查询工具不限。"""
    if not decisions:
        return "（决策账本为空）"
    if budget is not None:
        order = {"must": 0, "high": 1, "normal": 2, "low": 3}
        decisions = sorted(decisions, key=lambda d: order.get(d.priority.value, 9))
    lines = []
    total = 0
    for n, d in enumerate(decisions):
        if budget is not None and total > budget:
            lines.append(f"  - ……还有 {len(decisions) - n} 条，用 list_decisions 查")
            break
        parts = [f"[{d.id}] {d.content}（{d.kind.value}，优先级 {d.priority.value}）"]
        if d.requirement:
            req = {k: v for k, v in d.requirement.model_dump(mode="json").items() if v is not None}
            parts.append(f"必须满足：{req}")
        for opt in d.rejected_options:
            parts.append(f"已否决：{opt.description}")
        line = "  - " + "；".join(parts)
        line = line if len(line) <= 400 else line[:400] + "…"
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def pending_text(patches: list[Patch]) -> str:
    if not patches:
        return "（没有等待确认的改动）"
    lines = []
    for p in patches:
        lines.append(f"  - [{p.id}] {p.summary}（基于 V{p.base_version}，{'有错误' if p.has_errors else '校验通过'}）")
    return "\n".join(lines)
