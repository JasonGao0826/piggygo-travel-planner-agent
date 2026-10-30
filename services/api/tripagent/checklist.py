"""出发前清单（参照我们 V3、V4 计划的做法）：

- 每天要带的东西：从当天的项目、地点限制、航班、取车推出来，写在每天时间表开头；
- 出发前清单：证件、钱、手机、插头、保险、衣物、药品，打勾用；
- 待确认事项：订好了但还缺细节的（门牌号、入住时间、床型、停车、入住方式、租车保险……），
  每一项都应该变成有负责人和截止日期的待办，出发前还没确认完就提醒（校验规则 R26）。
"""

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .schema.place import Place
from .schema.trip import BookingStatus, ItemType, Todo, Trip

# 项目类型 → 要带的东西
BRING_BY_TAG = {
    "rafting": ["泳衣或速干衣", "毛巾和换洗衣物", "能下水的鞋"],
    "shark_boat": ["防风外套"],
    "cruise": ["防风防水外套", "晕船药（容易晕船的人）"],
    "fjord": ["防风防水外套"],
    "paragliding": ["保暖外套", "运动鞋"],
    "hiking": ["登山鞋或运动鞋", "水和零食", "防晒霜", "防风外套"],
    "short_walk": ["运动鞋"],
    "horse_riding": ["长裤", "包脚的鞋"],
    "glowworm": ["保暖外套"],
    "geothermal": ["运动鞋（硫磺味重，别穿新鞋）"],
    "spring": ["泳衣", "毛巾"],
    "luge": ["运动鞋"],
    "shooting": [],
}
CN_LICENCE_NEEDS_TRANSLATION = {"CN"}


def _day_items(trip: Trip):
    for day in trip.days:
        yield day, day.items


def day_bring(trip: Trip, places: dict[str, Place]) -> dict[date, list[str]]:
    """每天要带的东西（去重，按出现顺序）。"""
    places = {**places, **{p.id: p for p in trip.custom_places}}
    out: dict[date, list[str]] = {}
    for day, items in _day_items(trip):
        bring: list[str] = []
        for item in items:
            place = places.get(item.place_id or "")
            if item.type == ItemType.FLIGHT:
                bring += ["护照", "登机牌或电子行程单"]
                if item.flight and item.flight.international:
                    bring += ["NZeTA 和入境申报（NZTD）"] if trip.meta.destination == "NZ" else ["入境需要的签证材料"]
            elif item.type == ItemType.CAR_PICKUP:
                bring += ["驾照原件", "订车时用的信用卡（押金预授权）"]
                vehicle = next((v for v in trip.vehicles if v.id == item.vehicle_id), None)
                drivers = [trip.member(d) for d in (vehicle.drivers if vehicle else [])]
                if any(m and m.licence_country in CN_LICENCE_NEEDS_TRANSLATION for m in drivers):
                    bring.append("中国驾照的英文翻译件")
            elif item.type == ItemType.ACTIVITY:
                if item.booking_status == BookingStatus.BOOKED:
                    bring.append(f"{item.title} 的预订凭证（凭证夹里有）")
                if place:
                    for tag in place.tags:
                        bring += BRING_BY_TAG.get(tag, [])
                    for r in place.restrictions:
                        if r.code == "id_document":
                            bring.append("护照原件（项目要求）")
                    if place.weather_dependent and not item.optional:
                        bring.append("出发前看一眼天气预报")
        drive_min = sum((i.end - i.start).total_seconds() / 60 for i in items if i.type == ItemType.DRIVE)
        if drive_min >= 240:
            bring.append("路上吃的和水（开车 4 小时以上）")
        if bring:
            out[day.date] = list(dict.fromkeys(bring))
    return out


def departure_checklist(trip: Trip) -> list[tuple[str, list[str]]]:
    """出发前清单，按类分组。"""
    nz = trip.meta.destination == "NZ"
    drivers = [m for m in trip.members if m.can_drive]
    cn_drivers = [m.name for m in drivers if m.licence_country in CN_LICENCE_NEEDS_TRANSLATION]
    docs = ["护照（有效期至少还有 3 个月以上，回程后也要能返澳）", "澳洲签证状态（学生签能否出境后再入境）"]
    if nz:
        docs += ["NZeTA 电子旅行授权和 IVL 旅游税", "出发前 24 小时内填 NZTD 入境申报"]
    if drivers:
        docs.append("驾照原件：" + "、".join(m.name for m in drivers))
    if cn_drivers:
        docs.append("中国驾照的英文翻译件：" + "、".join(cn_drivers))
    has_car = bool(trip.vehicles)
    return [
        ("证件", docs),
        ("钱", ["一张免境外手续费的信用卡（租车押金要用信用卡）", "一张备用卡，和主卡分开放", "少量纽币现金" if nz else "少量现金",
               "手机钱包（Apple Pay / Google Pay）绑好卡"]),
        ("手机和电子", ["国际漫游或当地电话卡", "离线地图（山区没信号）", "转换插头（新西兰、澳洲都是 I 型）", "充电宝"]
         + (["车载充电线和手机支架"] if has_car else [])),
        ("保险", ["旅行保险（医疗、行李、取消）"] + (["确认租车保险的自付额"] if has_car else [])),
        ("衣物", ["防风防水外套", "保暖抓绒或羽绒（山区早晚冷）", "好走的鞋", "泳衣（漂流、温泉）", "墨镜和防晒霜（紫外线强）"]),
        ("药品", ["常用药和晕车晕船药", "创可贴"]),
        ("出发前一天", ["在凭证夹里下载好所有凭证，或打开行程包", "看一眼实时情况：路况、天气预警"]),
    ]


# ---------------- 待确认事项 ----------------

@dataclass
class ConfirmNeed:
    todo: Todo
    keywords: tuple[str, ...]
    record_name: str


def _due(trip: Trip, day: date, days_before: int = 7) -> date:
    return max(day - timedelta(days=days_before), trip.meta.start_date - timedelta(days=21))


def confirm_needs(trip: Trip) -> list[ConfirmNeed]:
    """订好了但还缺细节的地方，每一项对应一条确认待办（负责人默认是发起人）。"""
    owner = next((m.id for m in trip.members if m.role == "owner"), None)
    owners = [owner] if owner else []
    req = trip.meta.prefs.room_requirements
    needs: list[ConfirmNeed] = []

    def add(record_id: str, field: str, title: str, due: date, keywords: tuple[str, ...], name: str):
        needs.append(ConfirmNeed(Todo(id=f"todo_confirm_{record_id}_{field}", title=title, kind="confirm",
                                      owner_ids=owners, due=due, item_ids=[record_id]), keywords, name))

    for a in trip.accommodations:
        if a.booking_status != BookingStatus.BOOKED:
            continue
        due = _due(trip, a.check_in)
        label = f"{a.city}住宿（{a.name}）"
        if not a.address or "待确认" in a.address:
            add(a.id, "address", f"向房东确认{label}的门牌地址", due, ("门牌", "地址"), a.city)
        if a.earliest_checkin is None:
            add(a.id, "checkin", f"确认{label}最早几点能入住", due, ("入住时间", "几点能入住", "最早入住"), a.city)
        if a.latest_checkout is None:
            add(a.id, "checkout", f"确认{label}最晚几点退房", due, ("退房",), a.city)
        known = " ".join([a.rooms, *a.notes])   # 房型说明和备注里已经写了的，不再问
        if not a.beds and not a.rooms:
            add(a.id, "beds", f"确认{label}的房型和床" + (f"满足：{req}" if req else "够不够分"), due, ("床", "房型"), a.city)
        if a.parking is None and trip.vehicles and "停车" not in known:
            add(a.id, "parking", f"确认{label}有没有停车位", due, ("停车",), a.city)
        if not a.self_checkin and not any(k in known for k in ("自助入住", "钥匙盒", "密码箱", "见房东", "前台")):
            add(a.id, "self_checkin", f"确认{label}怎么入住（自助密码箱还是见房东）", due, ("入住方式", "自助入住", "钥匙", "密码"), a.city)
    for v in trip.vehicles:
        if v.booking_status != BookingStatus.BOOKED:
            continue
        due = _due(trip, v.pickup_time.astimezone(ZoneInfo(trip.meta.local_timezone)).date())
        seg = next((s.name for s in trip.segments if s.vehicle_id == v.id), v.model or v.id)
        if not v.insurance:
            add(v.id, "insurance", f"确认{seg}租车的保险和自付额", due, ("保险", "自付额"), seg)
        if not v.dropoff_desk_hours and not v.after_hours_return:
            add(v.id, "desk_hours", f"确认{seg}租车柜台的营业时间和营业时间外怎么还车", due, ("营业时间", "还车"), seg)
    return needs


def missing_confirm_todos(trip: Trip) -> list[ConfirmNeed]:
    """还没有对应待办的待确认事项。已经有同 id 的待办，或者同一处住宿、同一件事的确认待办（手写的也算），就不算缺。"""
    existing = [t for t in trip.todos if t.kind == "confirm"]
    out = []
    for need in confirm_needs(trip):
        covered = any(t.id == need.todo.id for t in existing) or any(
            (need.todo.item_ids[0] in t.item_ids or need.record_name in t.title) and any(k in t.title for k in need.keywords)
            for t in existing)
        if not covered:
            out.append(need)
    return out


def bring_by_day_text(trip: Trip, places: dict[str, Place]) -> dict[date, str]:
    return {d: "带上：" + "、".join(items) for d, items in day_bring(trip, places).items()}


__all__ = ["ConfirmNeed", "bring_by_day_text", "confirm_needs", "day_bring", "departure_checklist", "missing_confirm_todos"]
