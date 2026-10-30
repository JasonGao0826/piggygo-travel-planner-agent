"""多人协作的对话工具（设计规格第 13 节）：看大家的偏好、记下自己的偏好、发起投票、看投票。"""

from datetime import datetime, timezone
from typing import Callable

from ..collab import CollabService
from ..guides import GuideError, GuideService
from ..llm.base import LLM
from ..mcp_gateway import untrusted
from ..collab.service import deadline_in
from ..llm.base import Tool
from ..schema.collab import PollOptionDraft
from ..schema.intake import MustDo
from ..service import TripService


def _no_llm() -> LLM:
    raise GuideError("现在读不了攻略")


# 一轮对话里最多读几篇攻略（每篇都要去小红书读网页、再调用模型）
MAX_GUIDES_PER_TURN = 2


def build_collab_tools(service: TripService, trip_id: str, member_id: str,
                       llm_for_guides: Callable[[], LLM] | None = None, guides: GuideService | None = None) -> list[Tool]:
    collab = CollabService(service)
    llm_for_guides = llm_for_guides or _no_llm
    guides = guides or GuideService(service)
    read_count = [0]

    def get_preferences(_args):
        names = collab.names(trip_id)
        prefs = collab.preferences(trip_id)
        summary = collab.summary(trip_id)
        return untrusted("成员自己填写的偏好", datetime.now(timezone.utc), {
            "每个人的偏好": {names.get(m, m): (p.model_dump(mode="json", exclude={"member_id"}) if not p.is_empty() else "还没填")
                        for m, p in prefs.items()},
            "还没填的人": [n for m, n in names.items() if m not in prefs or prefs[m].is_empty()],
            "最近一次汇总": summary.model_dump(mode="json", exclude={"based_on"}) if summary else "还没汇总过",
            "汇总之后有人改过偏好": collab.summary_is_stale(trip_id, summary) if summary else None,
        })

    def update_my_preferences(args):
        add = {k: v for k, v in (args.get("add") or {}).items() if v}
        if "must_do" in add:
            add["must_do"] = [MustDo.model_validate(x) for x in add["must_do"]]
        p = collab.update_preferences(trip_id, member_id, add=add, remove={k: v for k, v in (args.get("remove") or {}).items() if v},
                                      set_={k: v for k, v in (args.get("set") or {}).items() if v is not None})
        return {"已记下": p.model_dump(mode="json", exclude={"member_id", "updated_at"}),
                "提示": "偏好已保存；汇总时会和其他人的偏好一起比较。"}

    def create_poll(args):
        poll = collab.create_poll(trip_id, "agent", args["question"],
                                  [PollOptionDraft.model_validate(o) for o in args["options"]],
                                  deadline=deadline_in(args.get("deadline_hours")), divergence_id=args.get("divergence_id"))
        return {"poll_id": poll.id, "options": [o.model_dump() for o in poll.options],
                "提示": "投票已发起，大家在网页的「大家」页签里投；所有人投完或到截止时间会自动结束，结果写进决策账本。"}

    def read_guide_link(args):
        if read_count[0] >= MAX_GUIDES_PER_TURN:
            raise GuideError(f"一次最多读 {MAX_GUIDES_PER_TURN} 篇攻略，其他的请用户下次再发")
        read_count[0] += 1
        g = guides.read(trip_id, member_id, llm_for_guides(), text=args["text"])
        names = {pid: p.name_zh for pid, p in service.places_for(service.current(trip_id)).items()}
        return untrusted(g.source, g.added_at, {
            "标题": g.title, "链接": g.url, "概括": g.extraction.summary,
            "提到的地点": [{"名称": p.name, "知识库地点": names.get(p.place_id) if p.place_id else "知识库里没有，要先核实",
                       "place_id": p.place_id, "说了什么": p.what, "建议": p.tips} for p in g.extraction.places],
            "价格（攻略里说的，可能过时）": g.extraction.prices, "预约建议": g.extraction.booking_tips, "注意事项": g.extraction.cautions,
            "提示": "攻略只当参考：价格、营业时间要以官网为准。问用户要不要把提到的地点加进想去或必玩，用 update_my_preferences 记下。",
        })

    def list_polls(_args):
        names = collab.names(trip_id)
        rows = []
        for p in collab.polls(trip_id):
            tally = p.tally()
            rows.append({
                "id": p.id, "question": p.question, "status": p.status,
                "options": [{"id": o.id, "label": o.label, "impact": o.impact,
                             "votes": [names.get(m, m) for m in tally.get(o.id, [])]} for o in p.options],
                "result": p.option(p.result).label if p.result else None, "decision_id": p.decision_id,
                "not_voted": [n for m, n in names.items() if m not in p.votes] if p.status == "open" else [],
            })
        return untrusted("成员发起的投票", datetime.now(timezone.utc), rows) if rows else "还没有投票。"

    S = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}  # noqa: E731
    STR = {"type": "string"}
    LIST = {"type": "array", "items": STR}
    return [
        Tool("get_preferences", "每个成员自己填的偏好（必玩、想去、不想玩、节奏、开车时长、预算、饮食）和最近一次意见汇总。", S({}),
             get_preferences),
        Tool("update_my_preferences", "记下正在对话的这个成员自己的偏好（只能改他自己的）。说了新的想玩、不想玩、节奏、开车时长等时调用。", S({
            "add": {"type": "object", "properties": {
                "wishlist": LIST, "avoid": LIST, "diet": LIST,
                "must_do": {"type": "array", "items": {"type": "object", "properties": {
                    "what": STR, "activity_tag": STR, "place_hint": STR, "date": {**STR, "description": "YYYY-MM-DD"},
                    "fixed_activity": {"type": "boolean"}, "fixed_place": {"type": "boolean"}, "fixed_date": {"type": "boolean"},
                }, "required": ["what"]}},
            }},
            "remove": {"type": "object", "properties": {"wishlist": LIST, "avoid": LIST, "diet": LIST}},
            "set": {"type": "object", "properties": {
                "thrill_level": {"type": "string", "enum": ["low", "mid", "high"]},
                "pace": {"type": "string", "enum": ["relaxed", "balanced", "packed"]},
                "max_daily_drive_hours": {"type": "number"}, "earliest_start": {**STR, "description": "HH:MM"},
                "notes": STR,
            }},
        }), update_my_preferences),
        Tool("create_poll", "发起投票，让大家一起决定有分歧的事。每个选项写清楚选它会怎样。", S({
            "question": STR,
            "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": {"type": "object", "properties": {
                "label": STR, "impact": {**STR, "description": "选这个会怎样，例如要分组、多开 2 小时车、少去一个景点"}},
                "required": ["label", "impact"]}},
            "deadline_hours": {"type": "number", "description": "多少小时后截止；不填就等大家都投完"},
            "divergence_id": {**STR, "description": "对应汇总里的分歧 id（有的话）"},
        }, ["question", "options"]), create_poll),
        Tool("list_polls", "所有投票：选项、谁投了什么、结果、还有谁没投。", S({}), list_polls),
        Tool("read_guide_link", "读取用户贴的小红书攻略链接（或整段分享文字），提取地点、价格、预约建议和注意事项，并对应到知识库。", S({
            "text": {**STR, "description": "用户贴的链接或整段分享文字"},
        }, ["text"]), read_guide_link),
    ]
