"""校验规则 R01–R20、R25（设计规格 11.1 节）。每条规则是一个函数：输入校验上下文，返回问题列表。"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import takewhile

from ..schema.memory import Issue, RejectedOption, Requirement, Severity
from ..schema.place import Place
from ..schema.trip import BookingStatus, Item, ItemType, Trip
from .context import ValidationContext

ERROR, WARNING = Severity.ERROR, Severity.WARNING


def _issue(code: str, severity: Severity, message: str, items=(), suggestions=()) -> Issue:
    return Issue(code=code, severity=severity, message=message,
                 item_ids=list(items), suggestions=list(suggestions))


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def _arrival(ctx: ValidationContext, timeline: list[Item], idx: int) -> tuple[datetime | None, str]:
    """推算某成员到达 timeline[idx] 起点的时间，并说明依据。

    往前找：同一地点的事件说明人早就在这里了，继续往前；遇到以这里为终点的移动，到达时间就是移动结束；
    遇到在别处的事件，到达时间 = 那个事件结束 + 车程（乘修正系数）。
    """
    target = timeline[idx].start_place
    j = idx - 1
    while j >= 0:
        prev = timeline[j]
        loc = prev.end_place
        if loc is None or (loc == target and not prev.is_movement):
            j -= 1
            continue
        if loc == target:
            return prev.end, f"{ctx.label(prev)} {ctx.hm(prev.end)} 到达"
        est = ctx.drive(loc, target)
        if est is None:
            return None, "缺少车程数据"
        arrive = prev.end + timedelta(minutes=est[1])
        return arrive, f"{ctx.label(prev)} {ctx.hm(prev.end)} 结束，之后约 {round(est[1])} 分钟车程"
    return None, "行程开始"


def _drive_minutes_by_day(ctx: ValidationContext) -> dict[date, float]:
    """每天每位成员在车上的总时长，取最大的那位（分钟）。"""
    result: dict[date, float] = {}
    for day in ctx.trip.days:
        per_member: dict[str, float] = defaultdict(float)
        for item in day.items:
            if item.type == ItemType.DRIVE:
                for m in ctx.trip.participants_of(item):
                    per_member[m] += (item.end - item.start).total_seconds() / 60
        result[day.date] = max(per_member.values(), default=0.0)
    return result


# ---------------- R01 同一成员的事件时间不重叠 ----------------

def r01_overlap(ctx: ValidationContext) -> list[Issue]:
    issues = []
    for m in ctx.trip.member_ids():
        tl = ctx.timeline(m)
        for a, b in zip(tl, tl[1:]):
            if b.start < a.end:
                issues.append(_issue(
                    "R01", ERROR,
                    f"{ctx.label(a)}（到 {ctx.hm(a.end)}）和 {ctx.label(b)}（{ctx.hm(b.start)} 开始）时间重叠",
                    [a.id, b.id], ["调整其中一项的时间", "如果是分组活动，检查参加人是否写对"]))
    return issues


# ---------------- R02 活动在营业时间内 ----------------

def r02_opening_hours(ctx: ValidationContext) -> list[Issue]:
    issues = []
    for item in ctx.trip.all_items():
        if item.type not in (ItemType.ACTIVITY, ItemType.MEAL, ItemType.FREE_TIME):
            continue
        place = ctx.place(item.place_id)
        if place is None or place.always_open or not place.hours:
            continue
        start, end = ctx.local(item.start), ctx.local(item.end)
        rule = place.hours_on(start.date())
        if rule is None:
            issues.append(_issue("R02", ERROR, f"{ctx.label(item)}：{place.name_zh} 当天不营业", [item.id],
                                 ["换一天", "确认官网营业时间"]))
            continue
        if start.time() < rule.open or end.time() > rule.close:
            issues.append(_issue(
                "R02", ERROR,
                f"{ctx.label(item)}（{ctx.hm(item.start)}–{ctx.hm(item.end)}）超出营业时间 "
                f"{rule.open:%H:%M}–{rule.close:%H:%M}", [item.id], ["调整时间到营业时间内"]))
        elif rule.last_entry and start.time() > rule.last_entry:
            issues.append(_issue(
                "R02", ERROR,
                f"{ctx.label(item)} {ctx.hm(item.start)} 才到，已过最晚入场时间 {rule.last_entry:%H:%M}",
                [item.id], [f"在 {rule.last_entry:%H:%M} 前入场"]))
    return issues


# ---------------- R03 签到提前量足够 ----------------

def r03_checkin(ctx: ValidationContext) -> list[Issue]:
    issues, seen = [], set()
    for m in ctx.trip.member_ids():
        tl = ctx.timeline(m)
        for idx, item in enumerate(tl):
            if item.type == ItemType.FLIGHT or item.checkin_before_min <= 0 or item.id in seen:
                continue
            arrive, why = _arrival(ctx, tl, idx)
            deadline = item.start - timedelta(minutes=item.checkin_before_min)
            if arrive is not None and arrive > deadline:
                seen.add(item.id)
                issues.append(_issue(
                    "R03", ERROR,
                    f"{ctx.label(item)} 要求 {ctx.hm(deadline)} 前签到，但预计 {ctx.hm(arrive)} 才能到（{why}）",
                    [item.id], ["提前前一项的结束时间", "换一个更晚的场次", "换一个更近的商家"]))
    return issues


# ---------------- R04 车程可行 ----------------

def r04_travel(ctx: ValidationContext) -> list[Issue]:
    issues, seen = [], set()

    def add(key, issue):
        if key not in seen:
            seen.add(key)
            issues.append(issue)

    for item in ctx.trip.all_items():
        if item.type != ItemType.DRIVE:
            continue
        est = ctx.drive(item.from_place_id, item.to_place_id)
        planned = (item.end - item.start).total_seconds() / 60
        if est is None:
            add(("nodata", item.id), _issue("R04", WARNING, f"{ctx.label(item)}：缺少车程数据，无法检查", [item.id]))
        elif planned < est[0]:
            add(("drive", item.id), _issue(
                "R04", ERROR, f"{ctx.label(item)} 只留了 {round(planned)} 分钟，纯驾驶就要约 {est[0]} 分钟",
                [item.id], [f"至少留 {round(est[1])} 分钟"]))
        elif planned < round(est[1]):
            add(("drive", item.id), _issue(
                "R04", WARNING, f"{ctx.label(item)} 留了 {round(planned)} 分钟，按路况建议留约 {round(est[1])} 分钟",
                [item.id], ["多留一些时间"]))

    for m in ctx.trip.member_ids():
        loc, free_at, last = None, None, None
        for item in ctx.timeline(m):
            start_loc = item.start_place
            if start_loc and loc and start_loc != loc and free_at is not None:
                est = ctx.drive(loc, start_loc)
                gap = (item.start - free_at).total_seconds() / 60
                if est is None:
                    add(("nodata", last.id, item.id), _issue(
                        "R04", WARNING, f"{ctx.label(last)} → {ctx.label(item)}：缺少两地车程数据，无法检查",
                        [last.id, item.id]))
                else:
                    raw, adjusted = est
                    need = round(adjusted + min(ctx.config.move_buffer_min, raw))
                    if gap < raw:
                        add(("gap", last.id, item.id), _issue(
                            "R04", ERROR,
                            f"{ctx.label(last)} 结束后只有 {round(gap)} 分钟赶到 {ctx.label(item)}，车程约 {raw} 分钟",
                            [last.id, item.id],
                            [*_optional_hint(ctx, ctx.local(item.start).date()), "调整时间", "补上开车安排"]))
                    elif gap < need:
                        add(("gap", last.id, item.id), _issue(
                            "R04", WARNING,
                            f"{ctx.label(last)} → {ctx.label(item)} 之间只有 {round(gap)} 分钟，建议留约 {round(need)} 分钟",
                            [last.id, item.id], ["多留一些时间"]))
            if item.end_place:
                loc = item.end_place
            free_at = max(free_at, item.end) if free_at else item.end
            last = item
    return issues


def _optional_hint(ctx: ValidationContext, day: date) -> list[str]:
    """当天有备选项时，建议先去掉备选（案例 V4：时间不够就跳过）。"""
    names = [i.title for d in ctx.trip.days if d.date == day for i in d.items if i.optional]
    return [f"先去掉当天的备选：{'、'.join(names[:3])}"] if names else []


# ---------------- R05 每日驾驶时长 ----------------

def r05_daily_drive(ctx: ValidationContext) -> list[Issue]:
    issues = []
    soft = ctx.trip.meta.prefs.max_daily_drive_hours
    for day, minutes in _drive_minutes_by_day(ctx).items():
        hours = minutes / 60
        if hours > ctx.config.max_drive_hard_hours:
            issues.append(_issue("R05", ERROR, f"{_md(day)} 开车约 {hours:.1f} 小时，超过 {ctx.config.max_drive_hard_hours:g} 小时上限",
                                 suggestions=[*_optional_hint(ctx, day), "拆成两天", "减少当天的景点"]))
        elif hours > soft:
            issues.append(_issue("R05", WARNING, f"{_md(day)} 开车约 {hours:.1f} 小时，超过你们设定的 {soft:g} 小时",
                                 suggestions=["两位司机轮换", "中途多休息"]))
    return issues


# ---------------- R06 住宿入住 / 退房时间 ----------------

def r06_accommodation_times(ctx: ValidationContext) -> list[Issue]:
    issues = []
    accs = {a.id: a for a in ctx.trip.accommodations}
    for item in ctx.trip.all_items():
        if item.type not in (ItemType.CHECKIN, ItemType.CHECKOUT) or not item.accommodation_id:
            continue
        acc = accs.get(item.accommodation_id)
        if acc is None:
            issues.append(_issue("R06", ERROR, f"{ctx.label(item)}：找不到住宿 {item.accommodation_id}", [item.id]))
            continue
        start = ctx.local(item.start)
        if item.type == ItemType.CHECKIN:
            if start.date() != acc.check_in:
                issues.append(_issue("R06", ERROR, f"{ctx.label(item)}：日期和 {acc.name} 的入住日期 {_md(acc.check_in)} 不一致", [item.id]))
            elif acc.earliest_checkin and start.time() < acc.earliest_checkin:
                issues.append(_issue("R06", WARNING,
                                     f"{ctx.label(item)} {ctx.hm(item.start)} 早于 {acc.name} 的入住时间 {acc.earliest_checkin:%H:%M}",
                                     [item.id], ["问房东能否提前入住或寄存行李"]))
        else:
            if start.date() != acc.check_out:
                issues.append(_issue("R06", ERROR, f"{ctx.label(item)}：日期和 {acc.name} 的退房日期 {_md(acc.check_out)} 不一致", [item.id]))
            elif acc.latest_checkout and ctx.local(item.end).time() > acc.latest_checkout:
                issues.append(_issue("R06", WARNING,
                                     f"{ctx.label(item)} 晚于 {acc.name} 的退房时间 {acc.latest_checkout:%H:%M}",
                                     [item.id], ["问房东能否延迟退房"]))
    return issues


# ---------------- R07 早班机可行 ----------------

def _airport_min(ctx: ValidationContext, flight: Item) -> int:
    """航班要提前多少分钟到机场。"""
    cfg = ctx.config
    return max(flight.checkin_before_min,
               cfg.international_flight_min if flight.flight and flight.flight.international else cfg.domestic_flight_min)


def r07_flights(ctx: ValidationContext) -> list[Issue]:
    issues = []
    cfg = ctx.config
    for item in ctx.trip.all_items():
        if item.type != ItemType.FLIGHT:
            continue
        need = _airport_min(ctx, item)
        deadline = item.start - timedelta(minutes=need)
        for m in ctx.trip.participants_of(item):
            tl = ctx.timeline(m)
            arrive, why = _arrival(ctx, tl, tl.index(item))
            if arrive is not None and arrive > deadline:
                issues.append(_issue("R07", ERROR,
                                     f"{ctx.label(item)} 需要 {ctx.hm(deadline)} 前到机场，但预计 {ctx.hm(arrive)} 才到（{why}）",
                                     [item.id], ["提早出发", "前一晚住到机场附近"]))
                break
        start = ctx.local(item.start)
        if start.hour < cfg.early_flight_hour:
            for acc in ctx.trip.accommodations:
                if acc.check_out == start.date():
                    est = ctx.drive(acc.place_id, item.from_place_id)
                    if est and est[0] > cfg.airport_night_max_min:
                        issues.append(_issue("R07", WARNING,
                                             f"{ctx.label(item)} 是早班机，前一晚住处 {acc.name} 到机场约 {est[0]} 分钟",
                                             [item.id], ["前一晚住到机场附近"]))
    return issues


# ---------------- R08 锁定项没有被改动（比较改动前后） ----------------

_LOCKED_ITEM_FIELDS = ("type", "start", "end", "place_id", "from_place_id", "to_place_id", "participants", "booking_status")
_LOCKED_ACC_FIELDS = ("place_id", "check_in", "check_out", "booking_status")
_LOCKED_VEHICLE_FIELDS = ("pickup_place_id", "pickup_time", "dropoff_place_id", "dropoff_time", "booking_status")


def check_locked_changes(before: Trip, after: Trip, allowed: set[str] = frozenset()) -> list[Issue]:
    """R08：锁定的事件、住宿、车辆，未经用户同意（allowed）不能被删除或改动关键字段。"""
    issues = []
    pairs = [
        ({i.id: i for i in before.all_items()}, {i.id: i for i in after.all_items()}, _LOCKED_ITEM_FIELDS, "事件"),
        ({a.id: a for a in before.accommodations}, {a.id: a for a in after.accommodations}, _LOCKED_ACC_FIELDS, "住宿"),
        ({v.id: v for v in before.vehicles}, {v.id: v for v in after.vehicles}, _LOCKED_VEHICLE_FIELDS, "租车"),
    ]
    for old_map, new_map, fields, kind in pairs:
        for oid, old in old_map.items():
            if not old.locked or oid in allowed:
                continue
            new = new_map.get(oid)
            if new is None:
                issues.append(_issue("R08", ERROR, f"锁定的{kind}「{getattr(old, 'title', None) or getattr(old, 'name', oid)}」被删除了", [oid],
                                     ["保留它，或请用户确认后再删"]))
                continue
            changed = [f for f in fields if getattr(old, f) != getattr(new, f)]
            if changed or not new.locked:
                what = "、".join(changed) if changed else "锁定状态"
                issues.append(_issue("R08", ERROR, f"锁定的{kind}「{getattr(old, 'title', None) or getattr(old, 'name', oid)}」的{what}被改动了", [oid],
                                     ["保留原安排，或请用户确认后再改"]))
    return issues


# ---------------- R09 每晚都有住宿 ----------------

def r09_nights(ctx: ValidationContext) -> list[Issue]:
    issues = []
    meta = ctx.trip.meta
    active = [a for a in ctx.trip.accommodations if a.booking_status != BookingStatus.CANCELLED]
    night = meta.start_date
    while night < meta.end_date:
        covering = [a for a in active if a.check_in <= night < a.check_out]
        if not covering:
            issues.append(_issue("R09", ERROR, f"{_md(night)} 晚上没有住宿", suggestions=["补上当晚住宿"]))
        elif len(covering) > 1:
            names = "、".join(a.name for a in covering)
            issues.append(_issue("R09", WARNING, f"{_md(night)} 晚上同时订了多处住宿：{names}", [a.id for a in covering]))
        night += timedelta(days=1)
    return issues


# ---------------- R10 租车前后连贯 ----------------

def r10_vehicles(ctx: ValidationContext) -> list[Issue]:
    issues = []
    vehicles = {v.id: v for v in ctx.trip.vehicles}
    for item in ctx.trip.all_items():
        if item.type in (ItemType.DRIVE, ItemType.CAR_PICKUP, ItemType.CAR_DROPOFF):
            v = vehicles.get(item.vehicle_id) if item.vehicle_id else None
            if v is None:
                issues.append(_issue("R10", ERROR, f"{ctx.label(item)} 没有对应的租车", [item.id], ["指定使用哪辆车"]))
                continue
            if item.type == ItemType.DRIVE and (item.start < v.pickup_time or item.end > v.dropoff_time):
                issues.append(_issue("R10", ERROR,
                                     f"{ctx.label(item)} 不在租车时间内（{ctx.hm(v.pickup_time)} 取车，{ctx.local(v.dropoff_time):%m/%d %H:%M} 还车）",
                                     [item.id], ["调整租车时间", "这段改用其他交通方式"]))
            if item.type == ItemType.CAR_PICKUP and item.place_id != v.pickup_place_id:
                issues.append(_issue("R10", ERROR, f"{ctx.label(item)} 的地点和租车订单的取车地点不一致", [item.id]))
            if item.type == ItemType.CAR_DROPOFF and item.place_id != v.dropoff_place_id:
                issues.append(_issue("R10", ERROR, f"{ctx.label(item)} 的地点和租车订单的还车地点不一致", [item.id]))
    # 车在哪里，就只能从哪里开走：从取车地点开始，每段开车的起点必须是车上一次停的地方
    for v in ctx.trip.vehicles:
        loc = v.pickup_place_id
        for item in sorted((i for i in ctx.trip.all_items() if i.vehicle_id == v.id), key=lambda i: i.start):
            if item.type == ItemType.DRIVE:
                if item.from_place_id != loc:
                    issues.append(_issue("R10", ERROR,
                                         f"{ctx.label(item)} 从 {ctx.place(item.from_place_id).name_zh if ctx.place(item.from_place_id) else item.from_place_id} 出发，"
                                         f"但车还停在 {ctx.place(loc).name_zh if ctx.place(loc) else loc}", [item.id],
                                         ["从车停的地方出发", "补上中间开车的安排"]))
                loc = item.to_place_id
            elif item.type == ItemType.CAR_DROPOFF and item.place_id != loc:
                issues.append(_issue("R10", ERROR, f"{ctx.label(item)}：车停在别处，不在还车地点", [item.id]))
    return issues


# ---------------- R11 司机资格 ----------------

def r11_drivers(ctx: ValidationContext) -> list[Issue]:
    issues, checked = [], set()
    cfg = ctx.config
    for v in ctx.trip.vehicles:
        for mid in v.drivers:
            if mid in checked:
                continue
            checked.add(mid)
            m = ctx.trip.member(mid)
            if m is None or not m.can_drive:
                issues.append(_issue("R11", ERROR, f"{m.name if m else mid} 被列为司机，但没有登记为可以开车", [v.id],
                                     ["换一位能开车的司机", "如果其实能开车，请本人在「大家」页签的「我的资料」里改"]))
                continue
            if m.age is None:
                issues.append(_issue("R11", WARNING, f"{m.name} 没有填年龄，无法确认租车资格", [v.id],
                                     [f"请 {m.name} 在「大家」页签的「我的资料」里补上年龄"]))
            elif m.age < cfg.min_driver_age:
                issues.append(_issue("R11", ERROR, f"{m.name} {m.age} 岁，低于租车最低年龄 {cfg.min_driver_age} 岁", [v.id]))
            elif m.age < cfg.young_driver_age:
                issues.append(_issue("R11", WARNING, f"{m.name} {m.age} 岁，不满 {cfg.young_driver_age} 岁，租车可能收年轻司机附加费",
                                     [v.id], ["向租车公司确认第二驾驶员和年轻司机费用"]))
            if m.licence_country and m.licence_country not in cfg.english_licence_countries:
                has_todo = any(t.kind == "licence_translation" and (not t.owner_ids or mid in t.owner_ids)
                               for t in ctx.trip.todos)
                if not has_todo:
                    issues.append(_issue("R11", ERROR, f"{m.name} 的驾照不是英文的，需要认可的英文翻译件，但待办里没有这一项",
                                         [v.id], [f"添加待办：{m.name} 办理驾照英文翻译件"]))
    return issues


# ---------------- R12 每段开车都有合格的司机 ----------------

def r12_drive_driver(ctx: ValidationContext) -> list[Issue]:
    issues = []
    vehicles = {v.id: v for v in ctx.trip.vehicles}
    for item in ctx.trip.all_items():
        if item.type != ItemType.DRIVE:
            continue
        if not item.driver_id:
            issues.append(_issue("R12", ERROR, f"{ctx.label(item)} 没有指定司机", [item.id]))
            continue
        driver = ctx.trip.member(item.driver_id)
        name = driver.name if driver else item.driver_id
        if item.driver_id not in ctx.trip.participants_of(item):
            issues.append(_issue("R12", ERROR, f"{ctx.label(item)} 的司机 {name} 不在这段行程的成员里", [item.id]))
        v = vehicles.get(item.vehicle_id) if item.vehicle_id else None
        if v and item.driver_id not in v.drivers:
            issues.append(_issue("R12", ERROR, f"{ctx.label(item)} 的司机 {name} 不在租车合同的驾驶员名单里", [item.id],
                                 ["换一位登记过的司机", "把他加为第二驾驶员"]))
    return issues


# ---------------- R13 受天气影响的项目有备用安排 ----------------

def r13_weather(ctx: ValidationContext) -> list[Issue]:
    issues = []
    # 只看行程里明确标为受天气影响的项目。知识库里地点的天气标记很宽泛（户外项目大多都标了），
    # 由 agent 排程时判断要不要在行程里标出来；报告里会照常显示地点的天气提示。
    for item in ctx.trip.all_items():
        if item.type == ItemType.ACTIVITY and item.weather_dependent and not item.backup:
            issues.append(_issue("R13", WARNING, f"{ctx.label(item)} 受天气影响，还没有备用安排", [item.id],
                                 ["写一个备用方案或可改期的时段"]))
    return issues


# ---------------- R14 项目限制 ----------------

def r14_restrictions(ctx: ValidationContext) -> list[Issue]:
    issues = []
    for item in ctx.trip.all_items():
        place = ctx.place(item.place_id)
        if not place or not place.restrictions:
            continue
        for mid in ctx.trip.participants_of(item):
            m = ctx.trip.member(mid)
            for r in place.restrictions:
                bad = (
                    (r.code == "max_weight_kg" and m.weight_kg is not None and m.weight_kg > float(r.value))
                    or (r.code == "min_weight_kg" and m.weight_kg is not None and m.weight_kg < float(r.value))
                    or (r.code == "min_age" and m.age is not None and m.age < float(r.value))
                    or (r.code == "max_age" and m.age is not None and m.age > float(r.value))
                )
                if bad:
                    issue = _issue("R14", ERROR, f"{ctx.label(item)}：{m.name} 不符合要求（{r.text}）", [item.id],
                                   ["这位成员换个项目", "分组安排"])
                    if r.code in ("max_weight_kg", "min_weight_kg"):   # 体重不给其他成员看（18 节）
                        issue = issue.model_copy(update={
                            "private_to": m.id,
                            "public_message": f"{ctx.label(item)}：有同行成员不符合这个项目的体重要求（详情只有本人能看到）"})
                    issues.append(issue)
    return issues


# ---------------- R15 需要预约的项目在待办里 ----------------

def r15_booking_todos(ctx: ValidationContext) -> list[Issue]:
    issues = []
    todos = [t for t in ctx.trip.todos if t.kind == "booking"]
    targets: list[tuple[str, str, Place | None, date | None]] = []
    for item in ctx.trip.all_items():
        if item.booking_status == BookingStatus.TO_BOOK:
            targets.append((item.id, ctx.label(item), ctx.place(item.place_id), ctx.local(item.start).date()))
    for a in ctx.trip.accommodations:
        if a.booking_status == BookingStatus.TO_BOOK:
            targets.append((a.id, f"住宿 {a.name}", None, None))
    for v in ctx.trip.vehicles:
        if v.booking_status == BookingStatus.TO_BOOK:
            targets.append((v.id, f"租车 {v.model or v.id}", None, None))
    for tid, label, place, day in targets:
        todo = next((t for t in todos if tid in t.item_ids), None)
        if todo is None or todo.due is None:
            issues.append(_issue("R15", ERROR, f"{label} 还没订，但待办里没有带截止日期的预订事项", [tid],
                                 ["添加预订待办并写明截止日期"]))
            continue
        if place and place.booking.lead_days and day:
            opens = day - timedelta(days=place.booking.lead_days)
            if todo.due < opens:
                issues.append(_issue("R15", WARNING,
                                     f"{label}：预约要到 {_md(opens)} 才开放，待办截止日 {_md(todo.due)} 太早",
                                     [tid], [f"把截止日改到 {_md(opens)} 当天"]))
    return issues


# ---------------- R16 不包含被否决的方案 ----------------

def _matches_rejection(opt: RejectedOption, place: Place) -> bool:
    if opt.place_id:
        return opt.place_id == place.id
    if opt.activity_tag and opt.activity_tag not in place.tags:
        return False
    if opt.city and opt.city != place.city:
        return False
    return bool(opt.activity_tag or opt.city)


def r16_rejected(ctx: ValidationContext) -> list[Issue]:
    issues = []
    for d in ctx.active_decisions():
        for opt in d.rejected_options:
            for item in ctx.trip.all_items():
                place = ctx.place(item.place_id)
                if place and item.booking_status != BookingStatus.CANCELLED and _matches_rejection(opt, place):
                    issues.append(_issue("R16", ERROR, f"{ctx.label(item)} 是之前否决过的方案：{opt.description}（{d.id}）",
                                         [item.id], ["换回已确定的方案"]))
    return issues


# ---------------- R17 必玩项目都已安排 ----------------

def _matches_requirement(req: Requirement, item: Item, place: Place | None, day: date) -> bool:
    if place is None:
        return False
    if req.place_id and req.place_id != place.id:
        return False
    if req.activity_tag and req.activity_tag not in place.tags:
        return False
    if req.city and req.city != place.city:
        return False
    if req.date and req.date != day:
        return False
    return True


def r17_must_do(ctx: ValidationContext) -> list[Issue]:
    issues = []
    for d in ctx.active_decisions():
        if d.requirement is None:
            continue
        found = any(
            item.booking_status != BookingStatus.CANCELLED
            and _matches_requirement(d.requirement, item, ctx.place(item.place_id), ctx.local(item.start).date())
            for item in ctx.trip.all_items()
        )
        if not found:
            issues.append(_issue("R17", ERROR, f"必玩项目没有安排：{d.content}（{d.id}）", suggestions=["把它排进行程"]))
    return issues


# ---------------- R18 地点真实存在 ----------------

def r18_places_exist(ctx: ValidationContext) -> list[Issue]:
    issues = []
    refs: list[tuple[str, str]] = []
    for item in ctx.trip.all_items():
        refs += [(p, item.id) for p in (item.place_id, item.from_place_id, item.to_place_id) if p]
    refs += [(a.place_id, a.id) for a in ctx.trip.accommodations]
    refs += [(p, v.id) for v in ctx.trip.vehicles for p in (v.pickup_place_id, v.dropoff_place_id)]
    for pid, owner in refs:
        if pid not in ctx.places:
            issues.append(_issue("R18", ERROR, f"{owner} 引用的地点 {pid} 不在知识库里，也没有核实过", [owner],
                                 ["先核实这个地点再加入行程"]))
    return issues


# ---------------- R19 不连续安排高强度日 ----------------

def r19_heavy_days(ctx: ValidationContext) -> list[Issue]:
    issues = []
    limit = ctx.config.heavy_day_drive_hours * 60
    drive = _drive_minutes_by_day(ctx)
    days = sorted(drive)
    for a, b in zip(days, days[1:]):
        if drive[a] >= limit and drive[b] >= limit:
            issues.append(_issue("R19", WARNING,
                                 f"{_md(a)} 和 {_md(b)} 连续两天开车都超过 {ctx.config.heavy_day_drive_hours:g} 小时",
                                 suggestions=["把其中一天的路程拆开", "中间安排一个轻松的半天"]))
    return issues


# ---------------- R20 用餐时间合理 ----------------

_TRANSIT = {ItemType.FLIGHT, ItemType.DRIVE, ItemType.TRANSFER, ItemType.BUFFER}
MEAL_MIN = 45   # 用餐时段里在当地的时间不到这么久，就不要求安排这顿饭


def _on_trip(ctx: ValidationContext) -> tuple[datetime | None, datetime | None]:
    """人在当地的时间：去程航班落地之后，到回程航班要到机场之前。
    去程、回程可能有转机，取第一天开头、最后一天结尾那串航班、接驳里最靠里的一班；不坐飞机的行程不限制。"""
    if not ctx.trip.days:
        return None, None
    lead = takewhile(lambda i: i.type in _TRANSIT, sorted(ctx.trip.days[0].items, key=lambda i: i.start))
    tail = takewhile(lambda i: i.type in _TRANSIT, sorted(ctx.trip.days[-1].items, key=lambda i: i.start, reverse=True))
    inbound = [i for i in lead if i.type == ItemType.FLIGHT]
    outbound = [i for i in tail if i.type == ItemType.FLIGHT]
    return (inbound[-1].end if inbound else None,
            outbound[-1].start - timedelta(minutes=_airport_min(ctx, outbound[-1])) if outbound else None)


def r20_meals(ctx: ValidationContext) -> list[Issue]:
    issues = []
    arrived, leaving = _on_trip(ctx)
    for day in ctx.trip.days:
        for name, (w_start, w_end) in (("午餐", ctx.config.lunch_window), ("晚餐", ctx.config.dinner_window)):
            ws = datetime.combine(day.date, w_start, ctx.tz)
            we = datetime.combine(day.date, w_end, ctx.tz)
            # 还没落地、或者已经要去机场回家了：这顿在家、机场或飞机上吃，不用排进行程
            if min(we, leaving or we) - max(ws, arrived or ws) < timedelta(minutes=MEAL_MIN):
                continue
            in_air = any(i.type == ItemType.FLIGHT and i.start <= ws and i.end >= we for i in day.items)
            has_meal = any(i.type == ItemType.MEAL and i.start < we and i.end > ws for i in day.items)
            if not in_air and not has_meal:
                issues.append(_issue("R20", WARNING,
                                     f"{_md(day.date)} {w_start:%H:%M}–{w_end:%H:%M} 之间没有安排{name}",
                                     suggestions=[f"安排{name}或准备路餐"]))
    return issues


# ---------------- R25 取还车在租车柜台营业时间内 ----------------

def _desk_hours(text: str | None, default: tuple) -> tuple:
    if not text:
        return default
    a, b = text.split("-")
    return (datetime.strptime(a, "%H:%M").time(), datetime.strptime(b, "%H:%M").time())


def r25_car_desk_hours(ctx: ValidationContext) -> list[Issue]:
    """案例 V4：早班机前要还车，那时租车柜台还没开门。"""
    issues = []
    for v in ctx.trip.vehicles:
        for what, place_id, at, hours_text in (("取车", v.pickup_place_id, v.pickup_time, v.pickup_desk_hours),
                                               ("还车", v.dropoff_place_id, v.dropoff_time, v.dropoff_desk_hours)):
            opens, closes = _desk_hours(hours_text, ctx.config.car_desk_hours)
            local = ctx.local(at).time()
            if opens <= local <= closes:
                continue
            place = ctx.place(place_id)
            where = place.name_zh if place else place_id
            known = f"柜台营业时间 {hours_text}" if hours_text else "可能在租车柜台营业时间以外"
            fix = (["按合同里的营业时间外还车方式办理"] if what == "还车" and v.after_hours_return
                   else ["向租车公司确认营业时间", f"确认营业时间外怎么{what}（例如钥匙投进还车箱）", f"把{what}时间挪到营业时间内"])
            issues.append(_issue("R25", WARNING,
                                 f"{_md(ctx.local(at).date())} {ctx.hm(at)} 在{where}{what}，{known}", [v.id], fix))
    return issues


# ---------------- R26 待确认事项有待办、按时确认 ----------------

def r26_confirmations(ctx: ValidationContext) -> list[Issue]:
    """订好了但还缺细节（门牌号、入住时间、床型、停车、入住方式、租车保险……）的，要有确认待办；过了截止日期还没确认要提醒。"""
    from ..checklist import missing_confirm_todos  # 放在这里避免循环导入
    issues = []
    missing = missing_confirm_todos(ctx.trip)
    if missing:
        names = "；".join(n.todo.title for n in missing[:4]) + ("……" if len(missing) > 4 else "")
        issues.append(_issue("R26", WARNING, f"有 {len(missing)} 项待确认还没有待办：{names}",
                             sorted({i for n in missing for i in n.todo.item_ids}),
                             ["在「待办」页签点「生成待确认待办」，分给具体的人"]))
    if ctx.today:
        late = [t for t in ctx.trip.todos if t.kind == "confirm" and t.status == "open" and t.due and t.due < ctx.today]
        soon = ctx.trip.meta.start_date - ctx.today <= timedelta(days=3)
        late_or_soon = late or ([t for t in ctx.trip.todos if t.kind == "confirm" and t.status == "open"] if soon else [])
        if late_or_soon:
            names = "、".join(t.title for t in late_or_soon[:3]) + ("等" if len(late_or_soon) > 3 else "")
            why = "已经过了截止日期" if late else "马上要出发了"
            issues.append(_issue("R26", WARNING, f"{why}，还有 {len(late_or_soon)} 项没确认：{names}", [],
                                 ["今天联系房东或租车公司确认", "确认后把待办标成完成"]))
    return issues


RULES = [
    r01_overlap, r02_opening_hours, r03_checkin, r04_travel, r05_daily_drive, r06_accommodation_times,
    r07_flights, r09_nights, r10_vehicles, r11_drivers, r12_drive_driver, r13_weather, r14_restrictions,
    r15_booking_todos, r16_rejected, r17_must_do, r18_places_exist, r19_heavy_days, r20_meals, r25_car_desk_hours,
    r26_confirmations,
]
