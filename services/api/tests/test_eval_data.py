"""回放测试数据的格式检查：文件能解析，引用的地点和项目类型都在知识库里。"""

from pathlib import Path

from tripagent.evals import ItemCheck, NoItemCheck, ConsistentValueCheck, load_replay

REPLAY = Path(__file__).parents[3] / "tests" / "eval" / "replay_nz.yaml"


def test_replay_file_parses():
    case = load_replay(REPLAY)
    assert [s.step for s in case.steps] == list(range(1, 14))
    assert len(case.intake.members) == 4


def test_replay_references_known_places_and_tags(places):
    case = load_replay(REPLAY)
    known_tags = {t for p in places.values() for t in p.tags}
    known_cities = {p.city for p in places.values()}
    for step in case.steps:
        for check in step.expect:
            matches = []
            if isinstance(check, (ItemCheck, NoItemCheck)):
                matches.append(check.match)
            if isinstance(check, ConsistentValueCheck):
                matches.append(check.item)
            for m in matches:
                assert m.place_id is None or m.place_id in places, (step.step, m.place_id)
                assert m.activity_tag is None or m.activity_tag in known_tags, (step.step, m.activity_tag)
                assert m.city is None or m.city in known_cities, (step.step, m.city)
