"""数据库存储：重启后数据还在；同一个版本号不能写两次。"""

import pytest

from tripagent.llm.base import Message
from tripagent.schema import Decision, Version
from tripagent.service import TripService
from tripagent.store import SqlStore, VersionExists

TRIP = "nz_2026"


def test_everything_survives_a_restart(tmp_path, trip, decisions, places, drive_times):
    url = f"sqlite:///{(tmp_path / 'trip.db').as_posix()}"
    service = TripService(SqlStore(url), places, drive_times)
    service.create_trip(trip, decisions)
    patch = service.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "汉堡午餐"}}],
                            summary="改午餐名称")
    service.confirm(patch.id, confirmed_by="m_nina")
    service.record_decision(TRIP, Decision(id="D-100", content="晚上不安排活动", kind="preference"))
    service.add_message(TRIP, "m_nina", Message("user", "你好"))

    reopened = TripService(SqlStore(url), places, drive_times)  # 模拟服务重启
    assert reopened.trip_ids() == [TRIP]
    assert reopened.current(TRIP).version == 2
    assert reopened.current(TRIP).find_item("d1_lunch").title == "汉堡午餐"
    assert reopened.patch(patch.id).status == "applied"
    assert "D-100" in {d.id for d in reopened.decisions(TRIP)}
    assert [m.text for m in reopened.conversation(TRIP, "m_nina")] == ["你好"]
    assert [v.version for v in reopened.history(TRIP)] == [1, 2]


def test_same_version_cannot_be_written_twice(tmp_path, trip):
    store = SqlStore(f"sqlite:///{(tmp_path / 'trip.db').as_posix()}")
    store.add_version(Version(trip_id=TRIP, version=1, state=trip))
    with pytest.raises(VersionExists):
        store.add_version(Version(trip_id=TRIP, version=1, state=trip))
