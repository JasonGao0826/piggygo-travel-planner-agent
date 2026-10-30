"""正式知识库（data/kb/nz/places.yaml，经官网核实）的检查。"""

from pathlib import Path

import pytest

from tripagent.kb import load_places
from tripagent.validator import errors, validate

KB = Path(__file__).parents[3] / "data" / "kb" / "nz" / "places.yaml"
VISIT_KINDS = {"activity", "attraction", "trailhead", "viewpoint", "restaurant"}


@pytest.fixture(scope="module")
def kb():
    return load_places(KB)


def test_kb_loads_and_every_place_has_sources(kb):
    assert len(kb) >= 27
    for place in kb.values():
        assert place.sources, f"{place.id} 没有记录来源"
        assert place.verified_at is not None, f"{place.id} 没有核实日期"


def test_kb_covers_all_places_visited_in_v8(kb, places):
    """V8 行程里去的景点和项目，正式知识库里都要有。"""
    visited = {pid for pid, p in places.items() if p.kind in VISIT_KINDS}
    assert visited - kb.keys() == set()


def test_v8_has_no_errors_against_real_kb(trip, kb, places, drive_times, decisions):
    from tripagent.validator import ValidationContext
    merged = {**places, **kb}  # 机场、住宿、部分城镇只在测试数据里
    assert errors(validate(ValidationContext(trip, merged, drive_times, decisions))) == []
