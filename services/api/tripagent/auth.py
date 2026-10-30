"""登录：每人一个专属邀请链接（内测用，设计规格 13.2 节的简化版）。

- 发起人给每个成员生成一个链接 /join/<token>，点开后浏览器记住"我是行程 X 里的成员 Y"（会话 cookie）；
- 之后所有请求都按会话认人：网页传来的 member_id 一律以会话为准，不能冒充别人；
- 链接就是钥匙：数据库里只存它的哈希；重新生成后旧链接立即失效；
- 每次用链接登录、新建行程，都换一个新的会话（原来加入的行程带过去），
  这样别人事先塞进浏览器的会话拿不到你的身份；
- 本地开发用 open 模式（不登录，页面上可以切换身份），上线用 invite 模式。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, UniqueConstraint, delete, insert, select, update

TOKEN_BYTES = 24
COOKIE = "pg_session"
SESSION_DAYS = 180   # 登录多久后要重新打开链接（和 cookie 的有效期一样）


class AuthError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite 读回来的时间没有时区，按 UTC 处理。"""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_meta = MetaData()
invites_t = Table(
    "invites", _meta,
    Column("token_hash", String(64), primary_key=True),
    Column("trip_id", String(64), nullable=False, index=True),
    Column("member_id", String(64), nullable=False),
    Column("created_by", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
)
sessions_t = Table(
    "sessions", _meta,
    Column("session_hash", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
session_members_t = Table(
    "session_members", _meta,
    Column("session_hash", String(64), nullable=False, index=True),
    Column("trip_id", String(64), nullable=False),
    Column("member_id", String(64), nullable=False),
    UniqueConstraint("session_hash", "trip_id"),
)
MAX_DEVICES = 10   # 每个成员最多同时在几台设备（会话）上登录，多了就让最早的退出


class Auth:
    """邀请链接和会话。用和行程同一个数据库（SQLite 或 PostgreSQL），测试时用内存 SQLite。"""

    def __init__(self, engine, mode: str = "open"):
        if mode not in ("open", "invite"):
            raise ValueError("AUTH_MODE 只能是 open 或 invite")
        self.engine = engine
        self.mode = mode
        _meta.create_all(engine)
        self.purge_expired()

    @property
    def enabled(self) -> bool:
        return self.mode == "invite"

    # ---------- 邀请链接 ----------

    def new_invite(self, trip_id: str, member_id: str, created_by: str | None = None) -> str:
        """给成员生成新链接；这个成员以前的链接全部作废。返回明文 token（只有这一次能看到）。"""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        with self.engine.begin() as c:
            c.execute(update(invites_t).where((invites_t.c.trip_id == trip_id) & (invites_t.c.member_id == member_id))
                      .values(revoked=True))
            c.execute(insert(invites_t).values(token_hash=_hash(token), trip_id=trip_id, member_id=member_id,
                                               created_by=created_by, created_at=_now(), revoked=False))
        return token

    def invite_status(self, trip_id: str) -> dict[str, datetime]:
        """每个成员当前有效链接的生成时间（没有链接的成员不在里面）。"""
        with self.engine.connect() as c:
            rows = c.execute(select(invites_t.c.member_id, invites_t.c.created_at)
                             .where((invites_t.c.trip_id == trip_id) & (invites_t.c.revoked == False)))  # noqa: E712
            return {r.member_id: r.created_at for r in rows}

    # ---------- 会话 ----------

    def peek(self, token: str) -> tuple[str, str]:
        """看看这个链接是谁的（行程 id，成员 id），不登录。"""
        with self.engine.connect() as c:
            row = c.execute(select(invites_t).where(invites_t.c.token_hash == _hash(token or ""))).first()
        if row is None or row.revoked:
            raise AuthError("这个邀请链接无效或已经作废了，请找发起人要一个新的。")
        return row.trip_id, row.member_id

    def join(self, token: str, session: str | None = None) -> tuple[str, str, str]:
        """用邀请链接加入：返回（新的会话 token，行程 id，成员 id）。原来会话加入的其他行程会带到新会话。"""
        with self.engine.begin() as c:
            row = c.execute(select(invites_t).where(invites_t.c.token_hash == _hash(token or ""))).first()
            if row is None or row.revoked:
                raise AuthError("这个邀请链接无效或已经作废了，请找发起人要一个新的。")
            session = _rotate(c, session)
            sh = _hash(session)
            c.execute(delete(session_members_t).where((session_members_t.c.session_hash == sh)
                                                      & (session_members_t.c.trip_id == row.trip_id)))
            c.execute(insert(session_members_t).values(session_hash=sh, trip_id=row.trip_id, member_id=row.member_id))
            older = c.execute(
                select(session_members_t.c.session_hash)
                .join(sessions_t, sessions_t.c.session_hash == session_members_t.c.session_hash)
                .where((session_members_t.c.trip_id == row.trip_id) & (session_members_t.c.member_id == row.member_id))
                .order_by(sessions_t.c.created_at.desc())).all()[MAX_DEVICES:]
            for r in older:
                c.execute(delete(session_members_t).where((session_members_t.c.session_hash == r.session_hash)
                                                          & (session_members_t.c.trip_id == row.trip_id)))
        return session, row.trip_id, row.member_id

    def rotate(self, session: str | None) -> str:
        """换一个新的会话 token（新建行程时用）：原来加入的行程带过去，旧 token 作废。"""
        with self.engine.begin() as c:
            return _rotate(c, session)

    def add_membership(self, session: str, trip_id: str, member_id: str) -> None:
        sh = _hash(session)
        with self.engine.begin() as c:
            c.execute(delete(session_members_t).where((session_members_t.c.session_hash == sh)
                                                      & (session_members_t.c.trip_id == trip_id)))
            c.execute(insert(session_members_t).values(session_hash=sh, trip_id=trip_id, member_id=member_id))

    def memberships(self, session: str | None) -> dict[str, str]:
        """会话 → {行程 id: 成员 id}。重新生成某人的链接时，先用 leave_all 让他已登录的设备都退出。"""
        if not session:
            return {}
        sh = _hash(session)
        with self.engine.connect() as c:
            created = c.scalar(select(sessions_t.c.created_at).where(sessions_t.c.session_hash == sh))
            if created is None or _aware(created) < _now() - timedelta(days=SESSION_DAYS):
                return {}   # 没有这个会话，或者已经过期
            rows = c.execute(select(session_members_t.c.trip_id, session_members_t.c.member_id)
                             .where(session_members_t.c.session_hash == sh)).all()
        return {r.trip_id: r.member_id for r in rows}

    def leave_all(self, trip_id: str, member_id: str) -> None:
        """作废某个成员在所有设备上的登录（重新生成链接时一起做）。"""
        with self.engine.begin() as c:
            c.execute(delete(session_members_t).where((session_members_t.c.trip_id == trip_id)
                                                      & (session_members_t.c.member_id == member_id)))

    def logout(self, session: str | None) -> None:
        """在这台设备上退出：删掉这个会话。"""
        if not session:
            return
        with self.engine.begin() as c:
            _drop(c, _hash(session))

    def purge_expired(self) -> None:
        """删掉过期的会话和它们加入的行程（启动时做一次）。"""
        cutoff = _now() - timedelta(days=SESSION_DAYS)
        with self.engine.begin() as c:
            old = [r.session_hash for r in c.execute(select(sessions_t.c.session_hash).where(sessions_t.c.created_at < cutoff))]
            for sh in old:
                _drop(c, sh)


def _drop(c, sh: str) -> None:
    c.execute(delete(session_members_t).where(session_members_t.c.session_hash == sh))
    c.execute(delete(sessions_t).where(sessions_t.c.session_hash == sh))


def _rotate(c, session: str | None) -> str:
    """新建一个会话。旧会话没过期的话，把它加入的行程挪过来，然后删掉旧会话；过期的旧会话直接删掉，不带过来。
    （两个页面同时用同一个旧会话换新时，后到的那个拿不到原来的行程，重新打开链接就好；
    不做"补发"，因为那样拿着旧会话的人能借机拿到刚登录的人的身份。）"""
    new = secrets.token_urlsafe(TOKEN_BYTES)
    nh = _hash(new)
    c.execute(insert(sessions_t).values(session_hash=nh, created_at=_now()))
    if not session:
        return new
    old = _hash(session)
    created = c.scalar(select(sessions_t.c.created_at).where(sessions_t.c.session_hash == old))
    if created is not None:
        if _aware(created) >= _now() - timedelta(days=SESSION_DAYS):
            c.execute(update(session_members_t).where(session_members_t.c.session_hash == old).values(session_hash=nh))
        _drop(c, old)
    return new
