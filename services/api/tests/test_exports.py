"""日历导出、邮件凭证、实时情况接口、后台刷新。不连外网。"""

from datetime import date, datetime, timezone
from email.message import EmailMessage

from fastapi.testclient import TestClient

from tripagent.api import create_app
from tripagent.calendar import trip_ics
from tripagent.evidence import extract_voucher, read_email
from tripagent.live.models import DayForecast
from tripagent.live.monitor import LiveMonitor
from tripagent.live.service import LiveService
from tripagent.llm.fake import ScriptedLLM, ScriptStep
from tripagent.service import TripService
from tripagent.store import InMemoryStore

TRIP = "nz_2026"
PDF = b"%PDF-1.4 fake voucher"


# ---------------- 日历 ----------------

def unfold(ics: str) -> list[str]:
    return ics.replace("\r\n ", "").split("\r\n")


def test_calendar_export(trip, places):
    ics = trip_ics(trip, places, now=datetime(2026, 10, 23, tzinfo=timezone.utc))
    raw = ics.split("\r\n")
    assert raw[0] == "BEGIN:VCALENDAR" and ics.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode("utf-8")) <= 75 for line in raw)
    lines = unfold(ics)
    n_events = sum(1 for x in lines if x == "BEGIN:VEVENT")
    expected = sum(1 for i in trip.all_items() if i.type.value not in ("free_time", "buffer"))
    assert n_events == expected
    # QF177 墨尔本 06:40（+11:00）起飞 = UTC 前一天 19:40；提前 180 分钟到场，再提前 60 分钟提醒
    i = lines.index("UID:d1_flight_qf177@nz_2026.tripagent")
    event = lines[i:lines.index("END:VEVENT", i)]
    assert "DTSTART:20261122T194000Z" in event and "TRIGGER:-PT240M" in event
    assert any(x.startswith("DESCRIPTION:需要提前 180 分钟到场") for x in event)
    assert any(x.startswith("SUMMARY:QF177 墨尔本 → 皇后镇") for x in event)


# ---------------- 邮件凭证 ----------------

def email_bytes(html: str | None = None, text: str | None = None, attach: bytes | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["Subject"] = "RealNZ <bookings@realnz.example>", "Booking confirmation RNZ123"
    if text:
        msg.set_content(text)
    if html:
        if text:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    if attach:
        msg.add_attachment(attach, maintype="application", subtype="pdf", filename="voucher.pdf")
    return msg.as_bytes()


def test_read_email_html_and_attachment():
    data = email_bytes(html="<html><style>.x{}</style><p>Milford Sound Cruise</p><p>Date: 25 Nov 2026&nbsp;13:15</p>"
                            "<script>alert(1)</script></html>", attach=PDF)
    body, atts = read_email(data)
    assert "主题：Booking confirmation RNZ123" in body
    assert "Milford Sound Cruise\nDate: 25 Nov 2026 13:15" in body and "alert" not in body and ".x{}" not in body
    assert [(a.mime, a.filename, a.data) for a in atts] == [("application/pdf", "voucher.pdf", PDF)]


def test_extract_voucher_from_email():
    llm = ScriptedLLM([ScriptStep(text='{"bookings": [], "notes": [], "unreadable": true}')])
    seen = []
    original = llm.extract
    llm.extract = lambda system, text, attachments, schema, **kw: (seen.append((text, attachments)),
                                                                   original(system, text, attachments, schema))[1]
    extract_voucher(llm, email_bytes(text="Cruise 25 Nov 13:15", attach=PDF), "confirm.eml", trip_hint="新西兰")
    text, atts = seen[0]
    assert "<邮件正文>" in text and "Cruise 25 Nov 13:15" in text and "行程背景：新西兰" in text
    assert [a.filename for a in atts] == ["voucher.pdf"]


# ---------------- 接口 ----------------

def client_with_live(places, drive_times, today=date(2026, 11, 20)):
    def forecast(points, start, end, tz):
        return [DayForecast(lat=p[0], lon=p[1], date=start, code=95, precip_mm=30, precip_prob=90, gust_kmh=80)
                for p in points]
    live = LiveService(today=lambda tz: today,
                       fetchers={"roads": lambda: [], "alerts": lambda: [], "forecast": forecast})
    service = TripService(InMemoryStore(), places, drive_times, live=live)
    c = TestClient(create_app(service, llm_factory=lambda: ScriptedLLM([])))
    c.post("/api/demo")
    return c, service


def test_live_and_calendar_endpoints(places, drive_times):
    c, _ = client_with_live(places, drive_times)
    live = c.get(f"/api/trips/{TRIP}/live").json()
    assert live["enabled"] and set(live["fetched_at"]) == {"roads", "alerts", "forecast"}
    assert live["errors"] == [] and isinstance(live["issues"], list)
    # 演示行程的地点有坐标的那天会查到预报
    assert all(f["weather"] == "雷雨" and f["date"] == "2026-11-23" for f in live["forecasts"])
    [day] = live["days"]                                     # 同一天的多个地点合成一行
    assert day["date"] == "2026-11-23" and day["gust_kmh"] == 80 and len(day["places"]) >= len(live["forecasts"])
    r = c.get(f"/api/trips/{TRIP}/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    assert r.text.startswith("BEGIN:VCALENDAR") and "filename*=UTF-8''" in r.headers["content-disposition"]


def test_live_endpoint_without_live_service(places, drive_times, store):
    c = TestClient(create_app(TripService(store, places, drive_times), llm_factory=lambda: ScriptedLLM([])))
    c.post("/api/demo")
    assert c.get(f"/api/trips/{TRIP}/live").json()["enabled"] is False


def test_monitor_refreshes_only_near_trips(places, drive_times):
    _, service = client_with_live(places, drive_times)
    monitor = LiveMonitor(service)
    assert monitor.run_once(date(2026, 10, 23)) == []        # 还有一个月，不刷新
    assert monitor.run_once(date(2026, 11, 15)) == [TRIP]    # 出发前 8 天
    assert monitor.run_once(date(2026, 12, 3)) == []         # 已经结束
