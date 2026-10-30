"""成员自己补充、修改资料（护照、签证、开车、驾照、年龄、体重）：只能改自己的，改完直接生成新版本，
校验和跟着资料走的待办（驾照翻译件、NZeTA）一起更新。"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tripagent.api import create_app
from tripagent.llm.fake import ScriptedLLM
from tripagent.planner.rules import NZETA_TODOS
from tripagent.schema import MemberProfile
from tripagent.service import ServiceError, TripService

TRIP = "nz_2026"
TODAY = date(2026, 10, 1)


@pytest.fixture
def service(store, trip, places, drive_times, decisions):
    s = TripService(store, places, drive_times)
    s.create_trip(trip, decisions)
    return s


def r11(issues) -> list[str]:
    return [i.message for i in issues if i.code == "R11"]


def todo(service, todo_id):
    return next((t for t in service.current(TRIP).todos if t.id == todo_id), None)


# ---------- 服务 ----------

def test_own_profile_change_is_a_new_version_and_rechecked(service):
    v, new = service.update_member_profile(TRIP, "m_leo", MemberProfile(age=23), today=TODAY)
    assert v.version == 2 and v.patch_id
    assert v.changelog == ["Leo 更新了自己的资料", "修改成员「Leo」：年龄 26 → 23"]
    # 年轻司机的提醒马上出现：既在这次返回的新问题里，也在行程检查里
    assert r11(new) == ["Leo 23 岁，不满 25 岁，租车可能收年轻司机附加费"]
    assert "Leo 23 岁，不满 25 岁，租车可能收年轻司机附加费" in r11(service.validate(TRIP))
    leo = service.current(TRIP).member("m_leo")
    assert (leo.age, leo.can_drive, leo.licence_country, leo.role) == (23, True, "AU", "member")   # 没写的不变
    event = service.activity(TRIP, 1)[0]
    assert (event.actor, event.kind, event.text) == ("m_leo", "profile", "Leo 更新了自己的资料（年龄），生成第 2 版")
    assert service.patch(v.patch_id).status == "applied" and service.patch(v.patch_id).author == "m_leo"
    assert service.pending_patches(TRIP) == []   # 不用再确认


def test_too_young_to_rent_is_an_error_but_still_saved(service):
    v, new = service.update_member_profile(TRIP, "m_tony", MemberProfile(age=20), today=TODAY)
    assert v is not None and service.current(TRIP).member("m_tony").age == 20
    assert r11(new) == ["Tony 20 岁，低于租车最低年龄 21 岁"]


def test_licence_translation_todo_follows_profile(service):
    v, _ = service.update_member_profile(TRIP, "m_leo", MemberProfile(licence_country="CN"), today=TODAY)
    t = todo(service, "todo_licence_leo")
    assert (t.kind, t.owner_ids, t.title, t.due) == ("licence_translation", ["m_leo"], "Leo：办理驾照的认可英文翻译件",
                                                     date(2026, 10, 31))
    assert "新增待办：Leo：办理驾照的认可英文翻译件" in v.changelog
    assert not any("翻译件" in m for m in r11(service.validate(TRIP)))   # 待办跟着加上了，R11 不报缺
    # 换回澳洲驾照：没完成的翻译件待办去掉
    v, _ = service.update_member_profile(TRIP, "m_leo", MemberProfile(licence_country="AU"), today=TODAY)
    assert todo(service, "todo_licence_leo") is None
    assert "删除待办：Leo：办理驾照的认可英文翻译件" in v.changelog


def test_finished_licence_todo_is_kept(store, trip, places, drive_times):
    trip.todos = [t.model_copy(update={"status": "done"}) if t.id == "todo_licence_tony" else t for t in trip.todos]
    s = TripService(store, places, drive_times)
    s.create_trip(trip)
    s.update_member_profile(TRIP, "m_tony", MemberProfile(licence_country="AU"), today=TODAY)
    assert todo(s, "todo_licence_tony").status == "done"   # 办完的留着，不删历史


def test_driver_who_stops_driving_gets_an_error_and_loses_the_todo(service):
    v, new = service.update_member_profile(
        TRIP, "m_tony", MemberProfile(can_drive=False, age=None, licence_country=None, licence_type=None), today=TODAY)
    assert v is not None   # 资料是事实：照样保存，由猪猪再调整行程
    assert "Tony 被列为司机，但没有登记为可以开车" in r11(new)
    assert todo(service, "todo_licence_tony") is None
    assert "删除待办：办理中国驾照的认可英文翻译件" in v.changelog


def test_nzeta_owners_follow_passport(store, trip, places, drive_times):
    everyone = [m.id for m in trip.members]
    trip.todos = [t.model_copy(update={"owner_ids": everyone}) if t.id in NZETA_TODOS else t for t in trip.todos]
    s = TripService(store, places, drive_times)
    s.create_trip(trip)
    s.update_member_profile(TRIP, "m_nina", MemberProfile(passport_country="AU"), today=TODAY)
    assert todo(s, "todo_nzeta").owner_ids == todo(s, "todo_visa_check").owner_ids == ["m_leo", "m_tony", "m_tiange"]
    v, _ = s.update_member_profile(TRIP, "m_nina", MemberProfile(passport_country="CN"), today=TODAY)
    assert todo(s, "todo_nzeta").owner_ids == everyone   # 按成员顺序加回来
    assert "修改成员「Nina」：护照 澳大利亚 → 中国" in v.changelog
    assert any(line.startswith("修改待办「四人分别申请 NZeTA") and "负责人" in line for line in v.changelog)
    # 没人需要了就去掉
    for mid in everyone:
        s.update_member_profile(TRIP, mid, MemberProfile(passport_country="NZ"), today=TODAY)
    assert todo(s, "todo_nzeta") is None and todo(s, "todo_visa_check") is None


def test_hand_written_todos_for_everyone_are_not_touched(service):
    service.update_member_profile(TRIP, "m_nina", MemberProfile(passport_country="AU"), today=TODAY)
    assert todo(service, "todo_nzeta").owner_ids == []   # 演示行程里没写负责人（全员），不动


def test_weight_change_does_not_show_the_number(service):
    v, _ = service.update_member_profile(TRIP, "m_tony", MemberProfile(weight_kg=61.5), today=TODAY)
    assert v.changelog == ["Tony 更新了自己的资料", "修改成员「Tony」：体重：已更新（只有本人能看到）"]
    assert service.activity(TRIP, 1)[0].text == "Tony 更新了自己的资料（体重），生成第 2 版"
    assert service.current(TRIP).member("m_tony").weight_kg == 61.5


def test_nothing_changed_writes_nothing(service):
    assert service.update_member_profile(TRIP, "m_leo", MemberProfile(age=26, visa=" 澳洲 500 学生签 ")) == (None, [])
    assert service.update_member_profile(TRIP, "m_tony", MemberProfile(can_drive=True)) == (None, [])
    assert service.latest(TRIP).version == 1


def test_profile_only_has_the_members_own_facts(service):
    for bad in ({"role": "owner"}, {"name": "天哥"}, {"id": "m_x"}, {"age": 0}, {"weight_kg": -1}, {"visa": "签" * 101}):
        with pytest.raises(ValidationError):
            MemberProfile.model_validate(bad)
    assert MemberProfile.model_validate({"visa": "  ", "licence_type": ""}).model_dump(exclude_unset=True) == \
        {"visa": None, "licence_type": None}
    with pytest.raises(ServiceError):
        service.update_member_profile(TRIP, "m_nobody", MemberProfile(age=30))


# ---------- 接口（本地开发：可以切换身份） ----------

def test_profile_over_api_in_open_mode(store, places, drive_times):
    service = TripService(store, places, drive_times)
    c = TestClient(create_app(service, llm_factory=lambda: ScriptedLLM([])))
    c.post("/api/demo")
    r = c.put(f"/api/trips/{TRIP}/members/m_tiange/profile",
              json={"can_drive": True, "age": 22, "licence_country": "CN", "licence_type": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] and body["version"] == 2
    assert "新增待办：天哥：办理驾照的认可英文翻译件" in body["changelog"]
    assert c.get(f"/api/trips/{TRIP}").json()["trip"]["members"][3]["can_drive"] is True
    assert c.get(f"/api/trips/{TRIP}/versions").json()[-1]["changelog"][0] == "天哥 更新了自己的资料"
    again = c.put(f"/api/trips/{TRIP}/members/m_tiange/profile", json={"age": 22}).json()
    assert again == {"changed": False, "version": 2, "changelog": [], "issues": []}
    assert c.put(f"/api/trips/{TRIP}/members/m_nobody/profile", json={"age": 22}).status_code == 400
    assert c.put(f"/api/trips/{TRIP}/members/m_tony/profile", json={"age": "老"}).status_code == 422


# ---------- 接口（上线：邀请链接登录） ----------

def members_of(client) -> dict[str, dict]:
    return {m["id"]: m for m in client.get(f"/api/trips/{TRIP}").json()["trip"]["members"]}


def test_members_can_only_edit_their_own_profile(site):
    links = site.links()
    tony, nina = site.member(links["m_tony"]), site.member(links["m_nina"])   # nina 是发起人
    url = f"/api/trips/{TRIP}/members/{{}}/profile"

    # 别人的资料谁都改不了，发起人也不行（路径各种写法都试一遍）
    assert nina.put(url.format("m_tony"), json={"age": 30}).status_code == 403
    r = tony.put(url.format("m_nina"), json={"passport_country": "AU"})
    assert r.status_code == 403 and r.json()["detail"] == "只能修改自己的资料。"
    assert tony.put(url.format("m%5Fnina"), json={"passport_country": "AU"}).status_code == 403
    assert tony.put(url.format("m_nobody"), json={"age": 30}).status_code == 403
    assert site.client().put(url.format("m_tony"), json={"age": 30}).status_code == 401   # 没登录
    # 称呼、角色不在资料里，改不了
    assert tony.put(url.format("m_tony"), json={"role": "owner"}).status_code == 422
    assert tony.put(url.format("m_tony"), json={"name": "天哥"}).status_code == 422
    assert tony.get(f"/api/trips/{TRIP}").json()["version"] == 1
    assert members_of(tony)["m_nina"]["passport_country"] == "CN" and members_of(tony)["m_tony"]["role"] == "member"

    # 自己的可以改：直接生成新版本，行程检查跟着变
    r = tony.put(url.format("m_tony"), json={"age": 21, "licence_type": "p"})
    assert r.status_code == 200
    body = r.json()
    assert body["changed"] and body["version"] == 2
    assert "修改成员「Tony」：年龄 24 → 21；驾照类型 正式驾照 → P 牌" in body["changelog"]
    assert [i["message"] for i in body["issues"] if i["code"] == "R11"] == ["Tony 21 岁，不满 25 岁，租车可能收年轻司机附加费"]
    detail = nina.get(f"/api/trips/{TRIP}").json()   # 别人打开行程也看到新资料和新提醒
    assert members_of(nina)["m_tony"]["age"] == 21
    assert "Tony 21 岁，不满 25 岁，租车可能收年轻司机附加费" in [i["message"] for i in detail["issues"]]


def test_weight_stays_private_after_editing(site):
    links = site.links()
    tony, nina = site.member(links["m_tony"]), site.member(links["m_nina"])
    assert tony.put(f"/api/trips/{TRIP}/members/m_tony/profile", json={"weight_kg": 61}).status_code == 200
    assert members_of(tony)["m_tony"]["weight_kg"] == 61
    assert "weight_kg" not in members_of(nina)["m_tony"]
    # 只看给人读的文字（时间戳里的数字可能碰巧有 61）
    seen = [line for v in nina.get(f"/api/trips/{TRIP}/versions").json() for line in v["changelog"]] + \
        [e["text"] for e in nina.get(f"/api/trips/{TRIP}/activity").json()]
    assert not any("61" in text for text in seen)
    assert "修改成员「Tony」：体重：已更新（只有本人能看到）" in seen
    assert "Tony 更新了自己的资料（体重），生成第 2 版" in seen
