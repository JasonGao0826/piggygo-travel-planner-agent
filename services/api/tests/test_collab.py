"""多人协作：每人偏好、意见汇总、投票、动态。用剧本模型，不联网。"""

import json
from datetime import datetime, time, timedelta, timezone

import pytest

from tripagent.collab import CollabService
from tripagent.collab.summary import structured_summary
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.schema.collab import MemberPreferences, PollOptionDraft, PreferenceForm
from tripagent.schema.intake import MustDo
from tripagent.service import ServiceError, TripService

TRIP = "nz_2026"
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
NAMES = {"m_nina": "Nina", "m_leo": "Leo", "m_tony": "Tony", "m_tiange": "天哥"}


@pytest.fixture
def collab(store, trip, places, drive_times, decisions):
    service = TripService(store, places, drive_times)
    service.create_trip(trip, decisions)
    return CollabService(service)


def prefs(member_id: str, **kw) -> MemberPreferences:
    return MemberPreferences(member_id=member_id, updated_at=NOW, **kw)


def llm_summary(**kw) -> ScriptedLLM:
    body = {"consensus": [], "divergences": [], "groupable": [], "must_do_tags": [], **kw}
    return ScriptedLLM([ScriptStep(text=json.dumps(body, ensure_ascii=False))])


def kinds(events):
    return [e.kind for e in events]


# ---------------- 代码比较的部分 ----------------

def test_structured_summary_divergences_and_consensus():
    consensus, divergences = structured_summary([
        prefs("m_nina", pace="relaxed", max_daily_drive_hours=4, earliest_start=time(8, 30),
              budget_per_person={"amount": 2000, "currency": "AUD"}, thrill_level="low", diet=["不吃辣"]),
        prefs("m_tony", pace="packed", max_daily_drive_hours=7, earliest_start=time(8, 0),
              budget_per_person={"amount": 3500, "currency": "AUD"}, thrill_level="high"),
    ], NAMES)
    by_kind = {d.kind: d for d in divergences}
    assert set(by_kind) == {"pace", "drive", "budget", "thrill"}
    assert by_kind["pace"].detail == "Nina 想悠闲；Tony 想排满"
    assert [o.label for o in by_kind["pace"].poll.options] == ["悠闲", "适中", "排满"]
    assert [o.label for o in by_kind["drive"].poll.options] == ["不超过 4 小时", "不超过 7 小时"]
    assert by_kind["thrill"].poll is None and "分组" in by_kind["thrill"].suggestion
    topics = {c.topic: c.detail for c in consensus}
    assert topics == {"出发时间": "早上 8:30 以后出发", "饮食": "Nina：不吃辣"}
    # 同一主题的分歧 id 每次都一样，已经发起的投票能对上
    assert structured_summary([prefs("m_nina", pace="relaxed"), prefs("m_tony", pace="packed")], NAMES)[1][0].id == by_kind["pace"].id


def test_single_member_is_not_compared():
    assert structured_summary([prefs("m_nina", pace="relaxed", max_daily_drive_hours=3)], NAMES) == ([], [])


# ---------------- 偏好 ----------------

def test_save_and_update_preferences(collab):
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(avoid=["晕船"], pace="packed"))
    p = collab.update_preferences(TRIP, "m_tony", add={"avoid": ["怕高", "晕船"], "wishlist": ["瓦纳卡"]},
                                  set_={"max_daily_drive_hours": 6})
    assert p.avoid == ["晕船", "怕高"] and p.wishlist == ["瓦纳卡"] and p.pace == "packed" and p.max_daily_drive_hours == 6
    p = collab.update_preferences(TRIP, "m_tony", remove={"avoid": ["晕船"]})
    assert p.avoid == ["怕高"]
    assert collab.preferences(TRIP)["m_tony"].avoid == ["怕高"]
    with pytest.raises(ServiceError):
        collab.update_preferences(TRIP, "m_tony", set_={"member_id": "m_nina"})
    with pytest.raises(ServiceError):
        collab.save_preferences(TRIP, "m_nobody", PreferenceForm())
    events = collab.service.activity(TRIP)
    assert events[0].text == "Tony 更新了自己的偏好（想去 1 处、不想玩 1 项）" and events[0].actor == "m_tony"


# ---------------- 汇总 ----------------

def test_summarize_merges_model_and_code_and_records_must_do(collab):
    collab.save_preferences(TRIP, "m_nina", PreferenceForm(
        pace="relaxed", avoid=["怕高"], must_do=[MustDo(what="看萤火虫", place_hint="蒂阿瑙")]))
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(
        pace="packed", wishlist=["瓦纳卡跳伞"], must_do=[MustDo(what="斜坡滑车")]))
    llm = llm_summary(
        consensus=[{"topic": "瓦纳卡", "detail": "Tony 想去，行程里已经安排", "member_ids": ["m_tony"]}],
        divergences=[{"topic": "跳伞", "detail": "Tony 想跳伞，Nina 怕高", "suggestion": "可以分组",
                      "positions": [{"member_ids": ["m_tony"], "stance": "想跳"},
                                    {"member_ids": ["m_nina", "m_ghost"], "stance": "怕高"}],
                      "poll": {"question": "跳伞怎么安排？", "options": [
                          {"label": "想跳的人去，其他人逛小镇", "impact": "要分两组"},
                          {"label": "大家都不跳", "impact": "Tony 的必玩项目取消"}]}}],
        groupable=[{"topic": "跳伞", "detail": "只有 Tony 想跳", "member_ids": ["m_tony", "m_ghost"], "others": "逛瓦纳卡湖"}],
        must_do_tags=[{"member_id": "m_tony", "index": 0, "activity_tag": "luge"},
                      {"member_id": "m_nina", "index": 0, "activity_tag": "not_a_real_tag"}],
    )
    s = collab.summarize(TRIP, llm, by="m_nina")
    assert [c.topic for c in s.consensus] == ["瓦纳卡"]
    assert [d.kind for d in s.divergences] == ["activity", "pace"]
    assert s.divergences[0].positions[1].member_ids == ["m_nina"]          # 写错的成员 id 去掉
    assert s.groupable[0].member_ids == ["m_tony"]
    assert s.missing == ["m_leo", "m_tiange"] and set(s.based_on) == {"m_nina", "m_tony"}
    # 斜坡滑车有项目类型 → 记入决策账本，校验器会检查；萤火虫类型对不上 → 只记文字
    assert s.decision_ids == ["D-m_nina-1", "D-m_tony-1"]
    glow = next(x for x in collab.service.decisions(TRIP) if x.id == "D-m_nina-1")
    assert glow.requirement is None and glow.content == "必玩：看萤火虫（知识库里没有对应的项目类型，由助手对照）"
    d = next(x for x in collab.service.decisions(TRIP) if x.id == "D-m_tony-1")
    assert d.requirement.activity_tag == "luge" and d.proposed_by == "m_tony" and d.content == "必玩：斜坡滑车（锁定项目）"
    assert not collab.summary_is_stale(TRIP)
    assert collab.service.activity(TRIP)[0].text.startswith("汇总了大家的想法：共识 1 条、分歧 2 条、可以分组 1 条")


def test_must_do_changes_withdraw_old_decision(collab):
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(must_do=[MustDo(what="跳伞", activity_tag="skydiving")]))
    collab.summarize(TRIP, llm_summary())
    assert collab.summary_is_stale(TRIP) is False
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(must_do=[MustDo(what="蹦极", activity_tag="bungy")]))
    assert collab.summary_is_stale(TRIP)
    s = collab.summarize(TRIP, llm_summary())
    by_id = {d.id: d for d in collab.service.decisions(TRIP)}
    assert by_id["D-m_tony-1"].content == "必玩：蹦极（锁定项目）" and s.decision_ids == ["D-m_tony-1"]
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(pace="packed"))
    collab.summarize(TRIP, llm_summary())
    assert collab.service.decisions(TRIP, active_only=True) == [d for d in collab.service.decisions(TRIP, active_only=True)
                                                                if d.id != "D-m_tony-1"]


def test_must_do_matching_ledger_is_skipped(collab):
    # 决策账本里已经有"必玩：滑翔伞"（D-010），偏好里再写一次不重复记
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(must_do=[MustDo(what="滑翔伞", activity_tag="paragliding")]))
    assert collab.summarize(TRIP, llm_summary()).decision_ids == []


def test_must_do_already_in_ledger_is_not_duplicated(collab):
    # 决策账本里已经有"骑马必须在格林诺奇"（D-001，由 Nina 提出）
    d1 = next(d for d in collab.service.decisions(TRIP) if d.id == "D-001")
    collab.service.store.save_decision(TRIP, d1.model_copy(update={"proposed_by": "m_nina"}))
    collab.save_preferences(TRIP, "m_nina", PreferenceForm(must_do=[MustDo(
        what="骑马", activity_tag="horse_riding", place_hint="Glenorchy", fixed_place=True)]))
    s = collab.summarize(TRIP, llm_summary())
    assert s.decision_ids == []


def test_summary_only_numbers_skips_model(collab):
    collab.save_preferences(TRIP, "m_nina", PreferenceForm(pace="relaxed"))
    collab.save_preferences(TRIP, "m_leo", PreferenceForm(pace="relaxed"))
    llm = ScriptedLLM([])
    s = collab.summarize(TRIP, llm)
    assert llm.seen_systems == [] and s.consensus[0].detail == "大家都想悠闲"
    collab.store.save_preferences(TRIP, prefs("m_nina"))
    collab.store.save_preferences(TRIP, prefs("m_leo"))
    with pytest.raises(ServiceError, match="还没有人填偏好"):
        collab.summarize(TRIP, llm)


# ---------------- 投票 ----------------

def options(*labels):
    return [PollOptionDraft(label=x, impact=f"{x}的影响") for x in labels]


def test_poll_closes_when_everyone_voted(collab):
    poll = collab.create_poll(TRIP, "m_tony", "跳伞怎么安排？", options("分组", "都不跳"), divergence_id="dv_x")
    assert [o.id for o in poll.options] == ["a", "b"]
    collab.vote(TRIP, poll.id, "m_tony", "a")
    collab.vote(TRIP, poll.id, "m_leo", "b")
    collab.vote(TRIP, poll.id, "m_leo", "a")          # 可以改票
    collab.vote(TRIP, poll.id, "m_nina", "b")
    done = collab.vote(TRIP, poll.id, "m_tiange", "a")
    assert done.status == "closed" and done.result == "a" and done.closed_by == "system"
    d = next(x for x in collab.service.decisions(TRIP) if x.id == done.decision_id)
    assert d.content == "投票决定：跳伞怎么安排？ → 分组" and sorted(d.agreed_by) == ["m_leo", "m_tiange", "m_tony"]
    assert d.rationale.startswith("分组 3 票、都不跳 1 票") and d.proposed_by == "m_tony"
    texts = [e.text for e in collab.service.activity(TRIP)]
    assert texts[0] == "投票结束：跳伞怎么安排？ → 分组（分组 3 票、都不跳 1 票），已记入决策账本"
    assert "Leo 改投「分组」（跳伞怎么安排？）" in texts
    with pytest.raises(ServiceError, match="已经结束"):
        collab.vote(TRIP, poll.id, "m_nina", "a")


def test_tie_needs_owner(collab):
    poll = collab.create_poll(TRIP, "agent", "节奏？", options("悠闲", "排满"))
    for m, o in (("m_nina", "a"), ("m_leo", "b"), ("m_tony", "a"), ("m_tiange", "b")):
        poll = collab.vote(TRIP, poll.id, m, o)
    assert poll.status == "open"                              # 平票，不自动结束
    with pytest.raises(ServiceError, match="平票"):
        collab.close(TRIP, poll.id, "m_nina")
    with pytest.raises(ServiceError, match="只有发起人"):
        collab.close(TRIP, poll.id, "m_tony", "a")
    done = collab.close(TRIP, poll.id, "m_nina", "b")
    assert done.result == "b" and done.note == "Nina 拍板（平票）"
    d = next(x for x in collab.service.decisions(TRIP) if x.id == done.decision_id)
    assert d.proposed_by is None and "Nina 拍板（平票）" in d.rationale


def test_deadline_closes_clear_result(collab):
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    poll = collab.create_poll(TRIP, "m_leo", "第一晚吃什么？", options("Fergburger", "中餐"), deadline=soon)
    collab.vote(TRIP, poll.id, "m_leo", "a")
    collab.close_due(TRIP, soon + timedelta(hours=1))
    assert collab.poll(TRIP, poll.id).status == "open"          # 4 个人只有 1 个投了：不到一半，不自动结束
    collab.vote(TRIP, poll.id, "m_nina", "a")
    collab.close_due(TRIP, soon - timedelta(hours=1))
    assert collab.poll(TRIP, poll.id).status == "open"          # 还没到截止时间
    collab.close_due(TRIP, soon + timedelta(hours=1))
    assert collab.poll(TRIP, poll.id).status == "closed"
    with pytest.raises(ServiceError, match="1 小时以后"):
        collab.create_poll(TRIP, "m_leo", "过去的截止时间？", options("A", "B"), deadline=NOW)


def test_poll_validation(collab):
    with pytest.raises(ServiceError):
        collab.create_poll(TRIP, "m_tony", "只有一个选项", options("A"))
    poll = collab.create_poll(TRIP, "m_tony", "问题", options("A", "B"))
    with pytest.raises(ServiceError):
        collab.vote(TRIP, poll.id, "m_tony", "z")
    with pytest.raises(ServiceError):
        collab.vote(TRIP, "poll99", "m_tony", "a")


def test_summary_links_existing_poll(collab):
    collab.save_preferences(TRIP, "m_nina", PreferenceForm(pace="relaxed"))
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(pace="packed"))
    s = collab.summarize(TRIP, ScriptedLLM([]))
    dv = s.divergences[0]
    poll = collab.create_poll(TRIP, "m_nina", dv.poll.question, dv.poll.options, divergence_id=dv.id)
    collab.save_preferences(TRIP, "m_leo", PreferenceForm(pace="balanced"))
    s = collab.summarize(TRIP, ScriptedLLM([]))
    assert s.divergences[0].poll_id == poll.id


# ---------------- 动态 ----------------

def test_activity_feed_records_trip_events(collab):
    service = collab.service
    patch = service.propose(TRIP, [{"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "汉堡"}}],
                            "午餐改吃汉堡")
    service.confirm(patch.id, "m_leo")
    texts = [e.text for e in service.activity(TRIP)]
    assert texts[0] == "Leo 确认了改动「午餐改吃汉堡」，生成第 2 版"
    assert texts[-1].startswith("创建了行程（第 1 版")
    events = service.activity(TRIP, limit=1)
    assert len(events) == 1 and events[0].id > 1


def test_context_text_for_agent(collab):
    collab.save_preferences(TRIP, "m_tony", PreferenceForm(avoid=["怕高"], max_daily_drive_hours=6))
    collab.create_poll(TRIP, "m_tony", "跳伞？", options("跳", "不跳"))
    collab.vote(TRIP, "poll1", "m_tony", "a")
    text = collab.context_text(TRIP)
    assert "- Tony（m_tony）：不想玩/不敢玩：怕高；每天开车不超过 6 小时" in text
    assert "- Nina（m_nina）：还没填偏好" in text
    assert "- [poll1] 跳伞？：跳 1 票、不跳 0 票；还没投：Nina、Leo、天哥" in text
