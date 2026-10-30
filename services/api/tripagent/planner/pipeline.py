"""从信息清单首次生成整份行程（设计规格第 5 节 S0–S7）。

S0 检查清单 → S1 出发前待办 → S2 路线框架（模型，用户确认）→ S4 每天去哪（模型）
→ S3 + S5 住宿、租车、时间线（代码）→ 校验 → 有错误就让模型修改，最多 max_repairs 轮。
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from ..llm.base import LLM, Message, Usage
from ..routing import DriveTimeProvider
from ..schema.intake import TripIntake
from ..schema.memory import Decision, Issue, Severity
from ..schema.place import Place
from ..schema.trip import Trip
from ..mcp_gateway import MCPGateway
from ..validator import ValidationContext, ValidatorConfig, validate
from ..checklist import missing_confirm_todos
from .enrich import enrich_bookings
from . import prompts
from .catalog import catalog_text, destination_places, travel_text
from .models import DayPlans, Skeleton
from .rules import member_ids, members_from_intake, must_do_decisions, predeparture_todos
from .timeline import TimelineBuilder

TIMEZONES = {"NZ": "Pacific/Auckland", "AU": "Australia/Melbourne"}
HOME_TZ = {"Melbourne": "Australia/Melbourne", "Sydney": "Australia/Sydney", "Brisbane": "Australia/Brisbane",
           "Adelaide": "Australia/Adelaide", "Perth": "Australia/Perth", "Canberra": "Australia/Sydney"}


class NeedsInput(Exception):
    """信息不够，必须先问用户。"""

    def __init__(self, questions: list[str]):
        super().__init__("；".join(questions))
        self.questions = questions


@dataclass
class PlanResult:
    trip: Trip
    decisions: list[Decision]
    skeleton: Skeleton
    plans: DayPlans
    issues: list[Issue]
    notes: list[str]
    rounds: int
    usage: Usage = field(default_factory=Usage)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]


def intake_problems(intake: TripIntake) -> list[str]:
    """S0：缺了就没法开始的信息。"""
    qs = []
    if not (intake.start_date and intake.end_date):
        qs.append("出发和返回的具体日期是哪天？")
    elif intake.end_date < intake.start_date:
        qs.append("返回日期早于出发日期，请检查。")
    elif (intake.end_date - intake.start_date).days > 30:
        qs.append("行程超过 30 天，第一版暂不支持，请缩短或分成几段。")
    if not intake.members:
        qs.append("一共几个人？请至少填一位成员。")
    if intake.transport == "self_drive" and not any(m.can_drive for m in intake.members):
        qs.append("选择了自驾，但没有成员能开车。谁来开车？")
    if intake.destination not in TIMEZONES:
        qs.append("目前只支持新西兰（NZ）和澳洲（AU）。")
    return qs


class Planner:
    def __init__(self, llm: LLM, places: dict[str, Place], drive: DriveTimeProvider, *,
                 today: date | None = None, config: ValidatorConfig | None = None, effort: str | None = None,
                 gateway: MCPGateway | None = None):
        self.llm, self.places, self.drive = llm, places, drive
        self.gateway = gateway  # 有的话 S6 用 Kiwi.com 查真实航班候选
        self.today = today or date.today()
        self.config = config or ValidatorConfig()
        self.effort = effort

    # ---------- 各阶段 ----------

    def _context(self, intake: TripIntake, decisions: list[Decision]) -> str:
        ids = dict(zip([m.name for m in intake.members], member_ids(intake)))
        lines = [f"今天是 {self.today}。", "用户填写的信息清单：",
                 json.dumps(intake.model_dump(mode="json", exclude_none=True), ensure_ascii=False),
                 f"成员 id：{json.dumps(ids, ensure_ascii=False)}", "决策账本（必须满足）："]
        lines += [f"- [{d.id}] {d.content}：{d.requirement.model_dump(exclude_none=True) if d.requirement else ''}"
                  for d in decisions] or ["（无）"]
        return "\n".join(lines)

    def _skeleton_problems(self, sk: Skeleton, intake: TripIntake) -> list[str]:
        probs = []
        want = [intake.start_date + timedelta(days=i) for i in range((intake.end_date - intake.start_date).days + 1)]
        got = [d.date for d in sk.days]
        if got != [w.isoformat() for w in want]:
            probs.append(f"days 必须是 {want[0]} 到 {want[-1]} 每天一条，现在是 {got}")
        for d in sk.days[:-1]:
            p = self.places.get(d.night_place_id or "")
            if p is None or p.kind not in ("town", "accommodation"):
                probs.append(f"{d.date} 的 night_place_id={d.night_place_id} 不是目录里的城镇")
        for f in sk.flights:
            for a in (f.from_airport, f.to_airport):
                if self.places.get(a) is None or self.places[a].kind != "airport":
                    probs.append(f"航班 {f.number} 的机场 {a} 不在目录里")
        seg_names = {s.name for s in sk.segments}
        for d in sk.days:
            if d.segment not in seg_names:
                probs.append(f"{d.date} 的 segment={d.segment} 不在 segments 里")
        return probs

    def make_skeleton(self, base: str, intake: TripIntake, feedback: str | None = None,
                      previous: Skeleton | None = None) -> Skeleton:
        text = base
        if previous and feedback:
            text += (f"\n\n上一版路线框架：\n{previous.model_dump_json()}\n\n用户的修改意见：{feedback}\n"
                     "请按意见修改后完整输出。")
        sk = self.llm.structured(prompts.SKELETON, [Message("user", text)], Skeleton, effort=self.effort)
        probs = self._skeleton_problems(sk, intake)
        if probs:  # 格式问题让模型自己改一次
            sk = self.llm.structured(prompts.SKELETON, [Message("user", text + "\n\n上一版的问题：\n- "
                                                                + "\n- ".join(probs))], Skeleton, effort=self.effort)
            probs = self._skeleton_problems(sk, intake)
            if probs:
                raise ValueError("路线框架不完整：" + "；".join(probs))
        return sk

    def build(self, intake, members, skeleton, plans, trip_id, todos, local_tz, home_tz) -> tuple[Trip, list[str]]:
        builder = TimelineBuilder(intake, members, skeleton, plans, self.places, self.drive, trip_id=trip_id,
                                  today=self.today, config=self.config, home_timezone=home_tz, local_timezone=local_tz)
        return builder.build([t.model_copy() for t in todos]), builder.notes

    def check(self, trip: Trip, decisions: list[Decision]) -> list[Issue]:
        return validate(ValidationContext(trip, self.places, self.drive, decisions, self.config))

    @staticmethod
    def _score(issues: list[Issue]) -> tuple[int, int]:
        errs = sum(i.severity == Severity.ERROR for i in issues)
        return errs, len(issues) - errs

    @staticmethod
    def _issues_text(issues: list[Issue], notes: list[str]) -> str:
        lines = [f"- [{i.code}/{i.severity.value}] {i.message}" for i in issues]
        lines += [f"- [排程] {n}" for n in notes]
        return "\n".join(lines)

    # ---------- 主流程 ----------

    def generate(self, intake: TripIntake, *, trip_id: str | None = None,
                 on_progress: Callable[[str, str], None] = lambda stage, msg: None,
                 review_skeleton: Callable[[Skeleton], str | None] | None = None,
                 max_repairs: int = 3) -> PlanResult:
        usage_before = Usage(**vars(getattr(self.llm, "total", Usage())))

        on_progress("S0", "检查信息清单")
        if qs := intake_problems(intake):
            raise NeedsInput(qs)
        members = members_from_intake(intake)
        decisions = must_do_decisions(intake, self.places)
        local_tz = TIMEZONES[intake.destination]
        home_tz = HOME_TZ.get(intake.origin_city, "Australia/Melbourne")
        trip_id = trip_id or f"trip_{uuid.uuid4().hex[:8]}"

        on_progress("S1", "整理出发前要办的事（签证、驾照、入境申报）")
        todos = predeparture_todos(intake, self.today)

        on_progress("S2", "计算车程，规划路线框架")
        dest = destination_places(self.places, intake.destination)
        dest.update({pid: p for pid, p in self.places.items() if p.kind == "airport"})
        base = (f"{self._context(intake, decisions)}\n\n地点目录（id｜名称｜类型｜城镇｜项目类型｜营业｜时长｜预约｜限制）：\n"
                f"{catalog_text(dest)}\n\n车程表（从城镇或机场出发，分钟）：\n{travel_text(dest, self.drive)}")
        skeleton = self.make_skeleton(base, intake)
        for _ in range(3):
            feedback = review_skeleton(skeleton) if review_skeleton else None
            if not feedback:
                break
            on_progress("S2", "按你的意见修改路线框架")
            skeleton = self.make_skeleton(base, intake, feedback, skeleton)

        on_progress("S4", "安排每天去哪些地方")
        day_input = f"{base}\n\n已确定的路线框架：\n{skeleton.model_dump_json()}"
        plans = self.llm.structured(prompts.DAYPLAN, [Message("user", day_input)], DayPlans, effort=self.effort)

        on_progress("S5", "排逐小时时间表，检查冲突")
        trip, notes = self.build(intake, members, skeleton, plans, trip_id, todos, local_tz, home_tz)
        issues = self.check(trip, decisions)
        best = (self._score(issues), trip, plans, issues, notes)
        rounds = 0
        while best[0][0] > 0 and rounds < max_repairs:
            rounds += 1
            on_progress("S5", f"有 {best[0][0]} 个冲突，第 {rounds} 次调整")
            repair_input = (f"{day_input}\n\n上一版每天的安排：\n{best[2].model_dump_json()}\n\n"
                            f"校验发现的问题：\n{self._issues_text(best[3], best[4])}")
            plans = self.llm.structured(prompts.REPAIR, [Message("user", repair_input)], DayPlans, effort=self.effort)
            trip, notes = self.build(intake, members, skeleton, plans, trip_id, todos, local_tz, home_tz)
            issues = self.check(trip, decisions)
            if self._score(issues) < best[0]:
                best = (self._score(issues), trip, plans, issues, notes)

        on_progress("S6", "整理待办和预订清单，查航班候选和住宿链接")
        _, trip, plans, issues, notes = best
        notes = notes + enrich_bookings(trip, self.places, self.gateway,
                                        whole_home=intake.accommodation_type == "whole_home")
        trip.todos += [n.todo for n in missing_confirm_todos(trip)]   # 已订的住宿、租车还缺的细节，变成确认待办
        usage = Usage()
        if hasattr(self.llm, "total"):
            usage = Usage(self.llm.total.input_tokens - usage_before.input_tokens,
                          self.llm.total.output_tokens - usage_before.output_tokens,
                          self.llm.total.calls - usage_before.calls)
        return PlanResult(trip=trip, decisions=decisions, skeleton=skeleton, plans=plans, issues=issues,
                          notes=notes, rounds=rounds, usage=usage)
