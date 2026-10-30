"""凭证夹：存档、查看、删除、打包下载，以及报告里的凭证清单。不联网。"""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from tripagent.api import create_app
from tripagent.llm.fake import ScriptedLLM
from tripagent.report import build_report
from tripagent.service import ServiceError, TripService
from tripagent.vault import VoucherService

TRIP = "nz_2026"
PDF = b"%PDF-1.4 skyline ticket"


@pytest.fixture
def vault(store, trip, places, drive_times, tmp_path):
    service = TripService(store, places, drive_times)
    service.create_trip(trip)
    return VoucherService(service, tmp_path)


def test_store_list_and_link(vault, tmp_path):
    ev = vault.store_file(TRIP, "m_leo", "11.23 缆车+滑板车入场票.pdf", PDF, title="Skyline 缆车＋斜坡滑车",
                          kind="activity", item_ids=["d1_skyline"])
    assert ev.id == "ev_001" and ev.mime == "application/pdf" and ev.size == len(PDF)
    assert ev.date.isoformat() == "2026-11-23"                       # 没填日期时取关联安排的日期
    assert (tmp_path / ev.stored_as).read_bytes() == PDF
    [view] = vault.views(TRIP)
    assert view["items"] == ["Skyline 天空缆车＋Luge"] and view["uploaded_by_name"] == "Leo"
    assert view["kind_label"] == "活动" and view["has_file"]
    assert vault.service.activity(TRIP)[0].text == "Leo 上传了活动凭证：Skyline 缆车＋斜坡滑车"
    with pytest.raises(ServiceError, match="没有这些安排"):
        vault.store_file(TRIP, "m_leo", "x.pdf", PDF, item_ids=["no_such_item"])


def test_items_that_reference_voucher_are_listed(vault):
    cruise = next(i for i in vault.service.current(TRIP).all_items() if i.evidence_ids)
    ev = vault.store_file(TRIP, "m_nina", "c.pdf", PDF)
    vault.store.delete_evidence(TRIP, ev.id)
    vault.store.save_evidence(TRIP, ev.model_copy(update={"id": cruise.evidence_ids[0]}))
    assert cruise.title in vault.linked_items(TRIP)[cruise.evidence_ids[0]]


def test_delete_permissions(vault, tmp_path):
    ev = vault.store_file(TRIP, "m_tony", "a.pdf", PDF)
    with pytest.raises(ServiceError, match="只有上传的人"):
        vault.delete(TRIP, ev.id, "m_leo")
    vault.delete(TRIP, ev.id, "m_nina")                              # 发起人可以删
    assert vault.views(TRIP) == [] and not (tmp_path / ev.stored_as).exists()
    ev2 = vault.store_file(TRIP, "m_tony", "b.pdf", PDF)
    vault.delete(TRIP, ev2.id, "m_tony")                                # 上传的人自己可以删
    assert vault.service.activity(TRIP)[0].text.startswith("Tony 删除了凭证")


def test_path_outside_uploads_is_refused(vault):
    ev = vault.store_file(TRIP, "m_tony", "a.pdf", PDF)
    vault.store.save_evidence(TRIP, ev.model_copy(update={"stored_as": "../../secret.txt"}))
    with pytest.raises(ServiceError, match="不见了"):
        vault.path(TRIP, ev.id)


def test_bundle_names_files_by_date(vault, tmp_path):
    vault.store_file(TRIP, "m_tony", "ticket.pdf", PDF, title="Skyline 缆车", item_ids=["d1_skyline"])
    vault.store_file(TRIP, "m_tony", "ticket2.PDF", PDF, title="Skyline 缆车", item_ids=["d1_skyline"])
    vault.store_file(TRIP, "m_tony", "notes.png", b"\x89PNG\r\n\x1a\n...", title="地热公园: 电子票/二维码")
    target = vault.bundle(TRIP, {"行程.docx": b"docx", "行程.ics": b"ics"}, tmp_path / "bundle.zip")
    names = zipfile.ZipFile(target).namelist()
    assert names == ["行程.docx", "行程.ics", "凭证/11-23 Skyline 缆车.pdf", "凭证/11-23 Skyline 缆车 (2).pdf",
                     "凭证/地热公园 电子票 二维码.png"]


def test_report_lists_vouchers(vault, places):
    vault.store_file(TRIP, "m_leo", "ticket.pdf", PDF, title="Skyline 缆车", kind="activity", item_ids=["d1_skyline"])
    report = build_report(vault.service.current(TRIP), places, vouchers=vault.views(TRIP))
    section = next(s for s in report.sections if s.title.endswith("预订凭证"))
    row = section.blocks[1].rows[0].cells
    assert row[:4] == ["11/23 周一", "Skyline 缆车", "活动", "Skyline 天空缆车＋Luge"] and row[5:] == ["Leo", "ticket.pdf"]
    assert not any(s.title.endswith("预订凭证") for s in build_report(vault.service.current(TRIP), places).sections)


def test_voucher_api_and_bundle(store, places, drive_times, tmp_path):
    service = TripService(store, places, drive_times)
    c = TestClient(create_app(service, llm_factory=lambda: ScriptedLLM([]), uploads_dir=tmp_path))
    c.post("/api/demo")
    r = c.post(f"/api/trips/{TRIP}/vouchers", data={"member_id": "m_tony", "title": "地热公园门票", "kind": "activity",
                                                    "date": "2026-11-30", "notes": "入口出示二维码"},
               files={"file": ("wai-o-tapu.pdf", PDF, "application/pdf")})
    assert r.status_code == 200 and r.json()["title"] == "地热公园门票" and r.json()["date"] == "2026-11-30"
    ev_id = r.json()["id"]
    f = c.get(f"/api/trips/{TRIP}/vouchers/{ev_id}/file")
    assert f.content == PDF and f.headers["content-type"] == "application/pdf" and "inline" in f.headers["content-disposition"]
    assert "attachment" in c.get(f"/api/trips/{TRIP}/vouchers/{ev_id}/file", params={"download": 1}).headers["content-disposition"]
    assert c.post(f"/api/trips/{TRIP}/vouchers", data={"member_id": "m_tony", "date": "30/11"},
                  files={"file": ("x.pdf", PDF, "application/pdf")}).status_code == 422

    z = c.get(f"/api/trips/{TRIP}/bundle.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert any(n.endswith(".docx") for n in names) and any(n.endswith(".ics") for n in names)
    assert "凭证/11-30 地热公园门票.pdf" in names

    assert c.delete(f"/api/trips/{TRIP}/vouchers/{ev_id}", params={"member_id": "m_leo"}).status_code == 400
    assert c.delete(f"/api/trips/{TRIP}/vouchers/{ev_id}", params={"member_id": "m_tony"}).json() == {"ok": True}
    assert c.get(f"/api/trips/{TRIP}/vouchers").json() == []
