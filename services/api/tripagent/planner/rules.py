"""S0、S1：用代码处理的部分——成员 id、必玩项目记入决策、出发前待办（签证、驾照、入境申报）。

规则都是【初定】，政策会变，生成的待办都提示"出发前以官方为准"。
成员加入后自己改资料时，跟着资料走的待办也按这里的规则更新（profile_todo_ops）。
"""

import re
from datetime import date, timedelta

from ..schema.intake import MemberIntake, MustDo, TripIntake
from ..schema.memory import Decision, Requirement
from ..schema.trip import Member, Todo, TodoKind, Trip
from ..validator.context import ValidatorConfig

ENGLISH_LICENCE = ValidatorConfig().english_licence_countries
NZETA_TODOS = ("todo_nzeta", "todo_visa_check")   # 负责人是要办 NZeTA 的人


def needs_licence_translation(m: Member | MemberIntake) -> bool:
    """开车、驾照不是英文的：要办认可的英文翻译件（和校验规则 R11 的判断一样）。"""
    return bool(m.can_drive and m.licence_country and m.licence_country not in ENGLISH_LICENCE)


def needs_nzeta(passport_country: str | None) -> bool:
    """去新西兰要办 NZeTA：新西兰、澳洲护照以外的都要（还不知道护照的也先算上）。"""
    return passport_country not in ("NZ", "AU")


def _soon(start: date, today: date) -> date:
    """证件类待办的截止日期：出发前三周左右，最早一周后，最晚一个月内。"""
    return max(today + timedelta(days=7), min(start - timedelta(days=21), today + timedelta(days=30)))


def licence_todo(member_id: str, name: str, due: date) -> Todo:
    return Todo(id=f"todo_licence_{member_id.removeprefix('m_')}", kind="licence_translation",
                owner_ids=[member_id], due=due, title=f"{name}：办理驾照的认可英文翻译件")


def member_ids(intake: TripIntake) -> list[str]:
    ids, used = [], set()
    for n, m in enumerate(intake.members, 1):
        slug = re.sub(r"[^a-z0-9]+", "_", m.name.lower()).strip("_")
        mid = f"m_{slug}" if slug else f"m_{n}"
        while mid in used:
            mid = f"{mid}_{n}"
        used.add(mid)
        ids.append(mid)
    return ids


def members_from_intake(intake: TripIntake) -> list[Member]:
    rows = []
    for n, (mid, m) in enumerate(zip(member_ids(intake), intake.members)):
        rows.append(Member(
            id=mid, name=m.name, role="owner" if n == 0 else "member", passport_country=m.passport_country,
            visa=m.visa, can_drive=m.can_drive, age=m.age, licence_country=m.licence_country,
            licence_type=m.licence_type, weight_kg=m.weight_kg,
        ))
    return rows


def requirement_for(md: MustDo, places: dict) -> Requirement | None:
    """一个必玩项目的三个标记 → 校验器能检查的要求（设计规格 4.4 节）。一个都没锁定时返回 None。"""
    req = Requirement()
    if md.fixed_activity and md.activity_tag:
        req.activity_tag = md.activity_tag
    if md.fixed_place and md.place_hint:
        hint = md.place_hint.strip()
        cities = {p.city for p in places.values() if p.city and p.city.lower() == hint.lower()}
        hits = [p for p in places.values() if hint in (p.name_zh, p.name_en) or hint in p.name_zh
                or hint.lower() in p.name_en.lower()]
        hit_cities = {p.city for p in hits if p.city}
        if cities:                                    # 写的是城镇名，例如 Glenorchy
            req.city = cities.pop()
        elif any(p.kind == "town" for p in hits):
            req.city = next(p.city for p in hits if p.kind == "town")
        elif len(hits) > 1 and len(hit_cities) == 1:  # 例如"格林诺奇"同时匹配那里的几家商家和景点
            req.city = hit_cities.pop()
        elif hits:                                    # 指定了具体商家
            req.place_id = hits[0].id
    if md.fixed_date and md.date:
        req.date = md.date
    return req if any(v is not None for v in req.model_dump().values()) else None


def must_do_content(md: MustDo, req: Requirement) -> str:
    flags = "、".join(x for x, on in (("项目", req.activity_tag), ("地点", req.place_id or req.city),
                                      ("日期", req.date)) if on)
    return f"必玩：{md.what}（锁定{flags}）"


def must_do_decisions(intake: TripIntake, places: dict) -> list[Decision]:
    """必玩项目和它的三个标记 → 决策账本里的硬约束（设计规格 4.4 节）。"""
    decisions = []
    ids = member_ids(intake)
    for mid, m in zip(ids, intake.members):
        for md in m.must_do:
            if (req := requirement_for(md, places)) is None:
                continue
            decisions.append(Decision(id=f"D-{len(decisions) + 1:03d}", content=must_do_content(md, req),
                                      kind="hard_constraint", priority="must", requirement=req, proposed_by=mid))
    return decisions


def predeparture_todos(intake: TripIntake, today: date) -> list[Todo]:
    """S1：签证、入境申报、驾照翻译件。"""
    todos: list[Todo] = []
    start = intake.start_date or today + timedelta(days=60)
    soon = _soon(start, today)
    ids = member_ids(intake)
    if intake.destination == "NZ":
        need_eta = [mid for mid, m in zip(ids, intake.members) if needs_nzeta(m.passport_country)]
        if need_eta:
            todos.append(Todo(id="todo_nzeta", kind="visa", owner_ids=need_eta, due=soon,
                              title="申请 NZeTA 电子旅行授权并缴纳 IVL 旅游税",
                              notes=["持澳洲有效签证的中国护照旅客，按当前政策通常可申请 NZeTA；出发前以新西兰移民局官网为准"]))
            todos.append(Todo(id="todo_visa_check", kind="visa", owner_ids=need_eta, due=soon,
                              title="检查护照有效期、澳洲签证有效期及返澳资格"))
        todos.append(Todo(id="todo_nztd", kind="visa", due=start - timedelta(days=1),
                          title="出发前 24 小时内填写 NZTD 新西兰入境申报"))
    for mid, m in zip(ids, intake.members):
        if needs_licence_translation(m):
            todos.append(licence_todo(mid, m.name, soon))
    todos.append(Todo(id="todo_card_fees", kind="payment", due=soon,
                      title="确认银行卡的境外手续费，准备一张备用卡和少量现金"))
    return todos


def profile_todo_ops(trip: Trip, member: Member, today: date) -> list[dict]:
    """成员改了资料之后，跟着资料走的待办怎么改（规则和建行程时一样）。trip 里已经是新资料，返回改动操作：
    - 开车、驾照不是英文的，要有办翻译件的待办；不再需要时，他还没完成的那条去掉；
    - 去新西兰：NZeTA、护照签证检查这两条的负责人跟着护照走。只调整写明了负责人、还没完成的
      （没写负责人表示全员，是手写的，不动）；没人需要了就去掉。"""
    ops: list[dict] = []
    mid = member.id
    start = trip.meta.start_date
    licence = [t for t in trip.todos if t.kind == TodoKind.LICENCE_TRANSLATION]
    if needs_licence_translation(member):
        if not any(not t.owner_ids or mid in t.owner_ids for t in licence):   # 和 R11 一样：没写负责人的算全员
            todo = licence_todo(mid, member.name, max(today, min(_soon(start, today), start - timedelta(days=1))))
            taken, base, n = {t.id for t in trip.todos}, todo.id, 2
            while todo.id in taken:
                todo, n = todo.model_copy(update={"id": f"{base}_{n}"}), n + 1
            ops.append({"op": "add", "collection": "todos", "value": todo.model_dump(mode="json")})
    else:
        for t in licence:
            if t.status == "open" and mid in t.owner_ids:
                rest = [o for o in t.owner_ids if o != mid]
                ops.append({"op": "update", "collection": "todos", "id": t.id, "set": {"owner_ids": rest}} if rest
                           else {"op": "remove", "collection": "todos", "id": t.id})
    if trip.meta.destination == "NZ":
        order = [m.id for m in trip.members]
        for t in trip.todos:
            if t.id not in NZETA_TODOS or not t.owner_ids or t.status != "open":
                continue
            owners = (set(t.owner_ids) - {mid}) | ({mid} if needs_nzeta(member.passport_country) else set())
            if owners == set(t.owner_ids):
                continue
            new = [o for o in order if o in owners]
            ops.append({"op": "update", "collection": "todos", "id": t.id, "set": {"owner_ids": new}} if new
                       else {"op": "remove", "collection": "todos", "id": t.id})
    return ops
