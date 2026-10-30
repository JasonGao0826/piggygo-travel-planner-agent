"""上线用的登录（专属邀请链接）：不能冒充别人、不能看没加入的行程、内测邀请码、管理口令、每天的模型额度。"""

import json
import time

from fastapi.testclient import TestClient
from sqlalchemy import text

from tripagent.api import create_app
from tripagent.auth import COOKIE
from tripagent.llm.fake import ScriptStep

from .conftest import ADMIN

TRIP = "nz_2026"


def test_without_link_nothing_is_visible(site):
    c = site.client()
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/session").json() == {"mode": "invite", "trips": [], "needs_beta_code": True}
    assert c.post("/api/demo").status_code == 403                         # 演示行程只有管理员能载入
    assert c.post("/api/admin/demo", headers={"x-admin-token": "nope"}).status_code == 403
    assert c.post("/api/admin/demo", headers=ADMIN).json() == {"id": TRIP}
    assert c.get("/api/trips").json() == []
    r = c.get(f"/api/trips/{TRIP}")
    assert r.status_code == 401 and "邀请链接" in r.json()["detail"]
    assert c.get(f"/api/trips/{TRIP}/report.docx").status_code == 401
    assert c.get(f"/api/trips/{TRIP}/vouchers").status_code == 401
    c.cookies.set(COOKIE, "made-up-session")
    assert c.get(f"/api/trips/{TRIP}").status_code == 401
    assert c.post("/api/join", json={"token": "made-up-token"}).status_code == 403


def test_join_with_link_and_only_see_own_trip(site):
    links = site.links()
    tony = site.member(links["m_tony"])
    assert tony.cookies.get(COOKIE)
    session = tony.get("/api/session").json()
    assert session["trips"] == [{"trip_id": TRIP, "member_id": "m_tony", "name": session["trips"][0]["name"], "role": "member"}]
    assert [t["id"] for t in tony.get("/api/trips").json()] == [TRIP]
    assert tony.get(f"/api/trips/{TRIP}").status_code == 200
    r = tony.get("/api/trips/someone_elses_trip")
    assert r.status_code == 403
    assert tony.get("/api/trips/someone%5Felses%5Ftrip").status_code == 403
    # 链接只存哈希，数据库里找不到明文
    with site.engine.connect() as conn:
        dump = json.dumps([list(map(str, row)) for row in conn.execute(text("select * from invites")).all()])
    assert links["m_tony"] not in dump


def test_cannot_act_as_someone_else(site):
    links = site.links()
    tony, nina = site.member(links["m_tony"]), site.member(links["m_nina"])     # nina 是发起人

    # 偏好：只能改自己的
    assert tony.put(f"/api/trips/{TRIP}/preferences/m_nina", json={"pace": "packed"}).status_code == 403
    assert tony.put(f"/api/trips/{TRIP}/preferences/m_tony", json={"pace": "packed"}).status_code == 200

    # 只有发起人能做意见汇总：Tony 在请求里写成 nina 也没用（各种写法都试一遍）
    url = f"/api/trips/{TRIP}/summary"
    claim = json.dumps({"member_id": "m_nina"})
    assert tony.post(url, json={"member_id": "m_nina"}).status_code == 403
    assert tony.post(url, content=claim, headers={"content-type": "Application/JSON"}).status_code == 403
    assert tony.post(url, content=claim).status_code in (403, 422)                                  # 不带 Content-Type
    assert tony.post(url, content=claim, headers={"content-type": "text/plain"}).status_code in (403, 422)

    # 查询参数里的 member_id 也以登录身份为准
    assert tony.get(f"/api/trips/{TRIP}/invites", params={"member_id": "m_nina"}).status_code == 403
    assert nina.get(f"/api/trips/{TRIP}/invites", params={"member_id": "m_tony"}).status_code == 200
    assert nina.get(f"/api/trips/{TRIP}/invites").status_code == 200

    # 表单（上传凭证）：写别人的名字也记在自己名下
    v = tony.post(f"/api/trips/{TRIP}/vouchers", data={"member_id": "m_nina", "title": "冲浪"},
                files={"file": ("surf.pdf", b"%PDF-1.4 surf", "application/pdf")}).json()
    assert v["uploaded_by"] == "m_tony"
    # 普通成员不能删别人传的凭证（带着别人的名字也不行）
    other = nina.post(f"/api/trips/{TRIP}/vouchers", data={"title": "住宿"},
                       files={"file": ("stay.pdf", b"%PDF-1.4 stay", "application/pdf")}).json()
    assert other["uploaded_by"] == "m_nina"
    assert tony.delete(f"/api/trips/{TRIP}/vouchers/{other['id']}", params={"member_id": "m_nina"}).status_code == 400
    assert [x["id"] for x in tony.get(f"/api/trips/{TRIP}/vouchers").json()] == [v["id"], other["id"]]


def test_regenerating_a_link_logs_out_the_old_one(site):
    links = site.links()
    tony, nina = site.member(links["m_tony"]), site.member(links["m_nina"])
    assert tony.post(f"/api/trips/{TRIP}/invites/m_leo", params={"member_id": "m_nina"}).status_code == 403

    new = nina.post(f"/api/trips/{TRIP}/invites/m_tony").json()["token"]
    assert tony.get(f"/api/trips/{TRIP}").status_code == 401                 # 旧设备退出
    assert site.client().post("/api/join", json={"token": links["m_tony"]}).status_code == 403   # 旧链接作废
    assert site.member(new).get(f"/api/trips/{TRIP}").status_code == 200
    status = {row["member_id"]: row["has_link"] for row in nina.get(f"/api/trips/{TRIP}/invites").json()}
    assert status["m_tony"] and status["m_nina"]

    # 发起人给自己换链接：当前这台设备不用重新点
    nina.post(f"/api/trips/{TRIP}/invites/m_nina")
    assert nina.get(f"/api/trips/{TRIP}").status_code == 200

    # 管理员重新生成全部链接：所有人都要用新链接
    site.links()
    assert nina.get(f"/api/trips/{TRIP}").status_code == 401


def test_new_trip_needs_beta_code_and_creator_becomes_owner(site):
    from .test_planner import INTAKE, PLANS, SKELETON
    site.steps[:] = [ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(PLANS))]
    site.build()
    c = site.client()
    assert c.post("/api/trips", json=INTAKE).status_code == 403
    assert c.post("/api/trips", json=INTAKE, headers={"x-beta-code": "wrong"}).status_code == 403
    job_id = c.post("/api/trips", json=INTAKE, headers={"x-beta-code": " piggy-beta "}).json()["job_id"]
    assert c.cookies.get(COOKIE)

    stranger = site.client()
    assert stranger.get(f"/api/jobs/{job_id}").status_code == 404           # 别人看不到这个任务
    assert stranger.post(f"/api/jobs/{job_id}/answer", json={"text": "确认"}).status_code == 404

    def wait_for(status):
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] == status:
                return job
            assert job["status"] != "failed", job
            time.sleep(0.05)
        raise AssertionError(f"任务没有进入 {status}：{job}")

    wait_for("needs_input")
    c.post(f"/api/jobs/{job_id}/answer", json={"text": "确认"})
    trip_id = wait_for("done")["trip_id"]
    [mine] = c.get("/api/session").json()["trips"]
    assert mine["trip_id"] == trip_id and mine["role"] == "owner"
    assert c.get(f"/api/trips/{trip_id}").status_code == 200
    assert stranger.get(f"/api/trips/{trip_id}").status_code == 401
    links = c.get(f"/api/trips/{trip_id}/invites").json()
    assert len(links) == len(INTAKE["members"]) and not any(row["has_link"] for row in links)


def test_daily_model_quota_per_trip(site):
    site.steps[:] = [ScriptStep(text="好的")] * 3
    site.build(daily_llm_limit=2)
    links = site.links()
    tony = site.member(links["m_tony"])
    chat = f"/api/trips/{TRIP}/chat"
    assert tony.post(chat, json={"member_id": "m_tony", "text": "你好"}).status_code == 200
    assert tony.post(chat, json={"member_id": "m_tony", "text": "在吗"}).status_code == 200
    r = tony.post(chat, json={"member_id": "m_tony", "text": "还在吗"})
    assert r.status_code == 429 and "每天 2 次" in r.json()["detail"]
    assert tony.get(f"/api/trips/{TRIP}").status_code == 200                # 不调用模型的请求不受影响


# ---------------- 安全审查发现的问题（每条一个测试） ----------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4 ticket"


def test_web_pages_cannot_be_stored_as_vouchers(site):
    """网页文件（.html、.svg）伪装成凭证：存不进去；凭证原文件只有图片和 PDF 能直接在浏览器里打开。"""
    tony = site.member(site.links()["m_tony"])
    url = f"/api/trips/{TRIP}/vouchers"
    for name, data, mime in [("receipt.html", b"<script>alert(1)</script>", "text/html"),
                             ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                              "image/svg+xml"),
                             ("page.xhtml", b"<html><script>alert(1)</script></html>", "application/xhtml+xml"),
                             ("fake.pdf", b"<script>alert(1)</script>", "application/pdf")]:
        r = tony.post(url, files={"file": (name, data, mime)})
        assert r.status_code == 400 and "不支持" in r.json()["detail"], name
    # 识别接口：类型不对直接拒绝，不留文件
    r = tony.post(f"/api/trips/{TRIP}/evidence", files={"file": ("x.html", b"<script>1</script>", "text/html")})
    assert r.status_code == 415
    assert not any((site.uploads / TRIP).glob("*.html"))

    # 图片（哪怕文件名是 .html）按内容认成 PNG：可以直接打开，但带 nosniff 和沙箱
    img = tony.post(url, files={"file": ("photo.html", PNG, "text/html")}).json()
    r = tony.get(f"{url}/{img['id']}/file")
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-security-policy"] == "sandbox"
    pdf = tony.post(url, files={"file": ("ticket.pdf", PDF, "application/pdf")}).json()
    r = tony.get(f"{url}/{pdf['id']}/file")
    assert r.headers["content-type"] == "application/pdf" and r.headers["content-disposition"].startswith("inline")
    r = tony.get(f"{url}/{pdf['id']}/file", params={"download": 1})
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")

    # 磁盘上的文件不管记录里写的是什么类型，都按内容判断：不是图片或 PDF 就只能下载
    stored = next(e for e in site.service.evidence(TRIP) if e.id == img["id"]).stored_as
    (site.uploads / stored).write_bytes(b"<script>alert(1)</script>")
    r = tony.get(f"{url}/{img['id']}/file")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")


def test_cannot_reject_another_trips_patch(site):
    links = site.links()
    other = site.service.current(TRIP).model_copy(deep=True)
    other.meta.id = "other_trip"
    site.service.create_trip(other, [])
    secret = site.service.propose("other_trip", [{"op": "update", "collection": "items", "id": "d1_lunch",
                                                   "set": {"title": "别人行程的秘密汉堡店"}}], "别人的改动")
    tony = site.member(links["m_tony"])
    r = tony.post(f"/api/trips/{TRIP}/patches/{secret.id}/reject")
    assert r.status_code == 400 and "秘密" not in r.text
    assert [p.id for p in site.service.pending_patches("other_trip")] == [secret.id]


def test_new_trips_closed_without_beta_code(site):
    from .test_planner import INTAKE
    site.build(beta_code=None)
    c = site.client()
    assert c.get("/api/session").json()["needs_beta_code"] is False
    r = c.post("/api/trips", json=INTAKE, headers={"x-beta-code": ""})
    assert r.status_code == 403 and "暂未开放" in r.json()["detail"]


def test_trip_generation_limits(site, monkeypatch):
    from .test_planner import INTAKE, PLANS, SKELETON
    import tripagent.api.app as app_module
    monkeypatch.setattr(app_module, "MAX_UNFINISHED_JOBS", 2)
    monkeypatch.setattr(app_module, "JOB_KEEP_S", 0)
    site.steps[:] = [ScriptStep(text=json.dumps(SKELETON)), ScriptStep(text=json.dumps(SKELETON)),
                     ScriptStep(text=json.dumps(PLANS)), ScriptStep(text=json.dumps(PLANS))]
    site.build(daily_trip_limit=2)
    code = {"x-beta-code": "piggy-beta"}

    def wait_for(c, job_id, status):
        for _ in range(100):
            job = c.get(f"/api/jobs/{job_id}").json()
            if job["status"] == status:
                return job
            assert job["status"] != "failed", job
            time.sleep(0.05)
        raise AssertionError(job)

    a, b, c = site.client(), site.client(), site.client()
    job_a = a.post("/api/trips", json=INTAKE, headers=code).json()["job_id"]
    wait_for(a, job_a, "needs_input")
    r = a.post("/api/trips", json=INTAKE, headers=code)                   # 同一个人：等上一个生成完
    assert r.status_code == 429 and "正在生成" in r.json()["detail"]
    job_b = b.post("/api/trips", json=INTAKE, headers=code).json()["job_id"]
    wait_for(b, job_b, "needs_input")
    r = c.post("/api/trips", json=INTAKE, headers=code)                   # 同时生成的太多
    assert r.status_code == 429 and "太多" in r.json()["detail"]

    a.post(f"/api/jobs/{job_a}/answer", json={"text": "确认"})
    wait_for(a, job_a, "done")
    b.post(f"/api/jobs/{job_b}/answer", json={"text": "确认"})
    wait_for(b, job_b, "done")
    r = c.post("/api/trips", json=INTAKE, headers=code)                   # 今天的名额（2 个）用完了
    assert r.status_code == 429 and "名额" in r.json()["detail"]
    assert a.get(f"/api/jobs/{job_a}").status_code == 404                 # 生成完的任务已经清掉


def test_errors_do_not_leak_details_after_launch(site):
    def broken():
        raise RuntimeError("OPENAI_API_KEY sk-live-secret 在 C:/secret/path")
    site.app = create_app(site.service, llm_factory=broken, uploads_dir=site.uploads, auth=site.auth,
                          beta_code="piggy-beta", admin_token="admin-secret")
    links = site.links()
    tony = TestClient(site.app, raise_server_exceptions=False)
    tony.post("/api/join", json={"token": links["m_tony"]})
    r = tony.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_tony", "text": "你好"})
    assert r.status_code == 500 and "错误编号" in r.json()["detail"]
    assert "sk-live" not in r.text and "secret" not in r.text and "RuntimeError" not in r.text


def test_join_always_issues_a_new_session(site):
    links = site.links()
    attacker = site.member(links["m_leo"])
    planted = attacker.cookies.get(COOKIE)
    victim = site.client()
    r = victim.post("/api/join", json={"token": links["m_tony"]}, headers={"cookie": f"{COOKIE}={planted}"})  # 别人事先塞进浏览器的会话
    assert r.status_code == 200 and r.cookies.get(COOKIE) not in (None, planted)
    assert attacker.get(f"/api/trips/{TRIP}").status_code == 401         # 塞进去的会话作废了，拿不到 Tony 的身份
    assert {t["member_id"] for t in victim.get("/api/session").json()["trips"]} == {"m_tony"}


def test_join_check_warns_before_switching_identity(site):
    links = site.links()
    tony = site.member(links["m_tony"])
    check = tony.post("/api/join/check", json={"token": links["m_leo"]}).json()
    assert check["member_id"] == "m_leo" and check["current_member_id"] == "m_tony" and check["current_name"] == "Tony"
    assert tony.get("/api/session").json()["trips"][0]["member_id"] == "m_tony"   # 只是看看，没有切换
    fresh = site.client().post("/api/join/check", json={"token": links["m_leo"]}).json()
    assert fresh["current_member_id"] is None
    assert site.client().post("/api/join/check", json={"token": "nope"}).status_code == 403


def test_quota_not_used_by_rejected_requests(site):
    site.steps[:] = [ScriptStep(text="好的")] * 3
    site.build(daily_llm_limit=2)
    tony = site.member(site.links()["m_tony"])
    for _ in range(3):   # 不是发起人，汇总被拒：不算次数
        assert tony.post(f"/api/trips/{TRIP}/summary", json={"member_id": "m_tony"}).status_code == 403
    assert tony.post(f"/api/trips/{TRIP}/chat", json={"wrong": "body"}).status_code == 422
    for text in ("你好", "在吗"):
        assert tony.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_tony", "text": text}).status_code == 200
    assert tony.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_tony", "text": "还在吗"}).status_code == 429


def test_large_json_body_is_rejected(site):
    tony = site.member(site.links()["m_tony"])
    r = tony.post(f"/api/trips/{TRIP}/chat", json={"member_id": "m_tony", "text": "猪" * 400_000})
    assert r.status_code == 413


def test_delete_uses_logged_in_member(site):
    tony = site.member(site.links()["m_tony"])
    v = tony.post(f"/api/trips/{TRIP}/vouchers", files={"file": ("t.pdf", PDF, "application/pdf")}).json()
    assert tony.delete(f"/api/trips/{TRIP}/vouchers/{v['id']}").status_code == 200   # 不传 member_id 也行
    assert tony.get(f"/api/trips/{TRIP}/messages").status_code == 200


def test_other_members_weight_is_hidden(site):
    from tripagent.io import load_decisions, load_trip
    from tripagent.paths import KB_DATA
    trip = load_trip(KB_DATA / "demo" / "nz_trip_v8.yaml")
    for m in trip.members:
        m.weight_kg = 60
    site.service.create_trip(trip, load_decisions(KB_DATA / "demo" / "nz_decisions.yaml"))
    tony = site.member(site.links()["m_tony"])
    members = {m["id"]: m for m in tony.get(f"/api/trips/{TRIP}").json()["trip"]["members"]}
    assert members["m_tony"]["weight_kg"] == 60
    assert all("weight_kg" not in m for mid, m in members.items() if mid != "m_tony")


def test_security_headers(site):
    r = site.client().get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    r = site.client().get(f"/api/trips/{TRIP}")
    assert r.status_code == 401 and r.headers["x-frame-options"] == "DENY"
