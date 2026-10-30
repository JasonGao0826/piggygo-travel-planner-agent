"""agent 能用的工具（设计规格第 10 节）。只读工具和写入工具分开；行程的写入只能通过 propose_patch。"""

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from ..llm.base import Tool
from ..mcp_gateway import MCPGateway, untrusted
from ..schema.memory import Decision, Evidence, Patch
from ..service import TripService
from ..state import apply_ops, describe
from .context import Formatter, decisions_text, trip_summary
from .collab_tools import build_collab_tools
from .external_tools import build_external_tools
from .money_tools import build_money_tools

OBJ = {"type": "object"}


@dataclass
class TurnLog:
    """一轮对话里 agent 做了哪些写入操作。"""

    patches: list[Patch] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


def build_tools(service: TripService, trip_id: str, member_id: str, log: TurnLog,
                gateway: MCPGateway | None = None, llm_for_guides=None, guides=None) -> list[Tool]:
    def trip():
        return service.current(trip_id)

    def places():
        return service.places_for(trip())

    def fmt():
        return Formatter(trip(), places())

    # ---------- 只读 ----------

    def get_trip_summary(_args):
        return trip_summary(trip(), places())

    def get_day(args):
        d = date.fromisoformat(args["date"])
        day = next((x for x in trip().days if x.date == d), None)
        if day is None:
            raise ValueError(f"行程里没有 {d} 这一天")
        f = fmt()
        return {"date": str(d), "route": day.route, "night_city": day.night_city,
                "items": [f.item_dict(i) for i in day.items]}

    def get_item(args):
        item = trip().find_item(args["item_id"])
        if item is None:
            raise ValueError(f"找不到事件 {args['item_id']}")
        return item.model_dump(mode="json")

    def search_places(args):
        kw = (args.get("keyword") or "").lower()
        city, tag = args.get("city"), (args.get("tag") or "").lower()
        rows = []
        for p in places().values():
            text = f"{p.id} {p.name_zh} {p.name_en} {p.city} {' '.join(p.tags)}".lower()
            if (kw and kw not in text) or (city and p.city != city) or (tag and tag not in p.tags):
                continue
            rows.append({"id": p.id, "name": f"{p.name_zh} / {p.name_en}", "kind": p.kind, "city": p.city,
                         "region": p.region, "tags": p.tags, "booking_required": p.booking.required})
        return rows[:20] or "没有找到匹配的地点。知识库里没有的地点不能直接加进行程，要先核实。"

    def get_place(args):
        p = places().get(args["place_id"])
        if p is None:
            raise ValueError(f"知识库里没有地点 {args['place_id']}")
        return p.model_dump(mode="json", exclude_none=True)

    def drive_time(args):
        est = service.drive_times.estimate(args["from_place_id"], args["to_place_id"])
        if est is None:
            return "没有这两地的车程数据，不能估算。"
        factor = service.config.factor_mountain if est.road == "mountain" else service.config.factor_normal
        return {"drive_minutes": est.minutes, "road": est.road, "suggested_minutes": round(est.minutes * factor)}

    def list_decisions(args):
        return decisions_text(service.decisions(trip_id, active_only=not args.get("include_inactive")))

    def list_todos(args):
        t = trip()
        status = args.get("status", "open")
        f = fmt()
        rows = [{"id": x.id, "title": x.title, "kind": x.kind.value, "owners": x.owner_ids or "全员",
                 "due": str(x.due) if x.due else None, "status": x.status, "item_ids": x.item_ids, "notes": x.notes}
                for x in t.todos if status == "all" or x.status == status]
        to_book = [f.item_line(i) for i in t.all_items() if i.booking_status.value == "to_book"]
        return {"todos": rows, "items_to_book": to_book}

    def validate_trip(_args):
        return [i.view(member_id) for i in service.validate(trip_id)] or "没有问题。"

    def list_evidence(_args):
        rows = [e.model_dump(mode="json") for e in service.evidence(trip_id)]
        return untrusted("凭证夹（成员上传、填写的内容）", datetime.now(timezone.utc), rows) if rows else "还没有凭证。"

    # ---------- 写入 ----------

    def record_decision(args):
        existing = {d.id for d in service.decisions(trip_id)}
        new_id = next(f"D-{n:03d}" for n in itertools.count(100) if f"D-{n:03d}" not in existing)
        decision = Decision.model_validate({**args, "id": new_id, "proposed_by": member_id})
        issues = service.record_decision(trip_id, decision)
        log.decisions.append(decision)
        return {"decision_id": new_id,
                "now_conflicting_with_plan": [i.model_dump(mode="json") for i in issues] or "当前行程和这条决策没有冲突。"}

    def record_evidence(args):
        existing = {e.id for e in service.evidence(trip_id)}
        new_id = next(f"ev_{n:03d}" for n in itertools.count(1) if f"ev_{n:03d}" not in existing)
        ev = Evidence(id=new_id, kind=args["kind"], fields=args.get("fields", {}), confirmed=True,
                      uploaded_by=member_id, file=args.get("description"))
        service.add_evidence(trip_id, ev)
        log.evidence.append(ev)
        return {"evidence_id": new_id}

    def propose_patch(args):
        before = trip()
        patch = service.propose(
            trip_id, args["ops"], args["summary"], reason=args.get("reason", ""), author="agent",
            unlock_item_ids=args.get("unlock_item_ids", []), decision_ids=args.get("decision_ids", []),
            evidence_ids=args.get("evidence_ids", []))
        changes = []
        if patch.status.value == "proposed":
            log.patches.append(patch)
            changes = describe(patch.changes, before, apply_ops(before, patch.ops),
                               {pid: p.name_zh for pid, p in places().items()})
        return {
            "patch_id": patch.id, "status": patch.status.value,
            "can_be_confirmed": patch.status.value == "proposed" and not patch.has_errors,
            "issues": [i.view(member_id) for i in patch.issues] or "没有新问题。",
            "changes": changes,
        }

    def withdraw_patch(args):
        p = service.reject(trip_id, args["patch_id"])
        log.patches = [x for x in log.patches if x.id != p.id]
        return {"patch_id": p.id, "status": p.status.value}

    S = lambda props, required=(): {"type": "object", "properties": props, "required": list(required)}  # noqa: E731
    STR = {"type": "string"}
    return [
        Tool("get_trip_summary", "行程概况：每天路线、航班、活动、锁定项、住宿、租车。", S({}), get_trip_summary),
        Tool("get_day", "某一天的完整安排（每个事件的 id、时间、地点、参加人、状态）。", S({"date": {**STR, "description": "YYYY-MM-DD"}}, ["date"]), get_day),
        Tool("get_item", "一个事件的全部字段。", S({"item_id": STR}, ["item_id"]), get_item),
        Tool("search_places", "在景点知识库里找地点。三个条件都可选。", S({"keyword": STR, "city": {**STR, "description": "英文城镇名，如 Glenorchy"}, "tag": {**STR, "description": "项目类型，如 horse_riding"}}), search_places),
        Tool("get_place", "一个地点的详细资料：营业时间、价格、预约、限制、注意事项、核实日期。", S({"place_id": STR}, ["place_id"]), get_place),
        Tool("drive_time", "两地车程。必须用它算车程，不能自己估。", S({"from_place_id": STR, "to_place_id": STR}, ["from_place_id", "to_place_id"]), drive_time),
        Tool("list_decisions", "决策账本：需求、硬约束、已否决的方案。", S({"include_inactive": {"type": "boolean"}}), list_decisions),
        Tool("list_todos", "待办和还没订的项目。", S({"status": {"type": "string", "enum": ["open", "done", "all"]}}), list_todos),
        Tool("validate_trip", "用校验器检查当前行程。", S({}), validate_trip),
        Tool("list_evidence", "已记录的预订凭证。", S({}), list_evidence),
        Tool("record_decision", "把用户的需求或决定记入决策账本。用户表达了新的要求、否决了某个方案、改变了之前的决定时调用。", S({
            "content": STR,
            "kind": {"type": "string", "enum": ["hard_constraint", "preference", "decision", "rejection"]},
            "priority": {"type": "string", "enum": ["must", "high", "normal", "low"]},
            "requirement": {**OBJ, "description": "必玩项目：activity_tag / place_id / city / date，只填用户锁定的那几项",
                            "properties": {"activity_tag": STR, "place_id": STR, "city": STR, "date": STR}},
            "rejected_options": {"type": "array", "items": {**OBJ, "properties": {"description": STR, "place_id": STR, "activity_tag": STR, "city": STR}, "required": ["description"]}},
            "supersedes": {**STR, "description": "被这条决策取代的旧决策 id"},
            "rationale": STR,
        }, ["content", "kind"]), record_decision),
        Tool("record_evidence", "记录用户上传的预订凭证（解析出的字段）。凭证上的信息优先于原计划。", S({
            "kind": {"type": "string", "enum": ["flight", "accommodation", "car", "activity", "other"]},
            "description": STR, "fields": OBJ}, ["kind", "fields"]), record_evidence),
        Tool("propose_patch", "提出行程改动。改动不会立即生效：先过校验器，再由用户确认。返回校验结果；有 error 就要修改后重新提出。", S({
            "summary": {**STR, "description": "一句话说明改了什么"},
            "reason": STR,
            "ops": {"type": "array", "items": {**OBJ, "properties": {
                "op": {"type": "string", "enum": ["add", "update", "remove", "update_day", "update_meta"]},
                "collection": {"type": "string", "enum": ["items", "accommodations", "vehicles", "todos"]},
                "id": STR, "date": STR, "set": OBJ, "value": OBJ}, "required": ["op"]}},
            "unlock_item_ids": {"type": "array", "items": STR, "description": "用户明确同意改动的锁定项"},
            "decision_ids": {"type": "array", "items": STR},
            "evidence_ids": {"type": "array", "items": STR},
        }, ["summary", "ops"]), propose_patch),
        Tool("withdraw_patch", "撤回自己刚提出、但不再需要的改动。", S({"patch_id": STR}, ["patch_id"]), withdraw_patch),
        *build_collab_tools(service, trip_id, member_id, llm_for_guides, guides),
        *build_money_tools(service, trip_id, member_id, gateway),
        *build_external_tools(service, trip_id, gateway),
    ]
