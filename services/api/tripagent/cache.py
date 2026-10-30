"""带过期时间的缓存，存外部服务（MCP、实时路况、天气……）返回的数据。

每条都记下抓取时间，用的时候能告诉用户"这是几点的数据"（设计规格 8.2 节：事实带来源和时间）。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import JSON, Column, DateTime, Engine, MetaData, String, Table, create_engine, delete, insert, select


@dataclass
class Cached:
    value: Any
    fetched_at: datetime


class Cache(Protocol):
    def get(self, key: str) -> Cached | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int) -> Cached: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryCache:
    def __init__(self):
        self._rows: dict[str, tuple[Any, datetime, datetime]] = {}

    def get(self, key: str) -> Cached | None:
        row = self._rows.get(key)
        if row is None or row[2] <= _now():
            return None
        return Cached(row[0], row[1])

    def set(self, key: str, value: Any, ttl_seconds: int) -> Cached:
        now = _now()
        self._rows[key] = (value, now, now + timedelta(seconds=ttl_seconds))
        return Cached(value, now)


_meta = MetaData()
_cache_t = Table(
    "cache", _meta,
    Column("key", String(255), primary_key=True),
    Column("value", JSON, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)


class SqlCache:
    """和 SqlStore 用同一个数据库，单独一张 cache 表。"""

    def __init__(self, url_or_engine: str | Engine):
        self.engine = create_engine(url_or_engine, future=True) if isinstance(url_or_engine, str) else url_or_engine
        _meta.create_all(self.engine)

    def get(self, key: str) -> Cached | None:
        with self.engine.connect() as c:
            row = c.execute(select(_cache_t).where(_cache_t.c.key == key)).first()
        if row is None:
            return None
        expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
        if expires <= _now():
            return None
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        return Cached(row.value, fetched)

    def set(self, key: str, value: Any, ttl_seconds: int) -> Cached:
        now = _now()
        json.dumps(value)  # 只缓存能存成 JSON 的数据
        with self.engine.begin() as c:
            c.execute(delete(_cache_t).where(_cache_t.c.key == key))
            c.execute(insert(_cache_t).values(key=key, value=value, fetched_at=now,
                                              expires_at=now + timedelta(seconds=ttl_seconds)))
        return Cached(value, now)
