"""对话流程测试：用写好剧本的假模型，检查工具、记忆和确认流程，不联网。"""

import pytest

from tripagent.agent import TripAgent
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.service import ServiceError, TripService

from .conftest import nz

TRIP = "nz_2026"


@pytest.fixture
def service(trip, decisions, places, drive_times, store):
    s = TripService(store, places, drive_times)
    s.create_trip(trip, decisions)
    return s


def agent_with(service, steps):
    llm = ScriptedLLM(steps)
    return TripAgent(service, llm), llm


def test_question_reads_memory_and_changes_nothing(service):
    agent, llm = agent_with(service, [ScriptStep("list_todos", {}), ScriptStep(text="还有 5 个项目没订。")])
    reply = agent.handle(TRIP, "m_tiange", "还有什么没订？")

    assert reply.text == "还有 5 个项目没订。" and reply.patches == []
    assert "霍比屯电影取景地标准导览" in str(reply.tool_calls[0].result["items_to_book"])
    assert service.current(TRIP).version == 1
    system = llm.seen_systems[0]
    assert "正在和你对话的是：天哥" in system and "[D-001] 骑马必须在格林诺奇" in system
    assert [m.role for m in service.conversation(TRIP, "m_tiange")] == ["user", "assistant"]


def test_decision_then_patch_then_user_confirms(service):
    agent, _ = agent_with(service, [
        ScriptStep("record_decision", {"content": "不去 Wai-O-Tapu 了", "kind": "rejection",
                                       "rejected_options": [{"description": "Wai-O-Tapu 地热公园", "place_id": "nz_wai_o_tapu"}]}),
        ScriptStep("propose_patch", {"summary": "11/30 下午取消 Wai-O-Tapu，漂流后直接回罗托鲁瓦", "ops": [
            {"op": "remove", "collection": "items", "id": "d8_waiotapu"},
            {"op": "remove", "collection": "items", "id": "d8_drive_waiotapu"},
            {"op": "update", "collection": "items", "id": "d8_drive_back", "set": {
                "from_place_id": "nz_kaituna_cascades",
                "start": nz("2026-11-30", "12:00").isoformat(), "end": nz("2026-11-30", "12:30").isoformat()}},
            {"op": "remove", "collection": "todos", "id": "todo_book_waiotapu"},
        ]}),
        ScriptStep(text="已记下，并提出了改动，请回复「确认」。"),
    ])
    reply = agent.handle(TRIP, "m_nina", "我们不去 Wai-O-Tapu 了")

    conflict = reply.tool_calls[0].result["now_conflicting_with_plan"]
    assert conflict[0]["code"] == "R16"
    patch_result = reply.tool_calls[1].result
    assert patch_result["can_be_confirmed"] and patch_result["issues"] == "没有新问题。"
    assert any("删除事件：Wai-O-Tapu 地热公园" in c for c in patch_result["changes"])
    assert any("出发地 Wai-O-Tapu 地热公园 → Kaituna 五级白水漂流" in c for c in patch_result["changes"])  # 显示地点名，不显示 id

    version = agent.confirm(TRIP, "m_nina")
    assert version.version == 2 and version.state.find_item("d8_waiotapu") is None
    assert [i for i in service.validate(TRIP) if i.severity == "error"] == []
    assert "已生成第 2 版" in service.conversation(TRIP, "m_nina")[-1].text


def test_tool_errors_go_back_to_the_model(service):
    agent, _ = agent_with(service, [
        ScriptStep("get_day", {"date": "2026-12-25"}),
        ScriptStep("propose_patch", {"summary": "写错 id", "ops": [
            {"op": "update", "collection": "items", "id": "no_such_item", "set": {"title": "x"}}]}),
        ScriptStep(text="出错了。"),
    ])
    reply = agent.handle(TRIP, "m_nina", "随便试试")
    assert "不在" in reply.tool_calls[0].error or "没有" in reply.tool_calls[0].error
    assert reply.tool_calls[1].result["status"] == "rejected"
    assert reply.patches == []


def test_locked_item_cannot_be_moved_without_consent(service):
    agent, _ = agent_with(service, [
        ScriptStep("propose_patch", {"summary": "打枪改到下午", "ops": [
            {"op": "update", "collection": "items", "id": "d4_real_guns", "set": {
                "start": nz("2026-11-26", "14:00").isoformat(), "end": nz("2026-11-26", "15:30").isoformat()}}]}),
        ScriptStep(text="这个已经付款锁定了。"),
    ])
    reply = agent.handle(TRIP, "m_leo", "打枪改到下午吧")
    result = reply.tool_calls[0].result
    assert not result["can_be_confirmed"] and result["issues"][0]["code"] == "R08"
    with pytest.raises(ServiceError):
        agent.confirm(TRIP, "m_leo")
