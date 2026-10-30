"""后端接口测试：用内存存储和剧本模型，不联网。"""

import pytest
from fastapi.testclient import TestClient

from tripagent.api import create_app
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.service import TripService

from .conftest import nz

TRIP = "nz_2026"


@pytest.fixture
def make_client(places, drive_times, store):
    def _make(steps=()):
        service = TripService(store, places, drive_times)
        llm = ScriptedLLM(list(steps))
        client = TestClient(create_app(service, llm_factory=lambda: llm))
        assert client.post("/api/demo").json() == {"id": TRIP}
        return client
    return _make


def test_demo_trip_and_report(make_client):
    c = make_client()
    trips = c.get("/api/trips").json()
    assert trips[0]["id"] == TRIP and trips[0]["version"] == 1 and len(trips[0]["members"]) == 4
    state = c.get(f"/api/trips/{TRIP}").json()
    assert len(state["trip"]["days"]) == 10 and state["pending"] == []
    assert {i["code"] for i in state["issues"]} == {"R04", "R05", "R11", "R25", "R26"}
    report = c.get(f"/api/trips/{TRIP}/report").json()
    assert report["sections"][1]["title"] == "2. 机票"
    assert report["sections"][1]["blocks"][0]["kind"] == "table"
    assert report["legend"][0] == {"city": "皇后镇", "color": "#DCEBFA"}


def test_docx_download(make_client):
    r = make_client().get(f"/api/trips/{TRIP}/report.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"  # docx 是 zip


def test_chat_propose_then_confirm(make_client):
    c = make_client([
        ScriptStep("propose_patch", {"summary": "午餐改名", "ops": [
            {"op": "update", "collection": "items", "id": "d1_lunch", "set": {"title": "皇后镇汉堡午餐"}}]}),
        ScriptStep(text="已提出改动，请确认。"),
    ])
    out = c.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_nina", "text": "午餐吃汉堡"}).json()
    assert out["reply"] == "已提出改动，请确认。"
    patch = out["patches"][0]
    assert patch["can_confirm"] and patch["changes"] == ["修改事件「午餐」：名称 午餐 → 皇后镇汉堡午餐"]
    assert [p["id"] for p in c.get(f"/api/trips/{TRIP}/patches").json()] == [patch["id"]]

    done = c.post(f"/api/trips/{TRIP}/patches/{patch['id']}/confirm", json={"member_id": "m_nina"}).json()
    assert done["version"] == 2
    assert c.get(f"/api/trips/{TRIP}/patches").json() == []
    assert [v["version"] for v in c.get(f"/api/trips/{TRIP}/versions").json()] == [1, 2]
    messages = c.get(f"/api/trips/{TRIP}/messages", params={"member_id": "m_nina"}).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]


def test_cannot_confirm_patch_with_errors(make_client):
    c = make_client([
        ScriptStep("propose_patch", {"summary": "打枪改到下午", "ops": [
            {"op": "update", "collection": "items", "id": "d4_real_guns", "set": {
                "start": nz("2026-11-26", "14:00").isoformat(), "end": nz("2026-11-26", "15:30").isoformat()}}]}),
        ScriptStep(text="已锁定，不能改。"),
    ])
    patch = c.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_leo", "text": "打枪改下午"}).json()["patches"][0]
    assert not patch["can_confirm"] and patch["issues"][0]["code"] == "R08"
    r = c.post(f"/api/trips/{TRIP}/patches/{patch['id']}/confirm", json={"member_id": "m_leo"})
    assert r.status_code == 400 and "错误" in r.json()["detail"]
    assert c.post(f"/api/trips/{TRIP}/patches/{patch['id']}/reject").json()["status"] == "rejected"


def test_unknown_trip_is_404(make_client):
    assert make_client().get("/api/trips/nope").status_code == 404


# ---------------- 凭证 ----------------

EXTRACTION = {"bookings": [{"kind": "activity", "title": "RealNZ Milford Sound Classic Cruise", "date": "2026-11-25",
                            "start_time": "13:15", "checkin_time": "12:55", "pax": 4, "confirmation_no": "RNZ-TEST-48213",
                            "quotes": ["Departs:  1:15 pm"]}], "notes": [], "unreadable": False}


def test_upload_voucher_then_apply(places, drive_times, store, tmp_path):
    import json
    from pathlib import Path
    from tripagent.api import create_app
    service = TripService(store, places, drive_times)
    llm = ScriptedLLM([
        ScriptStep(text=json.dumps(EXTRACTION)),                     # 识别
        ScriptStep("list_evidence", {}),                             # 助手看到凭证已经在凭证夹里
        ScriptStep(text="凭证和行程一致，已记录。"),
    ])
    c = TestClient(create_app(service, llm_factory=lambda: llm, uploads_dir=tmp_path))
    c.post("/api/demo")
    png = (Path(__file__).parent / "fixtures" / "vouchers" / "realnz_cruise.png").read_bytes()

    up = c.post(f"/api/trips/{TRIP}/evidence", data={"member_id": "m_nina"},
                files={"file": ("realnz_cruise.png", png, "image/png")}).json()
    assert up["extraction"]["bookings"][0]["start_time"] == "13:15"
    assert list((tmp_path / TRIP).glob(f"{up['upload_id']}_*"))  # 原件存下来了

    out = c.post(f"/api/trips/{TRIP}/evidence/{up['upload_id']}/apply",
                 json={"member_id": "m_nina", "extraction": up["extraction"]}).json()
    assert out["reply"] == "凭证和行程一致，已记录。"
    [ev] = service.evidence(TRIP)                                    # 存进凭证夹，没有重复记录
    assert ev.fields["bookings"][0]["confirmation_no"] == "RNZ-TEST-48213"
    assert ev.uploaded_by == "m_nina" and ev.filename == "realnz_cruise.png" and ev.mime == "image/png"
    assert ev.date.isoformat() == EXTRACTION["bookings"][0]["date"] and ev.kind == "activity"
    first_user_msg = service.conversation(TRIP, "m_nina")[0].text
    assert first_user_msg.startswith("【上传凭证】文件：realnz_cruise.png\n这份凭证已经存进凭证夹，编号 ev_001")
    vouchers = c.get(f"/api/trips/{TRIP}/vouchers").json()
    assert vouchers[0]["confirmation_nos"] == ["RNZ-TEST-48213"] and vouchers[0]["uploaded_by_name"] == "Nina"
    assert c.get(f"/api/trips/{TRIP}/vouchers/ev_001/file").content == png


def test_unsupported_file_type(make_client):
    c = make_client([ScriptStep(text="{}")])
    r = c.post(f"/api/trips/{TRIP}/evidence", data={"member_id": "m_nina"},
               files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 415 and "不支持" in r.json()["detail"]


# ---------------- 从信息清单生成 ----------------

def test_generate_trip_job_with_skeleton_confirmation(places, drive_times, store):
    import json
    import time
    from tripagent.api import create_app
    from .test_planner import INTAKE, PLANS, SKELETON
    service = TripService(store, places, drive_times)
    llm = ScriptedLLM([ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(PLANS))])
    c = TestClient(create_app(service, llm_factory=lambda: llm))

    job_id = c.post("/api/trips", json=INTAKE).json()["job_id"]

    def wait_for(status):
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] == status:
                return job
            assert job["status"] != "failed", job
            time.sleep(0.05)
        raise AssertionError(f"任务没有进入 {status}：{job}")

    job = wait_for("needs_input")
    assert job["skeleton"]["days"][0]["night_city"] == "皇后镇" and "确认" in job["questions"][-1]
    c.post(f"/api/jobs/{job_id}/answer", json={"text": "确认"})
    job = wait_for("done")
    trip = c.get(f"/api/trips/{job['trip_id']}").json()
    assert len(trip["trip"]["days"]) == 3 and not [i for i in trip["issues"] if i["severity"] == "error"]
    assert trip["decisions"][0]["requirement"]["activity_tag"] == "shark_boat"


def test_generate_job_reports_missing_info(make_client):
    from .test_planner import INTAKE
    c = make_client()
    r = c.post("/api/trips", json={**INTAKE, "start_date": None, "end_date": None})
    assert r.status_code == 422 and "日期" in r.json()["detail"]          # 缺了没法开始的信息：提交时就说


def test_confirm_does_not_need_the_model(places, drive_times, store):
    from tripagent.api import create_app

    def broken():
        raise RuntimeError("模型没配置")

    c = TestClient(create_app(TripService(store, places, drive_times), llm_factory=broken), raise_server_exceptions=False)
    c.post("/api/demo")
    r = c.post(f"/api/trips/{TRIP}/patches/p_nope/confirm", json={"member_id": "m_nina"})
    assert r.status_code == 400 and "没有等待确认的改动" in r.json()["detail"]
    r = c.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_nina", "text": "你好"},
               headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 500 and "模型没配置" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
