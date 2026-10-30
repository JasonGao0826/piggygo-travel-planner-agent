"""对话修改流程：用户说一句话 → agent 查记忆、调用工具、提出改动 → 用户确认后写入新版本。"""

from dataclasses import dataclass, field
from datetime import date

from ..collab import CollabService
from ..llm.base import LLM, Message, ToolCallRecord, Usage
from ..mcp_gateway import MCPGateway
from ..schema.memory import Decision, Evidence, Patch, Version
from ..service import ServiceError, TripService
from .context import DECISIONS_TEXT_MAX, decisions_text, pending_text, trip_summary
from .prompts import build_system
from .tools import TurnLog, build_tools


# 对话历史最多带多少字给模型
HISTORY_MAX_CHARS = 12_000


@dataclass
class AgentReply:
    text: str
    patches: list[Patch] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


class TripAgent:
    def __init__(self, service: TripService, llm: LLM, *, today: date | None = None,
                 history_messages: int = 20, effort: str | None = None, max_steps: int = 25,
                 gateway: MCPGateway | None = None, guides=None):
        self.service = service
        self.gateway = gateway  # 外部 MCP 服务（机票、汇率、网页搜索）；没有时这些工具不出现
        self.guides = guides    # 网站的攻略服务（带每天的额度）；不传就用默认的
        self.llm = llm
        self.today = today or date.today()
        self.history_messages = history_messages
        self.effort = effort
        self.max_steps = max_steps

    def _system(self, trip_id: str, member_id: str) -> str:
        trip = self.service.current(trip_id)
        member = trip.member(member_id)
        who = f"{member.name}（{member.id}，角色 {member.role}）" if member else member_id
        return build_system(
            today=self.today.isoformat(), member=who,
            summary=trip_summary(trip, self.service.places_for(trip)),
            decisions=decisions_text(self.service.decisions(trip_id, active_only=True), budget=DECISIONS_TEXT_MAX),
            pending=pending_text(self.service.pending_patches(trip_id)),
            group=CollabService(self.service).context_text(trip_id),
        )

    def handle(self, trip_id: str, member_id: str, text: str) -> AgentReply:
        history = self.service.conversation(trip_id, member_id)[-self.history_messages:]
        # 再按总字数截：从最近的往前留，加起来不超过 HISTORY_MAX_CHARS（每一步调用模型都会重发一遍历史）
        kept, total = [], 0
        for m in reversed(history):
            total += len(m.text)
            if total > HISTORY_MAX_CHARS and kept:
                break
            kept.append(m)
        history = kept[::-1]
        log = TurnLog()
        run = self.llm.run_tools(self._system(trip_id, member_id), [*history, Message("user", text)],
                                 build_tools(self.service, trip_id, member_id, log, self.gateway, lambda: self.llm,
                                             guides=self.guides),
                                 effort=self.effort, max_steps=self.max_steps)
        self.service.add_message(trip_id, member_id, Message("user", text))
        self.service.add_message(trip_id, member_id, Message("assistant", run.text))
        return AgentReply(text=run.text, patches=log.patches, decisions=log.decisions, evidence=log.evidence,
                          tool_calls=run.calls, usage=run.usage)

    def confirm(self, trip_id: str, member_id: str, patch_id: str | None = None) -> Version:
        """用户确认改动（这一步由界面触发，不经过模型）。不指定时确认最近一个等待中的改动。"""
        pending = self.service.pending_patches(trip_id)
        if not pending:
            raise ServiceError("没有等待确认的改动")
        patch = next((p for p in pending if p.id == patch_id), None) if patch_id else pending[-1]
        if patch is None:
            raise ServiceError(f"找不到等待确认的改动 {patch_id}")
        version = self.service.confirm(patch.id, confirmed_by=member_id)
        self.service.add_message(trip_id, member_id,
                                 Message("user", f"（我确认了改动：{patch.summary}。已生成第 {version.version} 版。）"))
        return version
