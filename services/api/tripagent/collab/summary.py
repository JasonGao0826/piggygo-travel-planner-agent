"""意见汇总（设计规格 13.3 节）：共识、分歧、可以分组。

分两部分做：
- 节奏、开车时长、出发时间、预算、刺激程度这些有明确取值的，由代码比较；
- 想玩什么、不想玩什么（例如"怕高"和"跳伞"冲突）要理解语义，交给模型，并让模型给出投票选项和影响。
"""

import hashlib
from datetime import time

from ..schema.collab import (PACE_LABEL, THRILL_LABEL, Divergence, GroupOption, MemberPreferences, PollDraft,
                             PollOptionDraft, Position, SummaryItem)
from ..schema.common import Model

PROMPT_VERSION = "summary-v2"

SUMMARY = """你在帮一群一起旅行的朋友汇总每个人的偏好。输入里有每个成员的偏好、当前行程概况、知识库里的项目类型。
请输出：
- consensus：大家都想去的，或者有人想去、没人反对的项目和地方。detail 写清楚是谁提的、行程里有没有安排。
- divergences：有人想去而有人明确不想去或不敢去（例如一个人想跳伞，另一个人怕高）；行程里已安排、但有人不想参加的项目；
  两个人想去的地方方向相反、时间上排不下。positions 写每一方的成员和立场；suggestion 给中立的解决思路，不替大家做决定。
  能用投票解决的，给 poll：一个问题和 2–4 个选项，每个选项的 impact 写清楚选它会怎样（例如"要分两组，多一辆车的接送"
  "少去一个景点""多开 2 小时车"）。
- groupable：只有部分人想玩、可以分组进行的项目；member_ids 是想玩的人，others 写其他人同时可以做什么（只是建议）。
- must_do_tags：每个必玩项目对应的项目类型，只能从给出的"项目类型"里选，而且要是同一种项目（例如 骑马→horse_riding、
  滑翔伞→paragliding）。只是大类相近的不算（例如跳伞不能填 adventure，蹦极不能填 adventure），这时写 null。
  index 是这个成员必玩列表里的序号，从 0 开始。
规则：
- 只根据输入，不编造成员没说过的偏好。member_ids 只能用给出的成员 id。
- 节奏、每天开车时长、出发时间、预算、刺激程度由程序比较，你不用输出这几类。
- topic、detail、stance、suggestion、问题和选项是给用户看的：用中文和成员称呼，不要出现成员 id、地点 id 或英文字段名。
- 同一件事只在一个类别里出现一次。"""


class LlmPosition(Model):
    member_ids: list[str]
    stance: str


class LlmDivergence(Model):
    topic: str
    detail: str
    positions: list[LlmPosition]
    suggestion: str
    poll: PollDraft | None


class MustDoTag(Model):
    member_id: str
    index: int
    activity_tag: str | None


class ActivitySummary(Model):
    consensus: list[SummaryItem]
    divergences: list[LlmDivergence]
    groupable: list[GroupOption]
    must_do_tags: list[MustDoTag]


def divergence_id(kind: str, topic: str) -> str:
    """同一类、同一主题的分歧每次汇总都得到同一个 id，已经发起的投票能对上。"""
    return f"dv_{kind}_{hashlib.sha1(topic.encode('utf-8')).hexdigest()[:6]}"


def _groups(values: dict[str, object]) -> dict[object, list[str]]:
    out: dict[object, list[str]] = {}
    for member_id, v in values.items():
        out.setdefault(v, []).append(member_id)
    return out


def _who(ids: list[str], names: dict[str, str]) -> str:
    return "、".join(names.get(i, i) for i in ids)


def _hm(t: time) -> str:
    return f"{t.hour}:{t.minute:02d}"


def structured_summary(prefs: list[MemberPreferences], names: dict[str, str]) -> tuple[list[SummaryItem], list[Divergence]]:
    """比较有明确取值的偏好。至少两个人填了同一项才比较。"""
    consensus: list[SummaryItem] = []
    divergences: list[Divergence] = []

    def add(kind, topic, detail, positions, suggestion, poll=None):
        divergences.append(Divergence(id=divergence_id(kind, topic), topic=topic, kind=kind, detail=detail,
                                      positions=positions, suggestion=suggestion, poll=poll))

    # 节奏
    pace = {p.member_id: p.pace for p in prefs if p.pace}
    if len(pace) >= 2:
        groups = _groups(pace)
        if len(groups) == 1:
            value, ids = next(iter(groups.items()))
            consensus.append(SummaryItem(topic="每天的节奏", detail=f"大家都想{PACE_LABEL[value]}", member_ids=ids))
        else:
            impact = {"relaxed": "每天 1–2 个点，留出休息时间，去的地方少一些",
                      "balanced": "每天 2–4 个点，有张有弛",
                      "packed": "每天 4–5 个点，去的地方多，比较累"}
            order = [v for v in ("relaxed", "balanced", "packed") if v in groups or v == "balanced"]
            add("pace", "每天的节奏",
                "；".join(f"{_who(ids, names)} 想{PACE_LABEL[v]}" for v, ids in groups.items()),
                [Position(member_ids=ids, stance=f"想{PACE_LABEL[v]}") for v, ids in groups.items()],
                "可以按适中排，长途日之后安排一个轻松的半天；想多玩的人可以在空档自己加点",
                PollDraft(question="每天的节奏按哪种来？",
                          options=[PollOptionDraft(label=PACE_LABEL[v], impact=impact[v]) for v in order]))

    # 每天开车时长
    drive = {p.member_id: p.max_daily_drive_hours for p in prefs if p.max_daily_drive_hours}
    if len(drive) >= 2:
        low, high = min(drive.values()), max(drive.values())
        if high - low >= 2:
            groups = _groups(drive)
            add("drive", "每天最多开车几小时",
                "；".join(f"{_who(ids, names)} 最多 {h:g} 小时" for h, ids in sorted(groups.items())),
                [Position(member_ids=ids, stance=f"最多 {h:g} 小时") for h, ids in sorted(groups.items())],
                f"一般按 {low:g} 小时排；超过的长途日提前说好，司机轮换",
                PollDraft(question="每天开车最多按几小时排？", options=[
                    PollOptionDraft(label=f"不超过 {low:g} 小时", impact="开车轻松，去的地方少一些，或者要多住一晚"),
                    PollOptionDraft(label=f"不超过 {high:g} 小时", impact="能多去几个地方，开车更累，司机要轮换"),
                ]))
        else:
            consensus.append(SummaryItem(topic="每天开车", detail=f"每天开车不超过 {low:g} 小时", member_ids=list(drive)))

    # 早上出发时间
    start = {p.member_id: p.earliest_start for p in prefs if p.earliest_start}
    if len(start) >= 2:
        early, late = min(start.values()), max(start.values())
        minutes = (late.hour * 60 + late.minute) - (early.hour * 60 + early.minute)
        if minutes >= 90:
            groups = _groups(start)
            add("start", "早上几点出发",
                "；".join(f"{_who(ids, names)} {_hm(t)} 以后" for t, ids in sorted(groups.items())),
                [Position(member_ids=ids, stance=f"{_hm(t)} 以后出发") for t, ids in sorted(groups.items())],
                f"平时按 {_hm(late)} 出发；需要赶早的日子（早班机、日出、固定场次）提前说好",
                PollDraft(question="平时早上几点出发？", options=[
                    PollOptionDraft(label=f"{_hm(early)} 出发", impact="每天能多玩一两个点，起得早"),
                    PollOptionDraft(label=f"{_hm(late)} 出发", impact="睡得够，下午和傍晚的安排更满"),
                ]))
        else:
            consensus.append(SummaryItem(topic="出发时间", detail=f"早上 {_hm(late)} 以后出发", member_ids=list(start)))

    # 预算（同币种才比较）
    by_currency: dict[str, dict[str, float]] = {}
    for p in prefs:
        if p.budget_per_person:
            by_currency.setdefault(p.budget_per_person.currency, {})[p.member_id] = float(p.budget_per_person.amount)
    for currency, budgets in by_currency.items():
        if len(budgets) < 2:
            continue
        low, high = min(budgets.values()), max(budgets.values())
        if low > 0 and high / low >= 1.3:
            groups = _groups(budgets)
            add("budget", "每人预算",
                "；".join(f"{_who(ids, names)} {v:,.0f} {currency}" for v, ids in sorted(groups.items())),
                [Position(member_ids=ids, stance=f"{v:,.0f} {currency}") for v, ids in sorted(groups.items())],
                f"共同的部分（住宿、租车、油费）按 {low:,.0f} {currency} 左右安排；想升级的项目各自加钱，不平摊",
                PollDraft(question="住宿和共同项目按哪个预算安排？", options=[
                    PollOptionDraft(label=f"每人约 {low:,.0f} {currency}", impact="住得简单一些，大家都负担得起"),
                    PollOptionDraft(label=f"每人约 {high:,.0f} {currency}", impact="住得更好、项目更多，有人会超预算"),
                ]))
        else:
            consensus.append(SummaryItem(topic="预算", detail=f"每人预算在 {low:,.0f}–{high:,.0f} {currency} 之间，差不多",
                                         member_ids=list(budgets)))

    # 刺激程度
    thrill = {p.member_id: p.thrill_level for p in prefs if p.thrill_level}
    groups = _groups(thrill)
    if "low" in groups and "high" in groups:
        add("thrill", "刺激项目",
            "；".join(f"{_who(ids, names)} 刺激程度接受度{THRILL_LABEL[v]}" for v, ids in groups.items()),
            [Position(member_ids=ids, stance=f"接受度{THRILL_LABEL[v]}") for v, ids in groups.items()],
            "滑翔伞、跳伞、漂流这类项目可以分组：想玩的人去，其他人同时去附近的景点或咖啡馆")

    # 饮食：不是分歧，是大家都要照顾的
    diet = [(p.member_id, "、".join(p.diet)) for p in prefs if p.diet]
    if diet:
        consensus.append(SummaryItem(topic="饮食", detail="；".join(f"{names.get(m, m)}：{d}" for m, d in diet),
                                     member_ids=[m for m, _ in diet]))
    return consensus, divergences


def preference_lines(prefs: list[MemberPreferences], names: dict[str, str], member_ids: list[str]) -> list[str]:
    """每人一行的偏好摘要，给模型看。"""
    by_id = {p.member_id: p for p in prefs}
    lines = []
    for mid in member_ids:
        p = by_id.get(mid)
        if p is None or p.is_empty():
            lines.append(f"- {names.get(mid, mid)}（{mid}）：还没填偏好")
            continue
        parts = []
        if p.must_do:
            items = []
            for n, md in enumerate(p.must_do):
                locks = "、".join(x for x, on in (("项目", md.fixed_activity), ("地点", md.fixed_place),
                                                  ("日期", md.fixed_date)) if on)
                extra = "，".join(x for x in (md.place_hint and f"在{md.place_hint}", md.date and f"{md.date}") if x)
                items.append(f"[{n}] {md.what}" + (f"（{extra}）" if extra else "") + (f"（锁定{locks}）" if locks else ""))
            parts.append("必玩：" + "；".join(items))
        if p.wishlist:
            parts.append("想去：" + "、".join(p.wishlist))
        if p.avoid:
            parts.append("不想玩/不敢玩：" + "、".join(p.avoid))
        if p.thrill_level:
            parts.append(f"刺激程度接受度{THRILL_LABEL[p.thrill_level]}")
        if p.pace:
            parts.append(f"节奏{PACE_LABEL[p.pace]}")
        if p.max_daily_drive_hours:
            parts.append(f"每天开车不超过 {p.max_daily_drive_hours:g} 小时")
        if p.earliest_start:
            parts.append(f"{_hm(p.earliest_start)} 以后出发")
        if p.budget_per_person:
            parts.append(f"预算每人 {p.budget_per_person.amount:,.0f} {p.budget_per_person.currency}")
        if p.diet:
            parts.append("饮食：" + "、".join(p.diet))
        if p.notes:
            parts.append(f"备注：{p.notes}")
        lines.append(f"- {names.get(mid, mid)}（{mid}）：" + "；".join(parts))
    return lines


def clean_activity_summary(raw: ActivitySummary, member_ids: set[str]) -> tuple[list[SummaryItem], list[Divergence],
                                                                              list[GroupOption]]:
    """丢掉模型写错的成员 id，分歧补上稳定的 id。"""
    def ok(ids: list[str]) -> list[str]:
        return [i for i in ids if i in member_ids]

    consensus = [c.model_copy(update={"member_ids": ok(c.member_ids)}) for c in raw.consensus]
    divergences = []
    for d in raw.divergences:
        positions = [Position(member_ids=ok(p.member_ids), stance=p.stance) for p in d.positions if ok(p.member_ids)]
        poll = d.poll if d.poll and len([o for o in d.poll.options if o.label.strip()]) >= 2 else None
        divergences.append(Divergence(id=divergence_id("activity", d.topic), topic=d.topic, kind="activity",
                                      detail=d.detail, positions=positions, suggestion=d.suggestion, poll=poll))
    groupable = [g.model_copy(update={"member_ids": ok(g.member_ids)}) for g in raw.groupable]
    return consensus, divergences, groupable
