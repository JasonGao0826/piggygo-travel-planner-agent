import copy
from datetime import datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from tripagent.api import create_app
from tripagent.auth import Auth
from tripagent.io import load_decisions, trip_from_dict
from tripagent.kb import load_places
from tripagent.llm.fake import ScriptedLLM
from tripagent.routing import StaticDriveTimes
from tripagent.schema import Trip
from tripagent.service import TripService
from tripagent.store import InMemoryStore, SqlStore
from tripagent.validator import ValidationContext

FIXTURES = Path(__file__).parent / "fixtures"
NZ = ZoneInfo("Pacific/Auckland")


@cache
def _raw_trip() -> dict:
    return yaml.safe_load((FIXTURES / "nz_trip_v8.yaml").read_text(encoding="utf-8"))


def nz(day: str, hhmm: str) -> datetime:
    """nz("2026-11-26", "11:30") → 新西兰当地时间。"""
    return datetime.fromisoformat(f"{day}T{hhmm}").replace(tzinfo=NZ)


def rebuild(trip: Trip) -> Trip:
    """测试里直接改了字段之后，重新走一遍校验，确保数据结构仍然合法。"""
    return Trip.model_validate(trip.model_dump()).normalized()


@pytest.fixture
def trip() -> Trip:
    return trip_from_dict(copy.deepcopy(_raw_trip()))


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """同一组测试在内存存储和数据库存储上各跑一遍。"""
    if request.param == "memory":
        return InMemoryStore()
    return SqlStore(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture(scope="session")
def places():
    return load_places(FIXTURES / "nz_places.yaml")


@pytest.fixture(scope="session")
def drive_times():
    return StaticDriveTimes.from_yaml(FIXTURES / "nz_drive_times.yaml")


@pytest.fixture
def decisions():
    return load_decisions(FIXTURES / "nz_decisions.yaml")


@pytest.fixture
def make_ctx(places, drive_times, decisions):
    def _make(trip: Trip, with_decisions: bool = True, drive=None) -> ValidationContext:
        return ValidationContext(trip, places, drive or drive_times, decisions if with_decisions else [])
    return _make

# ---------- 上线模式（邀请链接登录）的网站 ----------

ADMIN = {"x-admin-token": "admin-secret"}


@pytest.fixture
def site(places, drive_times, tmp_path):
    """一个上线模式（邀请链接登录）的网站，载入了新西兰演示行程的管理接口可用。site.client() 相当于一台新设备（各自的 cookie）。"""
    class Site:
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        auth = Auth(engine, "invite")
        service = TripService(InMemoryStore(), places, drive_times)
        steps: list = []

        uploads = tmp_path / "uploads"

        def build(self, **kw):
            llm = ScriptedLLM(self.steps)
            settings = {"beta_code": "piggy-beta", "admin_token": "admin-secret", **kw}
            self.app = create_app(self.service, llm_factory=lambda: llm, uploads_dir=self.uploads, auth=self.auth,
                                  **settings)
            return self

        def client(self) -> TestClient:
            return TestClient(self.app)

        def links(self) -> dict[str, str]:
            admin = self.client()
            admin.post("/api/admin/demo", headers=ADMIN)
            return {row["member_id"]: row["token"] for row in admin.post("/api/admin/trips/nz_2026/invites", headers=ADMIN).json()}

        def member(self, token: str) -> TestClient:
            c = self.client()
            assert c.post("/api/join", json={"token": token}).status_code == 200
            return c
    return Site().build()
