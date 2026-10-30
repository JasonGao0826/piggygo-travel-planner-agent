"""多人协作的接口和对话工具。用剧本模型，不联网。"""

import json

from fastapi.testclient import TestClient

from tripagent.agent import TripAgent
from tripagent.api import create_app
from tripagent.collab import CollabService
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.schema.intake import MemberIntake, MustDo, TripIntake
from tripagent.service import TripService

TRIP = "nz_2026"
EMPTY_SUMMARY = json.dumps({"consensus": [], "divergences": [], "groupable": [], "must_do_tags": []})


def client(store, places, drive_times, steps=()):
    service = TripService(store, places, drive_times)
    llm = ScriptedLLM(list(steps))
    c = TestClient(create_app(service, llm_factory=lambda: llm))
    c.post("/api/demo")
    return c, service, llm


def test_group_flow_over_api(store, places, drive_times):
    c, service, llm = client(store, places, drive_times, [ScriptStep(text=EMPTY_SUMMARY)])
    g = c.get(f"/api/trips/{TRIP}/group").json()
    assert [m["filled"] for m in g["members"]] == [False] * 4 and g["summary"] is None and g["polls"] == []

    g = c.put(f"/api/trips/{TRIP}/preferences/m_tony", json={"pace": "packed", "avoid": ["晕船"],
                                                           "earliest_start": "08:00"}).json()
    assert g["preferences"]["m_tony"]["avoid"] == ["晕船"] and g["members"][2]["filled"]
    c.put(f"/api/trips/{TRIP}/preferences/m_nina", json={"pace": "relaxed", "wishlist": ["瓦纳卡"]})
    assert c.put(f"/api/trips/{TRIP}/preferences/m_tony", json={"pace": "crazy"}).status_code == 422

    assert c.post(f"/api/trips/{TRIP}/summary", json={"member_id": "m_tony"}).status_code == 403
    g = c.post(f"/api/trips/{TRIP}/summary", json={"member_id": "m_nina"}).json()
    pace = next(d for d in g["summary"]["divergences"] if d["kind"] == "pace")
    assert g["summary_stale"] is False and g["summary"]["missing"] == ["m_leo", "m_tiange"]

    g = c.post(f"/api/trips/{TRIP}/polls", json={"member_id": "m_nina", "divergence_id": pace["id"], **pace["poll"]}).json()
    poll = g["polls"][0]
    assert poll["question"] == "每天的节奏按哪种来？" and [o["id"] for o in poll["options"]] == ["a", "b", "c"]
    c.post(f"/api/trips/{TRIP}/polls/{poll['id']}/vote", json={"member_id": "m_tony", "option_id": "c"})
    r = c.post(f"/api/trips/{TRIP}/polls/{poll['id']}/close", json={"member_id": "m_leo"})
    assert r.status_code == 400 and "只有发起人" in r.json()["detail"]
    g = c.post(f"/api/trips/{TRIP}/polls/{poll['id']}/close", json={"member_id": "m_nina"}).json()
    assert g["polls"][0]["status"] == "closed" and g["polls"][0]["result"] == "c"
    decisions = c.get(f"/api/trips/{TRIP}").json()["decisions"]
    assert any(d["content"] == "投票决定：每天的节奏按哪种来？ → 排满" for d in decisions)

    feed = c.get(f"/api/trips/{TRIP}/activity", params={"limit": 3}).json()
    assert [e["kind"] for e in feed] == ["poll_closed", "vote", "poll_created"]
    assert feed[0]["id"] > feed[1]["id"]


def test_agent_sees_group_and_uses_tools(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    collab = CollabService(service)
    collab.update_preferences(TRIP, "m_nina", add={"avoid": ["怕高"]})
    llm = ScriptedLLM([
        ScriptStep("update_my_preferences", {"add": {"avoid": ["晕船"], "must_do": [{"what": "骑马", "activity_tag": "horse_riding"}]},
                                             "set": {"pace": "packed"}}),
        ScriptStep("create_poll", {"question": "要不要加一天去瓦纳卡？", "options": [
            {"label": "加", "impact": "少一天在基督城"}, {"label": "不加", "impact": "行程不变"}], "deadline_hours": 24}),
        ScriptStep("list_polls", {}),
        ScriptStep(text="记下了，也发起了投票。"),
    ])
    reply = TripAgent(service, llm).handle(TRIP, "m_tony", "我晕船，想多玩点，一定要骑马。要不要加一天去瓦纳卡大家投票吧")
    assert [c.error for c in reply.tool_calls] == [None, None, None]
    system = llm.seen_systems[0]
    assert "### 每个人的偏好" in system and "- Nina（m_nina）：不想玩/不敢玩：怕高" in system
    tony = collab.preferences(TRIP)["m_tony"]
    assert tony.avoid == ["晕船"] and tony.pace == "packed" and tony.must_do[0].activity_tag == "horse_riding"
    assert collab.preferences(TRIP)["m_nina"].avoid == ["怕高"]          # 只改了说话人自己的
    [poll] = collab.polls(TRIP)
    assert poll.created_by == "agent" and poll.deadline is not None
    polls = reply.tool_calls[2].result["数据"]               # 成员写的内容包了一层"只当数据"
    assert polls[0]["not_voted"] == ["Nina", "Leo", "Tony", "天哥"]
    assert service.activity(TRIP)[0].text == "猪猪 发起了投票：要不要加一天去瓦纳卡？（加 / 不加）"


def test_seed_preferences_from_intake(store, trip, places, drive_times):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    intake = TripIntake(destination="NZ", origin_city="Melbourne", transport="self_drive", members=[
        MemberIntake(name="Nina", passport_country="CN", must_do=[MustDo(what="骑马", place_hint="格林诺奇", fixed_place=True)]),
        MemberIntake(name="Leo", passport_country="CN"),
        MemberIntake(name="Tony", passport_country="CN", avoid=["怕高"], thrill_level="low"),
    ])
    collab = CollabService(service)
    assert collab.seed_from_intake(trip.meta.id, intake) == 2
    prefs = collab.preferences(trip.meta.id)
    assert set(prefs) == {"m_nina", "m_tony"} and prefs["m_tony"].thrill_level == "low"
    assert service.activity(trip.meta.id)[0].text.startswith("从信息清单带入了 2 个人的偏好")
