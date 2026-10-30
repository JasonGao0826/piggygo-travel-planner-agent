"""记忆层的存储（设计规格 7.4 节）。

TripService 只通过 Store 接口读写。InMemoryStore 用于测试；SqlStore 用 SQLAlchemy，
本地开发用 SQLite 文件，正式环境把 DATABASE_URL 换成 PostgreSQL 即可。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import (JSON, Column, DateTime, Integer, MetaData, String, Table, Text, UniqueConstraint,
                        create_engine, delete, func, insert, select, update)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from .llm.base import Message
from .paths import DATA_DIR
from .schema.collab import ActivityEvent, MemberPreferences, Poll, PreferenceSummary
from .schema.memory import Decision, Evidence, Patch, Version
from .schema.money import Expense, MoneySettings, Transfer
from .guides import Guide


class VersionExists(Exception):
    """同一个版本号已经写过了（两个人几乎同时确认改动）。"""


class Store(Protocol):
    def trip_ids(self) -> list[str]: ...
    def has_trip(self, trip_id: str) -> bool: ...
    def versions(self, trip_id: str) -> list[Version]: ...
    def latest_version(self, trip_id: str) -> Version | None: ...
    def get_version(self, trip_id: str, number: int) -> Version | None: ...
    def add_version(self, version: Version) -> None: ...
    def next_patch_id(self) -> str: ...
    def get_patch(self, patch_id: str) -> Patch | None: ...
    def save_patch(self, patch: Patch) -> None: ...
    def patches(self, trip_id: str) -> list[Patch]: ...
    def decisions(self, trip_id: str) -> list[Decision]: ...
    def save_decision(self, trip_id: str, decision: Decision) -> None: ...
    def evidence(self, trip_id: str) -> list[Evidence]: ...
    def add_evidence(self, trip_id: str, evidence: Evidence) -> None: ...
    def save_evidence(self, trip_id: str, evidence: Evidence) -> None: ...
    def delete_evidence(self, trip_id: str, evidence_id: str) -> None: ...
    def messages(self, trip_id: str, member_id: str) -> list[Message]: ...
    def add_message(self, trip_id: str, member_id: str, message: Message) -> None: ...
    # 多人协作（设计规格第 13 节）
    def preferences(self, trip_id: str) -> list[MemberPreferences]: ...
    def save_preferences(self, trip_id: str, prefs: MemberPreferences) -> None: ...
    def latest_summary(self, trip_id: str) -> PreferenceSummary | None: ...
    def save_summary(self, trip_id: str, summary: PreferenceSummary) -> None: ...
    def polls(self, trip_id: str) -> list[Poll]: ...
    def save_poll(self, trip_id: str, poll: Poll) -> None: ...
    def activity(self, trip_id: str, limit: int = 50) -> list[ActivityEvent]: ...
    def add_activity(self, trip_id: str, event: ActivityEvent) -> ActivityEvent: ...
    # 账目与 AA 结算（设计规格第 14 节）
    def expenses(self, trip_id: str) -> list[Expense]: ...
    def save_expense(self, trip_id: str, expense: Expense) -> None: ...
    def delete_expense(self, trip_id: str, expense_id: str) -> None: ...
    def transfers(self, trip_id: str) -> list[Transfer]: ...
    def save_transfer(self, trip_id: str, transfer: Transfer) -> None: ...
    def delete_transfer(self, trip_id: str, transfer_id: str) -> None: ...
    def money_settings(self, trip_id: str) -> MoneySettings: ...
    def save_money_settings(self, trip_id: str, settings: MoneySettings) -> None: ...
    # 攻略链接（设计规格 15.4 节）
    def guides(self, trip_id: str) -> list[Guide]: ...
    def save_guide(self, trip_id: str, guide: Guide) -> None: ...
    def delete_guide(self, trip_id: str, guide_id: str) -> None: ...


# ---------------- 内存 ----------------

@dataclass
class InMemoryStore:
    _versions: dict[str, list[Version]] = field(default_factory=dict)
    _patches: dict[str, Patch] = field(default_factory=dict)
    _decisions: dict[str, list[Decision]] = field(default_factory=dict)
    _evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    _messages: dict[tuple[str, str], list[Message]] = field(default_factory=dict)
    _preferences: dict[tuple[str, str], MemberPreferences] = field(default_factory=dict)
    _summaries: dict[str, PreferenceSummary] = field(default_factory=dict)
    _polls: dict[str, dict[str, Poll]] = field(default_factory=dict)
    _activity: dict[str, list[ActivityEvent]] = field(default_factory=dict)
    _expenses: dict[str, dict[str, Expense]] = field(default_factory=dict)
    _transfers: dict[str, dict[str, Transfer]] = field(default_factory=dict)
    _money_settings: dict[str, MoneySettings] = field(default_factory=dict)
    _guides: dict[str, dict[str, Guide]] = field(default_factory=dict)

    def trip_ids(self) -> list[str]:
        return list(self._versions)

    def has_trip(self, trip_id: str) -> bool:
        return trip_id in self._versions

    def versions(self, trip_id: str) -> list[Version]:
        return list(self._versions.get(trip_id, []))

    def latest_version(self, trip_id: str) -> Version | None:
        rows = self._versions.get(trip_id)
        return rows[-1] if rows else None

    def get_version(self, trip_id: str, number: int) -> Version | None:
        return next((v for v in self._versions.get(trip_id, []) if v.version == number), None)

    def add_version(self, version: Version) -> None:
        rows = self._versions.setdefault(version.trip_id, [])
        if any(v.version == version.version for v in rows):
            raise VersionExists(f"{version.trip_id} 第 {version.version} 版已存在")
        rows.append(version)

    def next_patch_id(self) -> str:
        return f"p{len(self._patches) + 1:04d}"

    def get_patch(self, patch_id: str) -> Patch | None:
        return self._patches.get(patch_id)

    def save_patch(self, patch: Patch) -> None:
        self._patches[patch.id] = patch

    def patches(self, trip_id: str) -> list[Patch]:
        return [p for p in self._patches.values() if p.trip_id == trip_id]

    def decisions(self, trip_id: str) -> list[Decision]:
        return list(self._decisions.get(trip_id, []))

    def save_decision(self, trip_id: str, decision: Decision) -> None:
        rows = self._decisions.setdefault(trip_id, [])
        for i, d in enumerate(rows):
            if d.id == decision.id:
                rows[i] = decision
                return
        rows.append(decision)

    def evidence(self, trip_id: str) -> list[Evidence]:
        return list(self._evidence.get(trip_id, []))

    def add_evidence(self, trip_id: str, evidence: Evidence) -> None:
        self._evidence.setdefault(trip_id, []).append(evidence)

    def save_evidence(self, trip_id: str, evidence: Evidence) -> None:
        rows = self._evidence.setdefault(trip_id, [])
        for i, e in enumerate(rows):
            if e.id == evidence.id:
                rows[i] = evidence
                return
        rows.append(evidence)

    def delete_evidence(self, trip_id: str, evidence_id: str) -> None:
        self._evidence[trip_id] = [e for e in self._evidence.get(trip_id, []) if e.id != evidence_id]

    def messages(self, trip_id: str, member_id: str) -> list[Message]:
        return list(self._messages.get((trip_id, member_id), []))

    def add_message(self, trip_id: str, member_id: str, message: Message) -> None:
        self._messages.setdefault((trip_id, member_id), []).append(message)

    def preferences(self, trip_id: str) -> list[MemberPreferences]:
        return [p for (t, _), p in self._preferences.items() if t == trip_id]

    def save_preferences(self, trip_id: str, prefs: MemberPreferences) -> None:
        self._preferences[(trip_id, prefs.member_id)] = prefs

    def latest_summary(self, trip_id: str) -> PreferenceSummary | None:
        return self._summaries.get(trip_id)

    def save_summary(self, trip_id: str, summary: PreferenceSummary) -> None:
        self._summaries[trip_id] = summary

    def polls(self, trip_id: str) -> list[Poll]:
        return list(self._polls.get(trip_id, {}).values())

    def save_poll(self, trip_id: str, poll: Poll) -> None:
        self._polls.setdefault(trip_id, {})[poll.id] = poll

    def activity(self, trip_id: str, limit: int = 50) -> list[ActivityEvent]:
        return list(reversed(self._activity.get(trip_id, [])))[:limit]

    def add_activity(self, trip_id: str, event: ActivityEvent) -> ActivityEvent:
        rows = self._activity.setdefault(trip_id, [])
        event = event.model_copy(update={"id": len(rows) + 1})
        rows.append(event)
        return event

    def expenses(self, trip_id: str) -> list[Expense]:
        return list(self._expenses.get(trip_id, {}).values())

    def save_expense(self, trip_id: str, expense: Expense) -> None:
        self._expenses.setdefault(trip_id, {})[expense.id] = expense

    def delete_expense(self, trip_id: str, expense_id: str) -> None:
        self._expenses.get(trip_id, {}).pop(expense_id, None)

    def transfers(self, trip_id: str) -> list[Transfer]:
        return list(self._transfers.get(trip_id, {}).values())

    def save_transfer(self, trip_id: str, transfer: Transfer) -> None:
        self._transfers.setdefault(trip_id, {})[transfer.id] = transfer

    def delete_transfer(self, trip_id: str, transfer_id: str) -> None:
        self._transfers.get(trip_id, {}).pop(transfer_id, None)

    def money_settings(self, trip_id: str) -> MoneySettings:
        return self._money_settings.get(trip_id, MoneySettings())

    def save_money_settings(self, trip_id: str, settings: MoneySettings) -> None:
        self._money_settings[trip_id] = settings

    def guides(self, trip_id: str) -> list[Guide]:
        return list(self._guides.get(trip_id, {}).values())

    def save_guide(self, trip_id: str, guide: Guide) -> None:
        self._guides.setdefault(trip_id, {})[guide.id] = guide

    def delete_guide(self, trip_id: str, guide_id: str) -> None:
        self._guides.get(trip_id, {}).pop(guide_id, None)


# ---------------- 数据库 ----------------

JSONType = JSON().with_variant(JSONB(), "postgresql")
metadata = MetaData()

versions_t = Table(
    "versions", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("version", Integer, nullable=False),
    Column("data", JSONType, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("trip_id", "version"),
)
patches_t = Table(
    "patches", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("id", String(32), nullable=False, unique=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("status", String(16), nullable=False),
    Column("data", JSONType, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
decisions_t = Table(
    "decisions", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)
evidence_t = Table(
    "evidence", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)
messages_t = Table(
    "messages", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False),
    Column("member_id", String(64), nullable=False),
    Column("role", String(16), nullable=False),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

preferences_t = Table(
    "preferences", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("member_id", String(64), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "member_id"),
)
summaries_t = Table(
    "summaries", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("data", JSONType, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)
polls_t = Table(
    "polls", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)
activity_t = Table(
    "activity", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("data", JSONType, nullable=False),
)

expenses_t = Table(
    "expenses", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)
transfers_t = Table(
    "transfers", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)
money_settings_t = Table(
    "money_settings", metadata,
    Column("trip_id", String(64), primary_key=True),
    Column("data", JSONType, nullable=False),
)

guides_t = Table(
    "guides", metadata,
    Column("pk", Integer, primary_key=True, autoincrement=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("id", String(32), nullable=False),
    Column("data", JSONType, nullable=False),
    UniqueConstraint("trip_id", "id"),
)

DEFAULT_SQLITE = DATA_DIR / "tripagent.db"   # 本地 services/api/data/，上线时在持久硬盘上


class SqlStore:
    def __init__(self, url: str | None = None):
        url = url or os.getenv("DATABASE_URL") or f"sqlite:///{DEFAULT_SQLITE.as_posix()}"
        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, future=True)
        metadata.create_all(self.engine)

    # ---------- 版本 ----------

    def trip_ids(self) -> list[str]:
        with self.engine.connect() as c:
            return list(c.scalars(select(versions_t.c.trip_id).distinct()))

    def has_trip(self, trip_id: str) -> bool:
        return self.latest_version(trip_id) is not None

    def versions(self, trip_id: str) -> list[Version]:
        with self.engine.connect() as c:
            rows = c.scalars(select(versions_t.c.data).where(versions_t.c.trip_id == trip_id).order_by(versions_t.c.version))
            return [Version.model_validate(r) for r in rows]

    def latest_version(self, trip_id: str) -> Version | None:
        with self.engine.connect() as c:
            row = c.scalar(select(versions_t.c.data).where(versions_t.c.trip_id == trip_id)
                           .order_by(versions_t.c.version.desc()).limit(1))
            return Version.model_validate(row) if row else None

    def get_version(self, trip_id: str, number: int) -> Version | None:
        with self.engine.connect() as c:
            row = c.scalar(select(versions_t.c.data).where(versions_t.c.trip_id == trip_id, versions_t.c.version == number))
            return Version.model_validate(row) if row else None

    def add_version(self, version: Version) -> None:
        try:
            with self.engine.begin() as c:
                c.execute(insert(versions_t).values(trip_id=version.trip_id, version=version.version,
                                                    data=version.model_dump(mode="json")))
        except IntegrityError as exc:
            raise VersionExists(f"{version.trip_id} 第 {version.version} 版已存在") from exc

    # ---------- 改动提案 ----------

    def next_patch_id(self) -> str:
        with self.engine.connect() as c:
            last = c.scalar(select(func.max(patches_t.c.pk))) or 0
        return f"p{last + 1:04d}"

    def get_patch(self, patch_id: str) -> Patch | None:
        with self.engine.connect() as c:
            row = c.scalar(select(patches_t.c.data).where(patches_t.c.id == patch_id))
            return Patch.model_validate(row) if row else None

    def save_patch(self, patch: Patch) -> None:
        data = patch.model_dump(mode="json")
        with self.engine.begin() as c:
            exists = c.scalar(select(patches_t.c.pk).where(patches_t.c.id == patch.id))
            if exists:
                c.execute(update(patches_t).where(patches_t.c.id == patch.id).values(status=patch.status.value, data=data))
            else:
                c.execute(insert(patches_t).values(id=patch.id, trip_id=patch.trip_id, status=patch.status.value, data=data))

    def patches(self, trip_id: str) -> list[Patch]:
        with self.engine.connect() as c:
            rows = c.scalars(select(patches_t.c.data).where(patches_t.c.trip_id == trip_id).order_by(patches_t.c.pk))
            return [Patch.model_validate(r) for r in rows]

    # ---------- 决策、凭证、对话 ----------

    def decisions(self, trip_id: str) -> list[Decision]:
        with self.engine.connect() as c:
            rows = c.scalars(select(decisions_t.c.data).where(decisions_t.c.trip_id == trip_id).order_by(decisions_t.c.pk))
            return [Decision.model_validate(r) for r in rows]

    def save_decision(self, trip_id: str, decision: Decision) -> None:
        data = decision.model_dump(mode="json")
        with self.engine.begin() as c:
            cond = (decisions_t.c.trip_id == trip_id) & (decisions_t.c.id == decision.id)
            if c.scalar(select(decisions_t.c.pk).where(cond)):
                c.execute(update(decisions_t).where(cond).values(status=decision.status.value, data=data))
            else:
                c.execute(insert(decisions_t).values(trip_id=trip_id, id=decision.id, status=decision.status.value, data=data))

    def evidence(self, trip_id: str) -> list[Evidence]:
        with self.engine.connect() as c:
            rows = c.scalars(select(evidence_t.c.data).where(evidence_t.c.trip_id == trip_id).order_by(evidence_t.c.pk))
            return [Evidence.model_validate(r) for r in rows]

    def add_evidence(self, trip_id: str, evidence: Evidence) -> None:
        with self.engine.begin() as c:
            c.execute(insert(evidence_t).values(trip_id=trip_id, id=evidence.id, data=evidence.model_dump(mode="json")))

    def save_evidence(self, trip_id: str, evidence: Evidence) -> None:
        cond = (evidence_t.c.trip_id == trip_id) & (evidence_t.c.id == evidence.id)
        data = evidence.model_dump(mode="json")
        with self.engine.begin() as c:
            if c.scalar(select(evidence_t.c.pk).where(cond)):
                c.execute(update(evidence_t).where(cond).values(data=data))
            else:
                c.execute(insert(evidence_t).values(trip_id=trip_id, id=evidence.id, data=data))

    def delete_evidence(self, trip_id: str, evidence_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(evidence_t).where((evidence_t.c.trip_id == trip_id) & (evidence_t.c.id == evidence_id)))

    def messages(self, trip_id: str, member_id: str) -> list[Message]:
        with self.engine.connect() as c:
            rows = c.execute(select(messages_t.c.role, messages_t.c.text)
                             .where(messages_t.c.trip_id == trip_id, messages_t.c.member_id == member_id)
                             .order_by(messages_t.c.pk))
            return [Message(role=r.role, text=r.text) for r in rows]

    def add_message(self, trip_id: str, member_id: str, message: Message) -> None:
        with self.engine.begin() as c:
            c.execute(insert(messages_t).values(trip_id=trip_id, member_id=member_id, role=message.role, text=message.text))

    # ---------- 多人协作 ----------

    def preferences(self, trip_id: str) -> list[MemberPreferences]:
        with self.engine.connect() as c:
            rows = c.scalars(select(preferences_t.c.data).where(preferences_t.c.trip_id == trip_id)
                             .order_by(preferences_t.c.pk))
            return [MemberPreferences.model_validate(r) for r in rows]

    def save_preferences(self, trip_id: str, prefs: MemberPreferences) -> None:
        data = prefs.model_dump(mode="json")
        cond = (preferences_t.c.trip_id == trip_id) & (preferences_t.c.member_id == prefs.member_id)
        with self.engine.begin() as c:
            if c.scalar(select(preferences_t.c.pk).where(cond)):
                c.execute(update(preferences_t).where(cond).values(data=data))
            else:
                c.execute(insert(preferences_t).values(trip_id=trip_id, member_id=prefs.member_id, data=data))

    def latest_summary(self, trip_id: str) -> PreferenceSummary | None:
        with self.engine.connect() as c:
            row = c.scalar(select(summaries_t.c.data).where(summaries_t.c.trip_id == trip_id)
                           .order_by(summaries_t.c.pk.desc()).limit(1))
            return PreferenceSummary.model_validate(row) if row else None

    def save_summary(self, trip_id: str, summary: PreferenceSummary) -> None:
        with self.engine.begin() as c:
            c.execute(insert(summaries_t).values(trip_id=trip_id, data=summary.model_dump(mode="json")))

    def polls(self, trip_id: str) -> list[Poll]:
        with self.engine.connect() as c:
            rows = c.scalars(select(polls_t.c.data).where(polls_t.c.trip_id == trip_id).order_by(polls_t.c.pk))
            return [Poll.model_validate(r) for r in rows]

    def save_poll(self, trip_id: str, poll: Poll) -> None:
        data = poll.model_dump(mode="json")
        cond = (polls_t.c.trip_id == trip_id) & (polls_t.c.id == poll.id)
        with self.engine.begin() as c:
            if c.scalar(select(polls_t.c.pk).where(cond)):
                c.execute(update(polls_t).where(cond).values(status=poll.status, data=data))
            else:
                c.execute(insert(polls_t).values(trip_id=trip_id, id=poll.id, status=poll.status, data=data))

    def activity(self, trip_id: str, limit: int = 50) -> list[ActivityEvent]:
        with self.engine.connect() as c:
            rows = c.execute(select(activity_t.c.pk, activity_t.c.data).where(activity_t.c.trip_id == trip_id)
                             .order_by(activity_t.c.pk.desc()).limit(limit))
            return [ActivityEvent.model_validate({**r.data, "id": r.pk}) for r in rows]

    # ---------- 账目 ----------

    def _upsert(self, table, trip_id: str, row_id: str, data: dict, **extra) -> None:
        cond = (table.c.trip_id == trip_id) & (table.c.id == row_id)
        with self.engine.begin() as c:
            if c.scalar(select(table.c.pk).where(cond)):
                c.execute(update(table).where(cond).values(data=data, **extra))
            else:
                c.execute(insert(table).values(trip_id=trip_id, id=row_id, data=data, **extra))

    def expenses(self, trip_id: str) -> list[Expense]:
        with self.engine.connect() as c:
            rows = c.scalars(select(expenses_t.c.data).where(expenses_t.c.trip_id == trip_id).order_by(expenses_t.c.pk))
            return [Expense.model_validate(r) for r in rows]

    def save_expense(self, trip_id: str, expense: Expense) -> None:
        self._upsert(expenses_t, trip_id, expense.id, expense.model_dump(mode="json"), status=expense.status)

    def delete_expense(self, trip_id: str, expense_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(expenses_t).where((expenses_t.c.trip_id == trip_id) & (expenses_t.c.id == expense_id)))

    def transfers(self, trip_id: str) -> list[Transfer]:
        with self.engine.connect() as c:
            rows = c.scalars(select(transfers_t.c.data).where(transfers_t.c.trip_id == trip_id).order_by(transfers_t.c.pk))
            return [Transfer.model_validate(r) for r in rows]

    def save_transfer(self, trip_id: str, transfer: Transfer) -> None:
        self._upsert(transfers_t, trip_id, transfer.id, transfer.model_dump(mode="json"))

    def delete_transfer(self, trip_id: str, transfer_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(transfers_t).where((transfers_t.c.trip_id == trip_id) & (transfers_t.c.id == transfer_id)))

    def money_settings(self, trip_id: str) -> MoneySettings:
        with self.engine.connect() as c:
            row = c.scalar(select(money_settings_t.c.data).where(money_settings_t.c.trip_id == trip_id))
            return MoneySettings.model_validate(row) if row else MoneySettings()

    def save_money_settings(self, trip_id: str, settings: MoneySettings) -> None:
        data = settings.model_dump(mode="json")
        with self.engine.begin() as c:
            if c.scalar(select(money_settings_t.c.trip_id).where(money_settings_t.c.trip_id == trip_id)):
                c.execute(update(money_settings_t).where(money_settings_t.c.trip_id == trip_id).values(data=data))
            else:
                c.execute(insert(money_settings_t).values(trip_id=trip_id, data=data))

    def guides(self, trip_id: str) -> list[Guide]:
        with self.engine.connect() as c:
            rows = c.scalars(select(guides_t.c.data).where(guides_t.c.trip_id == trip_id).order_by(guides_t.c.pk))
            return [Guide.model_validate(r) for r in rows]

    def save_guide(self, trip_id: str, guide: Guide) -> None:
        self._upsert(guides_t, trip_id, guide.id, guide.model_dump(mode="json"))

    def delete_guide(self, trip_id: str, guide_id: str) -> None:
        with self.engine.begin() as c:
            c.execute(delete(guides_t).where((guides_t.c.trip_id == trip_id) & (guides_t.c.id == guide_id)))

    def add_activity(self, trip_id: str, event: ActivityEvent) -> ActivityEvent:
        with self.engine.begin() as c:
            pk = c.execute(insert(activity_t).values(trip_id=trip_id, data=event.model_dump(mode="json", exclude={"id"}))
                           ).inserted_primary_key[0]
        return event.model_copy(update={"id": pk})
