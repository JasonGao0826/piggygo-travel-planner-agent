"""行程服务：记忆层的写入入口（设计规格 8.4 节）。

所有对行程的修改都走这里：提出改动 → 校验 → 用户确认 → 写入新版本。
决策账本、凭证、对话记录也在这里维护。数据通过 Store 接口存取（见 store.py）。
"""

from datetime import date, datetime, timezone

from pydantic import ValidationError

from .live.models import LiveSnapshot
from .live.service import LiveService
from .llm.base import Message
from .planner.rules import profile_todo_ops
from .schema.collab import ActivityEvent
from .routing import DriveTimeProvider
from .schema.memory import Decision, DecisionStatus, Evidence, Issue, Patch, PatchOp, PatchStatus, Severity, Version
from .schema.place import Place
from .schema.trip import Member, MemberProfile, Trip
from .state import PatchError, apply_ops, changed_ids, describe, diff, touched_ids
from .store import InMemoryStore, Store, VersionExists
from .validator import ValidationContext, ValidatorConfig, check_locked_changes, validate


class ServiceError(Exception):
    pass


class ConflictError(ServiceError):
    """改动提出之后，同一批记录已经被别人改过了。"""


EVIDENCE_LABEL = {"flight": "机票", "accommodation": "住宿", "car": "租车", "activity": "活动", "other": ""}
# 动态里说改了哪几样资料（体重也只说改了）
PROFILE_LABELS = {"passport_country": "护照", "visa": "签证", "can_drive": "开车", "age": "年龄",
                  "licence_country": "驾照", "licence_type": "驾照", "weight_kg": "体重"}


def _issue_key(issue: Issue) -> tuple:
    return issue.code, issue.message


class TripService:
    def __init__(self, store: Store, places: dict[str, Place], drive_times: DriveTimeProvider,
                 config: ValidatorConfig | None = None, live: LiveService | None = None):
        self.store = store
        self.places = places
        self.drive_times = drive_times
        self.config = config or ValidatorConfig()
        self.live = live  # 实时数据（路况、天气）；没有时只做离线校验

    # ---------- 读取 ----------

    def trip_ids(self) -> list[str]:
        return self.store.trip_ids()

    def current(self, trip_id: str) -> Trip:
        return self._latest(trip_id).state

    def latest(self, trip_id: str) -> Version:
        return self._latest(trip_id)

    def version(self, trip_id: str, number: int) -> Version:
        v = self.store.get_version(trip_id, number)
        if v is None:
            raise ServiceError(f"{trip_id} 没有第 {number} 版")
        return v

    def history(self, trip_id: str) -> list[Version]:
        self._latest(trip_id)
        return self.store.versions(trip_id)

    def decisions(self, trip_id: str, active_only: bool = False) -> list[Decision]:
        rows = self.store.decisions(trip_id)
        return [d for d in rows if d.status == DecisionStatus.ACTIVE] if active_only else rows

    def pending_patches(self, trip_id: str) -> list[Patch]:
        return [p for p in self.store.patches(trip_id) if p.status == PatchStatus.PROPOSED]

    def patch(self, patch_id: str) -> Patch:
        return self._patch(patch_id)

    def evidence(self, trip_id: str) -> list[Evidence]:
        return self.store.evidence(trip_id)

    def conversation(self, trip_id: str, member_id: str) -> list[Message]:
        return self.store.messages(trip_id, member_id)

    def activity(self, trip_id: str, limit: int = 50) -> list[ActivityEvent]:
        return self.store.activity(trip_id, limit)

    def name_of(self, trip_id: str, member_id: str | None) -> str:
        """成员 id → 称呼，用于动态和消息。"""
        if member_id in (None, "agent"):
            return "猪猪"
        if member_id == "system":
            return "系统"
        member = self.current(trip_id).member(member_id)
        return member.name if member else member_id

    def places_for(self, trip: Trip) -> dict[str, Place]:
        """知识库 + 这个行程自己的地点（同 id 时以行程自己的为准）。"""
        if hasattr(self.drive_times, "register"):
            self.drive_times.register(trip.custom_places)
        return {**self.places, **{p.id: p for p in trip.custom_places}}

    def validate(self, trip_id: str, trip: Trip | None = None) -> list[Issue]:
        return validate(self._ctx(trip_id, trip or self.current(trip_id)))

    def live_snapshot(self, trip: Trip) -> LiveSnapshot | None:
        return self.live.snapshot(trip, self.places_for(trip)) if self.live else None

    # ---------- 写入 ----------

    def create_trip(self, trip: Trip, decisions: list[Decision] = ()) -> Version:
        trip_id = trip.meta.id
        if self.store.has_trip(trip_id):
            raise ServiceError(f"行程 {trip_id} 已经存在")
        for d in decisions:
            self.store.save_decision(trip_id, d)
        first = Version(trip_id=trip_id, version=1, state=trip.normalized().model_copy(update={"version": 1}),
                        changelog=["创建行程"])
        self.store.add_version(first)
        self.log(trip_id, "system", "created", f"创建了行程（第 1 版，{len(trip.all_items())} 个安排）")
        return first

    def log(self, trip_id: str, actor: str | None, kind: str, text: str, refs: list[str] = ()) -> ActivityEvent:
        """写一条动态（设计规格 13.6 节）。"""
        return self.store.add_activity(trip_id, ActivityEvent(at=datetime.now(timezone.utc), actor=actor, kind=kind,
                                                              text=text, refs=list(refs)))

    def add_message(self, trip_id: str, member_id: str, message: Message) -> None:
        self.store.add_message(trip_id, member_id, message)

    def record_decision(self, trip_id: str, decision: Decision) -> list[Issue]:
        """记入决策账本。取代旧决策时，旧的标成 superseded。返回当前行程因这条决策新出现的问题。"""
        rows = self.store.decisions(trip_id)
        if any(d.id == decision.id for d in rows):
            raise ServiceError(f"决策 {decision.id} 已经存在")
        before = {_issue_key(i) for i in self.validate(trip_id)}
        if decision.supersedes:
            old = next((d for d in rows if d.id == decision.supersedes), None)
            if old is None:
                raise ServiceError(f"要取代的决策 {decision.supersedes} 不存在")
            old.status = DecisionStatus.SUPERSEDED
            self.store.save_decision(trip_id, old)
        self.store.save_decision(trip_id, decision)
        who = self.name_of(trip_id, decision.proposed_by)
        self.log(trip_id, decision.proposed_by, "decision", f"{who} 的决定记入了决策账本：{decision.content}", [decision.id])
        return [i for i in self.validate(trip_id) if _issue_key(i) not in before]

    def add_evidence(self, trip_id: str, evidence: Evidence) -> Evidence:
        if any(e.id == evidence.id for e in self.store.evidence(trip_id)):
            raise ServiceError(f"凭证 {evidence.id} 已经存在")
        self.store.add_evidence(trip_id, evidence)
        label = evidence.title or evidence.file or evidence.filename
        self.log(trip_id, evidence.uploaded_by, "evidence",
                 f"{self.name_of(trip_id, evidence.uploaded_by)} 上传了{EVIDENCE_LABEL.get(evidence.kind, '')}凭证"
                 + (f"：{label}" if label else ""), [evidence.id])
        return evidence

    def propose(self, trip_id: str, ops: list[PatchOp], summary: str, *, reason: str = "", author: str = "agent",
                base_version: int | None = None, unlock_item_ids: list[str] = (),
                decision_ids: list[str] = (), evidence_ids: list[str] = ()) -> Patch:
        """提出改动：在副本上应用并校验，不写入。返回带差异和问题列表的提案。"""
        base = self.version(trip_id, base_version) if base_version else self._latest(trip_id)
        patch = Patch(
            id=self.store.next_patch_id(), trip_id=trip_id, base_version=base.version, ops=list(ops),
            summary=summary, reason=reason, author=author, unlock_item_ids=list(unlock_item_ids),
            decision_ids=list(decision_ids), evidence_ids=list(evidence_ids),
        )
        after, issues = self._evaluate(trip_id, base.state, patch)
        patch.issues = issues
        if after is None:
            patch.status = PatchStatus.REJECTED
        else:
            patch.changes = diff(base.state, after)
        self.store.save_patch(patch)
        return patch

    def confirm(self, patch_id: str, confirmed_by: str) -> Version:
        """用户确认后写入新版本。期间如果行程被别人改过，就在最新版本上重新应用和校验。"""
        patch = self._patch(patch_id)
        if patch.status != PatchStatus.PROPOSED:
            raise ServiceError(f"改动 {patch_id} 的状态是 {patch.status}，不能确认")
        if patch.has_errors:
            raise ServiceError(f"改动 {patch_id} 还有错误，不能写入")

        latest = self._latest(patch.trip_id)
        if latest.version != patch.base_version:
            touched = touched_ids(patch.ops)
            for v in self.store.versions(patch.trip_id)[patch.base_version:]:
                other = self.store.get_patch(v.patch_id) if v.patch_id else None
                if other and touched & changed_ids(other.changes):
                    patch.status = PatchStatus.CONFLICT
                    self.store.save_patch(patch)
                    raise ConflictError(f"改动 {patch_id} 涉及的记录在第 {v.version} 版已被修改，需要重新提出")
            after, issues = self._evaluate(patch.trip_id, latest.state, patch)
            if after is None or any(i.severity == Severity.ERROR for i in issues):
                patch.status, patch.issues = PatchStatus.CONFLICT, issues
                self.store.save_patch(patch)
                raise ConflictError(f"改动 {patch_id} 放到最新版本上不再成立，需要重新提出")
            patch.issues, patch.base_version = issues, latest.version
        else:
            after = apply_ops(latest.state, patch.ops)

        patch.changes = diff(latest.state, after)
        try:
            new = self._commit(patch, latest, after, confirmed_by)
        except VersionExists as exc:  # 两个人同时确认：后到的那个重新提出
            raise ConflictError(f"改动 {patch_id} 确认时行程刚被别人更新，请重新提出") from exc
        self.log(patch.trip_id, confirmed_by, "version",
                 f"{self.name_of(patch.trip_id, confirmed_by)} 确认了改动「{patch.summary}」，生成第 {new.version} 版",
                 [patch.id, f"V{new.version}"])
        return new

    def update_member_profile(self, trip_id: str, member_id: str, profile: MemberProfile, *,
                              today: date | None = None) -> tuple[Version | None, list[Issue]]:
        """成员本人补充或修改资料（护照、签证、开车、驾照、年龄、体重）。资料是事实，不是方案：
        本人改了就直接生成新版本，不用再确认；跟着资料走的待办（驾照翻译件、NZeTA）一起更新。
        资料改了带来的新问题（例如租车登记的司机改成了不开车）照常在行程检查里提示，再请猪猪调整行程。
        返回新版本和这次新出现的问题；什么都没变时返回 (None, [])。"""
        fields = profile.model_dump(exclude_unset=True)
        for _ in range(3):   # 保存时正好有人确认了改动：在最新版本上重做一次
            latest = self._latest(trip_id)
            before = latest.state
            old = before.member(member_id)
            if old is None:
                raise ServiceError(f"行程里没有成员 {member_id}")
            member = Member.model_validate({**old.model_dump(), **fields})
            if member == old:
                return None, []
            moved = before.model_copy(update={"members": [member if m.id == member_id else m for m in before.members]})
            patch = Patch(id=self.store.next_patch_id(), trip_id=trip_id, base_version=latest.version,
                          ops=profile_todo_ops(moved, member, today or date.today()),
                          summary=f"{member.name} 更新了自己的资料", reason="成员本人填写", author=member_id)
            try:
                after = apply_ops(moved, patch.ops)
            except (PatchError, ValidationError) as exc:
                raise ServiceError(f"资料没有保存：{exc}") from exc
            patch.changes = diff(before, after)
            existing = {_issue_key(i) for i in self.validate(trip_id, before)}
            patch.issues = [i for i in self.validate(trip_id, after) if _issue_key(i) not in existing]
            try:
                new = self._commit(patch, latest, after, member_id)
            except VersionExists:
                continue
            fields_text = "、".join(dict.fromkeys(PROFILE_LABELS.get(k, k) for k in fields if getattr(old, k) != getattr(member, k)))
            self.log(trip_id, member_id, "profile", f"{member.name} 更新了自己的资料（{fields_text}），生成第 {new.version} 版",
                     [patch.id, f"V{new.version}"])
            return new, patch.issues
        raise ConflictError("保存时行程刚被别人更新，请再保存一次")

    def reject(self, trip_id: str, patch_id: str) -> Patch:
        patch = self._patch(patch_id)
        if patch.trip_id != trip_id:   # 改动编号是全局的：不能动别的行程的改动
            raise ServiceError(f"找不到改动 {patch_id}")
        if patch.status == PatchStatus.PROPOSED:
            patch.status = PatchStatus.REJECTED
            self.store.save_patch(patch)
        return patch

    # ---------- 内部 ----------

    def _commit(self, patch: Patch, latest: Version, after: Trip, by: str) -> Version:
        """写入新版本（patch.changes 已经算好）。两个人同时写入时，后到的抛出 VersionExists。"""
        number = latest.version + 1
        names = {pid: p.name_zh for pid, p in self.places_for(after).items()}
        new = Version(trip_id=patch.trip_id, version=number, state=after.model_copy(update={"version": number}),
                      patch_id=patch.id, changelog=[patch.summary, *describe(patch.changes, latest.state, after, names)])
        self.store.add_version(new)
        patch.status, patch.confirmed_by = PatchStatus.APPLIED, by
        self.store.save_patch(patch)
        return new

    def _evaluate(self, trip_id: str, before: Trip, patch: Patch) -> tuple[Trip | None, list[Issue]]:
        """应用改动并校验。只报告这次改动新带来的问题，之前就有的问题不算在它头上。"""
        try:
            after = apply_ops(before, patch.ops)
        except (PatchError, ValidationError) as exc:
            return None, [Issue(code="P00", severity=Severity.ERROR, message=f"改动格式有误：{exc}")]
        existing = {_issue_key(i) for i in validate(self._ctx(trip_id, before))}
        new_issues = [i for i in validate(self._ctx(trip_id, after)) if _issue_key(i) not in existing]
        locked = check_locked_changes(before, after, set(patch.unlock_item_ids))
        return after, locked + new_issues

    def _ctx(self, trip_id: str, trip: Trip) -> ValidationContext:
        self.places_for(trip)  # 登记行程自己地点的坐标，供车程计算使用
        return ValidationContext(trip, self.places, self.drive_times,
                                 self.decisions(trip_id, active_only=True), self.config, self.live_snapshot(trip),
                                 today=date.today())

    def _latest(self, trip_id: str) -> Version:
        v = self.store.latest_version(trip_id)
        if v is None:
            raise ServiceError(f"找不到行程 {trip_id}")
        return v

    def _patch(self, patch_id: str) -> Patch:
        p = self.store.get_patch(patch_id)
        if p is None:
            raise ServiceError(f"找不到改动 {patch_id}")
        return p


__all__ = ["ConflictError", "InMemoryStore", "ServiceError", "TripService"]
