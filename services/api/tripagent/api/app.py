"""后端接口（FastAPI）。网页只调用这些接口，业务逻辑都在后端（设计规格 6.4 节）。

create_app 接收已经组装好的服务，测试时可以换成内存存储和剧本模型。
"""

import logging
import os
import re
import secrets
import tempfile
import threading
import time
from decimal import Decimal
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Annotated, Callable, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from ..agent import AgentReply, TripAgent
from ..auth import COOKIE, Auth, AuthError
from ..calendar import trip_ics
from ..checklist import missing_confirm_todos
from ..collab import CollabService
from ..collab.service import deadline_in
from ..evidence import STORABLE, VoucherExtraction, evidence_message, extract_voucher, guess_mime, save_upload
from ..guides import GuideError, GuideLimit, GuideNote, GuideService, fetch_note
from ..planner import NeedsInput, Planner
from ..planner.pipeline import intake_problems
from ..schema.collab import PollOptionDraft, PreferenceForm
from ..schema.intake import TripIntake
from ..schema.trip import MemberProfile
from ..io import load_decisions, load_trip
from ..live.report import live_report
from ..llm.base import LLM, MODEL_CALLS, Attachment, ModelBudgetExceeded
from ..mcp_gateway import MCPGateway
from ..report import build_report, render_docx
from ..report.model import Bullets, Card, Heading, Paragraph, Report, Table
from ..schema.memory import Patch
from ..service import ConflictError, ServiceError, TripService
from ..money import ExpenseIn, MoneyService, fmt, gateway_rates, members_on_voucher
from ..schema.money import Split
from ..planner.rules import member_ids
from ..state import apply_ops, describe
from ..paths import KB_DATA, OUTPUT, UPLOADS
from ..vault import VoucherService
from .security import Quota, SecurityMiddleware, charge, client_key

DATA = KB_DATA        # 随代码发布的资料（演示行程、车程表）
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# 凭证原文件只有这几种可以直接在浏览器里打开，其他一律下载（防止网页文件里的脚本冒用登录）
INLINE_SAFE = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"}
# 上线后同时在生成的行程最多几个（等确认路线框架的也算）；生成完的任务保留多久（秒）
MAX_UNFINISHED_JOBS = 5
JOB_KEEP_S = 3600
# 对话时猪猪一轮最多查几次（每次都是一次模型调用，按次数计入额度）
CHAT_MAX_STEPS = 15
# 每个行程每天最多读几篇攻略（读网页要时间，读不到的也算）
DAILY_GUIDES_PER_TRIP = 30
# 新建行程的信息清单：成员、地区、已有预订最多几个
INTAKE_LIMITS = {"members": 12, "regions": 10, "existing_bookings": 30, "couples": 6}
UPLOAD_ID = re.compile(r"^[0-9a-f]{10}$")
log = logging.getLogger("tripagent.api")


# ---------- 请求和返回的数据结构 ----------

class ChatIn(BaseModel):
    member_id: str
    text: str = Field(min_length=1, max_length=4000)


class ConfirmIn(BaseModel):
    member_id: str


class ApplyEvidenceIn(BaseModel):
    member_id: str
    extraction: VoucherExtraction


class AnswerIn(BaseModel):
    text: str = Field(max_length=2000)


class JoinIn(BaseModel):
    token: str = Field(max_length=200)


class ExpenseBody(BaseModel):
    member_id: str
    expense: ExpenseIn


class DisputeIn(BaseModel):
    member_id: str
    note: str = Field(default="", max_length=500)


class ActualIn(BaseModel):
    member_id: str
    charged_amount: Decimal
    charged_currency: str | None = None


class TransferIn(BaseModel):
    member_id: str
    from_member: str
    to_member: str
    amount: Decimal
    note: str = Field(default="", max_length=200)


class UnitsIn(BaseModel):
    member_id: str
    units: list[Annotated[list[str], Field(max_length=12)]] = Field(max_length=12)


class PollOptionIn(BaseModel):
    label: str = Field(max_length=80)
    impact: str = Field(default="", max_length=300)


class PollIn(BaseModel):
    member_id: str
    question: str = Field(max_length=200)
    options: list[PollOptionIn] = Field(max_length=8)
    deadline_hours: float | None = Field(default=None, ge=1, le=24 * 14)   # 截止时间：1 小时到 14 天
    divergence_id: str | None = None


class VoteIn(BaseModel):
    member_id: str
    option_id: str


class ClosePollIn(BaseModel):
    member_id: str
    option_id: str | None = None


ACCEPT_WORDS = ("确认", "可以", "好", "同意", "ok", "OK", "没问题", "就这样")
ANSWER_TIMEOUT_S = 15 * 60


@dataclass
class Job:
    """首次生成行程的后台任务。"""

    id: str
    status: str = "running"            # running / needs_input / done / failed
    stage: str = ""
    message: str = ""
    trip_id: str | None = None
    questions: list[str] = field(default_factory=list)
    skeleton: dict | None = None
    answer: str | None = None
    event: threading.Event = field(default_factory=threading.Event)
    session: str | None = None         # 上线后：发起这个任务的登录会话（只有他能看进度、确认路线框架）
    client: str | None = None          # 发起的来源（网络地址）：每个来源同时只能生成一个
    finished_at: float | None = None   # 生成完（成功或失败）的时间，过一阵从内存里清掉

    def view(self) -> dict:
        return {"id": self.id, "status": self.status, "stage": self.stage, "message": self.message,
                "trip_id": self.trip_id, "questions": self.questions, "skeleton": self.skeleton}


class PatchView(BaseModel):
    id: str
    summary: str
    status: str
    base_version: int
    can_confirm: bool
    issues: list[dict]
    changes: list[str]


class ChatOut(BaseModel):
    reply: str
    patches: list[PatchView]
    decisions: list[dict]
    tool_calls: list[dict]
    usage: dict


def report_to_dict(report: Report) -> dict:
    kinds = {Paragraph: "paragraph", Bullets: "bullets", Table: "table", Card: "card", Heading: "heading"}

    def block(b):
        d = asdict(b)
        d["kind"] = kinds[type(b)]
        return d

    return {"title": report.title, "cover_lines": report.cover_lines,
            "legend": [{"city": c, "color": f"#{fill}"} for c, fill in report.legend],
            "sections": [{"title": s.title, "new_page": s.new_page, "blocks": [block(b) for b in s.blocks]}
                         for s in report.sections]}


def create_app(service: TripService, llm_factory: Callable[[], LLM], *, uploads_dir: Path = UPLOADS,
               gateway: MCPGateway | None = None, guide_fetch: Callable[[str], GuideNote] = fetch_note,
               auth: Auth | None = None, beta_code: str | None = None, admin_token: str | None = None,
               daily_llm_limit: int | None = None, daily_trip_limit: int | None = None,
               daily_site_llm_limit: int | None = None, cookie_secure: bool = False) -> FastAPI:
    """auth 为 invite 模式时要用邀请链接登录（上线用）；不传就是 open 模式（本地开发，页面上可以切换身份）。"""
    app = FastAPI(title="小猪游记 API", version="0.3.0")
    invite_mode = bool(auth and auth.enabled)
    engine = auth.engine if auth else None   # 用量记在数据库里，重启不清零
    # 上限是 0 就是不允许；None 才是不限
    trip_quota = Quota(daily_trip_limit, engine) if daily_trip_limit is not None else None
    guide_quota = Quota(DAILY_GUIDES_PER_TRIP, engine) if invite_mode else None

    def failure_text(what: str, exc: Exception) -> str:
        """出错时给用户看的话。本地开发直接显示原因；上线后只给错误编号，细节记在服务器日志里
        （异常信息里可能有接口地址、文件路径、请求编号，不该给用户看）。"""
        if not invite_mode:
            return f"{what}：{type(exc).__name__}: {exc}"
        ref = uuid.uuid4().hex[:8]
        log.error("%s（错误编号 %s）", what, ref, exc_info=exc)
        return f"{what}，请稍后重试（错误编号 {ref}）"

    @app.middleware("http")
    async def _unexpected_errors(request: Request, call_next):
        """没预料到的错误也返回带 detail 的 JSON。这层在 CORS 里面，浏览器能看到原因。"""
        try:
            return await call_next(request)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"detail": failure_text("服务器出错", exc)})

    @app.exception_handler(RequestValidationError)
    async def _invalid(_req: Request, exc: RequestValidationError):
        """参数不对：只说哪里不对，最多 10 条，不把提交的内容原样返回（请求很大时会很耗内存）。"""
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()[:10]]
        return JSONResponse(status_code=422, content={"detail": errors})

    app.add_middleware(SecurityMiddleware, auth=auth, beta_code=beta_code,
                       quota=Quota(daily_llm_limit, engine) if daily_llm_limit is not None else None,
                       site_quota=Quota(daily_site_llm_limit, engine) if daily_site_llm_limit is not None else None)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                       allow_methods=["*"], allow_headers=["*"])
    llm_holder: dict[str, LLM] = {}
    vault = VoucherService(service, uploads_dir)
    money = MoneyService(service, rates=gateway_rates(gateway) if gateway and gateway.available("frankfurter") else None)
    guides = GuideService(service, fetch=guide_fetch,
                          budget=(lambda trip_id: guide_quota.take(f"guides:{trip_id}")) if guide_quota else None)
    vault.prune_all()
    for stale in OUTPUT.glob("tmp*.zip") if OUTPUT.is_dir() else []:   # 上次没删掉的行程包临时文件
        if time.time() - stale.stat().st_mtime > 3600:
            stale.unlink(missing_ok=True)

    def docx_path(trip_id: str, version: int) -> Path:
        """Word 报告只留每个行程最新的一版，旧版本删掉，免得占满硬盘。"""
        OUTPUT.mkdir(parents=True, exist_ok=True)
        name = f"{safe_name(trip_id)}-V{version}.docx"
        for old in OUTPUT.glob(f"{safe_name(trip_id)}-V*.docx"):
            if old.name != name:
                old.unlink(missing_ok=True)
        return OUTPUT / name

    def report_for(trip_id: str):
        """网页、Word、行程包用同一份报告：带上凭证夹和账本。"""
        v = service.latest(trip_id)
        return v, build_report(v.state, service.places_for(v.state), changelog=changelog_of(v),
                               vouchers=vault.views(trip_id), money=money.summary(trip_id),
                               guide_links=guides.links_by_place(trip_id))
    jobs: dict[str, Job] = {}

    def llm() -> LLM:
        if "llm" not in llm_holder:
            llm_holder["llm"] = llm_factory()
        return llm_holder["llm"]

    def agent() -> TripAgent:
        return TripAgent(service, llm(), gateway=gateway, max_steps=CHAT_MAX_STEPS, guides=guides)

    def chat_out(reply: AgentReply, viewer: str | None = None) -> "ChatOut":
        return ChatOut(
            reply=reply.text,
            patches=[patch_view(service.patch(p.id), viewer) for p in reply.patches],
            decisions=[d.model_dump(mode="json") for d in reply.decisions],
            tool_calls=[{"name": c.name, "error": c.error} for c in reply.tool_calls],
            usage=asdict(reply.usage),
        )

    def viewer_of(request: Request) -> str | None:
        """上线后按登录的成员过滤隐私（体重）；本地开发不区分。"""
        return request.scope["state"].get("member_id") if invite_mode else None

    def patch_view(p: Patch, viewer: str | None = None) -> PatchView:
        changes: list[str] = []
        if p.changes:
            before = service.version(p.trip_id, p.base_version).state
            try:
                names = {pid: pl.name_zh for pid, pl in service.places_for(before).items()}
                changes = describe(p.changes, before, apply_ops(before, p.ops), names)
            except Exception:
                changes = [f"{c.kind} {c.collection} {c.id}" for c in p.changes]
        return PatchView(id=p.id, summary=p.summary, status=p.status.value, base_version=p.base_version,
                         can_confirm=p.status.value == "proposed" and not p.has_errors,
                         issues=[i.view(viewer) for i in p.issues], changes=changes)

    def changelog_of(v) -> list[str]:
        return v.changelog if v.version > 1 else []  # 第 1 版只有"创建行程"，报告里不单列

    def acting(request: Request, claimed: str | None) -> str:
        """这次请求是谁：invite 模式以登录会话为准，open 模式用页面传来的 member_id。"""
        if invite_mode:
            return request.scope["state"]["member_id"]
        if not claimed:
            raise HTTPException(400, "缺少 member_id")
        return claimed

    def require_admin(request: Request) -> None:
        if not admin_token or not secrets.compare_digest(request.headers.get("x-admin-token", "").encode(), admin_token.encode()):
            raise HTTPException(403, "管理口令不对")

    def set_session_cookie(response: Response, session: str) -> None:
        response.set_cookie(COOKIE, session, max_age=180 * 24 * 3600, httponly=True, samesite="lax",
                            secure=cookie_secure, path="/")

    def require_trip(trip_id: str) -> None:
        if trip_id not in service.trip_ids():
            raise HTTPException(404, f"找不到行程 {trip_id}")

    @app.exception_handler(ConflictError)
    async def _conflict(_req: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ModelBudgetExceeded)
    async def _budget(_req: Request, exc: ModelBudgetExceeded):
        return JSONResponse(status_code=429, content={"detail": str(exc)})

    @app.exception_handler(ServiceError)
    async def _service_error(_req: Request, exc: ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ---------- 行程 ----------

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/trips")
    def list_trips(request: Request):
        rows = []
        mine = request.scope["state"]["memberships"]
        for trip_id in service.trip_ids():
            if invite_mode and trip_id not in mine:
                continue
            v = service.latest(trip_id)
            t = v.state
            rows.append({"id": trip_id, "title": t.meta.title, "start_date": t.meta.start_date,
                         "end_date": t.meta.end_date, "version": v.version, "destination": t.meta.destination,
                         "members": [{"id": m.id, "name": m.name, "role": m.role} for m in t.members]})
        return rows

    @app.post("/api/demo")
    def create_demo(request: Request):
        """载入新西兰 V8 演示行程（已存在就直接返回）。上线后只有管理员能载入。"""
        if invite_mode:
            require_admin(request)
        trip = load_trip(DATA / "demo" / "nz_trip_v8.yaml")
        if trip.meta.id not in service.trip_ids():
            service.create_trip(trip, load_decisions(DATA / "demo" / "nz_decisions.yaml"))
        return {"id": trip.meta.id}

    @app.get("/api/trips/{trip_id}")
    def get_trip(trip_id: str, request: Request):
        require_trip(trip_id)
        v = service.latest(trip_id)
        trip = v.state.model_dump(mode="json")
        if invite_mode:  # 体重只用于校验骑马、漂流等限制，不给其他成员看（18 节）
            for m in trip["members"]:
                if m["id"] != request.scope["state"]["member_id"]:
                    m.pop("weight_kg", None)
        return {
            "version": v.version,
            "trip": trip,
            "issues": [i.view(viewer_of(request)) for i in service.validate(trip_id)],
            "pending": [patch_view(p, viewer_of(request)) for p in service.pending_patches(trip_id)],
            "decisions": [d.model_dump(mode="json") for d in service.decisions(trip_id, active_only=True)],
        }

    @app.get("/api/trips/{trip_id}/report")
    def get_report(trip_id: str):
        require_trip(trip_id)
        v = service.latest(trip_id)
        return report_to_dict(report_for(trip_id)[1])

    @app.get("/api/trips/{trip_id}/report.docx")
    def get_report_docx(trip_id: str):
        require_trip(trip_id)
        v = service.latest(trip_id)
        path = render_docx(report_for(trip_id)[1], docx_path(trip_id, v.version))
        return FileResponse(path, filename=f"{safe_name(v.state.meta.title)}-V{v.version}.docx",
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    @app.get("/api/trips/{trip_id}/live")
    def get_live(trip_id: str):
        """实时情况：路况、天气预警、天气预报、公众假日，以及它们对行程的影响。"""
        require_trip(trip_id)
        return live_report(service, trip_id)

    @app.get("/api/trips/{trip_id}/calendar.ics")
    def get_calendar(trip_id: str):
        """导出日历文件，导入手机日历后每个安排都有提醒。"""
        require_trip(trip_id)
        trip = service.current(trip_id)
        return Response(trip_ics(trip, service.places_for(trip)), media_type="text/calendar; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(trip.meta.title)}.ics"})

    @app.get("/api/trips/{trip_id}/versions")
    def get_versions(trip_id: str):
        require_trip(trip_id)
        return [{"version": v.version, "patch_id": v.patch_id, "changelog": v.changelog}
                for v in service.history(trip_id)]

    @app.get("/api/trips/{trip_id}/messages")
    def get_messages(trip_id: str, request: Request, member_id: str = ""):
        require_trip(trip_id)
        member_id = acting(request, member_id)
        return [{"role": m.role, "text": m.text} for m in service.conversation(trip_id, member_id)]

    # ---------- 对话和改动 ----------

    @app.post("/api/trips/{trip_id}/chat", response_model=ChatOut)
    def chat(trip_id: str, body: ChatIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        return chat_out(agent().handle(trip_id, me, body.text), viewer_of(request))

    # ---------- 从信息清单生成行程 ----------

    site_quota = Quota(daily_site_llm_limit, engine) if daily_site_llm_limit is not None else None

    def run_job(job: Job, intake: TripIntake) -> None:
        if site_quota is not None:   # 生成行程调用模型也算进全站每天的额度
            MODEL_CALLS.set(lambda: charge([(site_quota, "site")]))
        def progress(stage: str, msg: str) -> None:
            job.stage, job.message = stage, msg

        def review(sk) -> str | None:
            job.skeleton = {"summary": sk.summary, "assumptions": sk.assumptions,
                            "days": [{"date": d.date, "route": d.route, "night_city": d.night_city, "focus": d.focus}
                                     for d in sk.days],
                            "flights": [f.model_dump() for f in sk.flights]}
            job.questions = [*sk.questions, "路线框架如上。确认就回复「确认」；想改哪里，直接说。"]
            job.status, job.message = "needs_input", "等你确认路线框架"
            job.event.clear()
            if not job.event.wait(ANSWER_TIMEOUT_S):
                raise TimeoutError("等待确认路线框架超时，请重新提交")
            text = (job.answer or "").strip()
            job.status, job.questions = "running", []
            return None if text in ACCEPT_WORDS or text.startswith(ACCEPT_WORDS[:2]) else text

        try:
            planner = Planner(llm(), service.places, service.drive_times, today=date.today(), gateway=gateway)
            result = planner.generate(intake, trip_id=job.id.replace("job_", "trip_"), on_progress=progress,
                                      review_skeleton=review)
            service.create_trip(result.trip, result.decisions)
            if invite_mode and job.session:   # 建行程的人就是发起人（信息清单里的第一个成员）
                auth.add_membership(job.session, result.trip.meta.id, result.trip.members[0].id)
            CollabService(service).seed_from_intake(result.trip.meta.id, intake)
            money.seed_units(result.trip.meta.id, intake.couples,
                             dict(zip([m.name for m in intake.members], member_ids(intake))))
            errors = len(result.errors)
            job.trip_id, job.status = result.trip.meta.id, "done"
            job.message = "行程已生成" + (f"，还有 {errors} 个冲突需要处理" if errors else "")
        except NeedsInput as exc:
            job.status, job.questions, job.message = "failed", exc.questions, "信息清单还缺几项"
        except TimeoutError as exc:   # 等确认路线框架超时
            job.status, job.message = "failed", str(exc)
        except Exception as exc:  # 后台任务出错要告诉用户，不能悄悄失败
            job.status, job.message = "failed", failure_text("生成失败", exc)
        finally:
            job.finished_at = time.monotonic()

    def prune_jobs() -> None:
        """生成完一小时的任务从内存里清掉。"""
        now = time.monotonic()
        for job_id in [j.id for j in jobs.values() if j.finished_at and now - j.finished_at > JOB_KEEP_S]:
            jobs.pop(job_id, None)

    def new_session(old: str | None, joined_token: str | None = None) -> tuple[str, str | None, str | None]:
        """换新的登录会话（join 或新建行程时）。返回（新会话，行程 id，成员 id）；这个浏览器正在生成的行程跟着走。"""
        if joined_token is None:
            session, trip_id, member_id = auth.rotate(old), None, None
        else:
            session, trip_id, member_id = auth.join(joined_token, old)
        for j in jobs.values():
            if old and j.session == old:
                j.session = session
        return session, trip_id, member_id

    @app.post("/api/trips")
    def create_trip(intake: TripIntake, request: Request, response: Response):
        """新建行程（后台生成，会多次调用模型）。上线后要内测邀请码，并限制同时生成和每天新建的数量。"""
        session = request.scope["state"]["session"]
        prune_jobs()
        if invite_mode:
            if not beta_code:   # 没设置邀请码就不开放，免得谁都能花模型的钱
                raise HTTPException(403, "新建行程暂未开放。")
            if not secrets.compare_digest(request.headers.get("x-beta-code", "").strip().encode(), beta_code.encode()):
                raise HTTPException(403, "内测邀请码不对。请找小猪游记团队要邀请码。")
            unfinished = [j for j in jobs.values() if j.finished_at is None]
            client = client_key(request.headers.raw)
            if any((session and j.session == session) or (client != "unknown" and j.client == client) for j in unfinished):
                raise HTTPException(429, "你有一个行程正在生成，等它完成后再新建。")
            if len(unfinished) >= MAX_UNFINISHED_JOBS:
                raise HTTPException(429, "现在同时生成的行程太多了，请过几分钟再试。")
        for field_name, most in INTAKE_LIMITS.items():
            if len(getattr(intake, field_name) or []) > most:
                raise HTTPException(422, f"信息清单里的 {field_name} 太多了（最多 {most} 个）")
        if problems := intake_problems(intake):   # 缺了没法开始的信息：先说，不占当天的名额
            raise HTTPException(422, "；".join(problems))
        if trip_quota and not trip_quota.take("new_trips"):
            raise HTTPException(429, f"今天新建行程的名额用完了（每天 {trip_quota.daily_limit} 个），明天再来吧。")
        job = Job(id=f"job_{uuid.uuid4().hex[:8]}")
        if invite_mode:
            job.client = client_key(request.headers.raw)
            job.session = new_session(session)[0]
            set_session_cookie(response, job.session)
        jobs[job.id] = job
        threading.Thread(target=run_job, args=(job, intake), daemon=True).start()
        return {"job_id": job.id}

    def own_job(request: Request, job: Job) -> None:
        if invite_mode and job.session != request.scope["state"]["session"]:
            raise HTTPException(404, "找不到这个任务")

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        if job_id not in jobs:
            raise HTTPException(404, f"找不到任务 {job_id}")
        own_job(request, jobs[job_id])
        return jobs[job_id].view()

    @app.post("/api/jobs/{job_id}/answer")
    def answer_job(job_id: str, body: AnswerIn, request: Request):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"找不到任务 {job_id}")
        own_job(request, job)
        if job.status != "needs_input":
            raise HTTPException(400, "这个任务现在不需要回答")
        job.answer = body.text
        job.event.set()
        return job.view()

    # ---------- 凭证 ----------

    @app.post("/api/trips/{trip_id}/evidence")
    def upload_evidence(trip_id: str, request: Request, member_id: str = Form(""), file: UploadFile = File(...)):
        """上传凭证图片或 PDF，返回识别出的字段，供用户核对。这一步不改行程。"""
        require_trip(trip_id)
        member_id = acting(request, member_id)
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "文件太大，请上传 10MB 以内的图片或 PDF")
        if guess_mime(file.filename or "upload", data) not in STORABLE:
            raise HTTPException(415, "不支持的文件类型。请上传图片（PNG、JPG）、PDF 或确认邮件（.eml）。")
        vault.prune_pending(trip_id)
        vault.check_space(trip_id, len(data))
        upload_id = uuid.uuid4().hex[:10]
        saved = save_upload(uploads_dir, trip_id, upload_id, file.filename or "upload", data)
        meta = service.current(trip_id).meta
        hint = f"{meta.title}，{meta.start_date} 至 {meta.end_date}"
        try:
            extraction = extract_voucher(llm(), data, file.filename or "upload", trip_hint=hint)
        except ValidationError as exc:   # 模型的输出读不懂（例如太长被截断）：不把模型原文返回给用户
            saved.unlink(missing_ok=True)
            raise HTTPException(502, failure_text("识别结果读不懂，请换一张更清楚的凭证", exc)) from exc
        except ValueError as exc:
            saved.unlink(missing_ok=True)
            raise HTTPException(415, str(exc)) from exc
        except Exception:
            saved.unlink(missing_ok=True)
            raise
        vault.remember_upload(trip_id, upload_id, member_id)
        return {"upload_id": upload_id, "filename": file.filename, "extraction": extraction.model_dump(mode="json")}

    @app.post("/api/trips/{trip_id}/evidence/{upload_id}/apply", response_model=ChatOut)
    def apply_evidence(trip_id: str, upload_id: str, body: ApplyEvidenceIn, request: Request):
        """用户核对过识别结果后，交给 agent 记录凭证并提出改动。每次上传只能用一次，只有上传的人能用。"""
        require_trip(trip_id)
        me = acting(request, body.member_id)
        owner = vault.upload_owner(trip_id, upload_id)
        path = vault.pending_file(trip_id, upload_id)
        if path is None or (owner is None and invite_mode):
            raise HTTPException(404, "找不到上传的文件，请重新上传")
        if owner is not None and owner != me:
            raise HTTPException(403, "只有上传的人能确认这份凭证")
        if len(body.extraction.model_dump_json()) > 20_000:
            raise HTTPException(413, "识别结果太长了")
        filename = path.name.removeprefix(f"{upload_id}_")
        ev = vault.store_extraction(trip_id, me, upload_id, body.extraction)  # 先存进凭证夹，大家都能看到
        vault.forget_upload(trip_id, upload_id)
        drafts = draft_expenses_from_voucher(trip_id, me, ev.id, body.extraction)   # 有金额的顺带生成账目草稿
        message = evidence_message(filename, body.extraction, ev.id)
        if drafts:
            message += ("\n系统已按凭证金额生成账目草稿：" + "；".join(drafts)
                        + "。草稿要在「账本」里确认后才入账；不用再 propose_expense，回复里告诉用户去账本核对。")
        return chat_out(agent().handle(trip_id, me, message), viewer_of(request))

    # ---------- 凭证夹 ----------

    @app.get("/api/trips/{trip_id}/vouchers")
    def list_vouchers(trip_id: str):
        require_trip(trip_id)
        return vault.views(trip_id)

    @app.post("/api/trips/{trip_id}/vouchers")
    def store_voucher(trip_id: str, request: Request, member_id: str = Form(""), file: UploadFile = File(...),
                      title: str = Form("", max_length=120),
                      kind: Literal["flight", "accommodation", "car", "activity", "other"] = Form("other"),
                      date_: str = Form("", alias="date"), item_id: str = Form(""), notes: str = Form("", max_length=500)):
        """只存档：不识别、不改行程，直接放进凭证夹。"""
        require_trip(trip_id)
        member_id = acting(request, member_id)
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "文件太大，请上传 10MB 以内的文件")
        if not data:
            raise HTTPException(400, "文件是空的")
        try:
            day = date.fromisoformat(date_) if date_ else None
        except ValueError as exc:
            raise HTTPException(422, "日期格式应为 YYYY-MM-DD") from exc
        ev = vault.store_file(trip_id, member_id, file.filename or "voucher", data, title=title, kind=kind, day=day,
                              item_ids=[item_id] if item_id else [], notes=notes)
        return next(v for v in vault.views(trip_id) if v["id"] == ev.id)

    @app.get("/api/trips/{trip_id}/vouchers/{evidence_id}/file")
    def voucher_file(trip_id: str, evidence_id: str, download: bool = False):
        """查看或下载凭证原文件。"""
        require_trip(trip_id)
        ev, path = vault.path(trip_id, evidence_id)
        name = ev.filename or path.name
        with path.open("rb") as f:
            mime = guess_mime(name, f.read(16))   # 按文件内容判断，不信记录里的类型
        inline = not download and mime in INLINE_SAFE
        headers = {"Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}",
                   "X-Content-Type-Options": "nosniff"}
        if mime != "application/pdf":   # PDF 要用浏览器自带的阅读器，不能加沙箱
            headers["Content-Security-Policy"] = "sandbox"
        return FileResponse(path, media_type=mime if inline else "application/octet-stream", headers=headers)

    @app.delete("/api/trips/{trip_id}/vouchers/{evidence_id}")
    def delete_voucher(trip_id: str, evidence_id: str, request: Request, member_id: str = ""):
        require_trip(trip_id)
        vault.delete(trip_id, evidence_id, acting(request, member_id))
        return {"ok": True}

    @app.get("/api/trips/{trip_id}/bundle.zip")
    def get_bundle(trip_id: str):
        """行程包：Word 报告 + 日历文件 + 凭证夹里的所有原文件。"""
        require_trip(trip_id)
        v = service.latest(trip_id)
        trip = v.state
        places = service.places_for(trip)
        title = trip.meta.title
        docx = render_docx(report_for(trip_id)[1], docx_path(trip_id, v.version))
        base = safe_name(title)
        OUTPUT.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=OUTPUT)
        os.close(fd)
        try:
            vault.bundle(trip_id, {f"{base}-V{v.version}.docx": docx.read_bytes(),
                                   f"{base}-账目.csv": money.csv(trip_id).encode("utf-8"),
                                   f"{base}.ics": trip_ics(trip, places).encode("utf-8")}, Path(tmp))
        except BaseException:
            os.unlink(tmp)
            raise
        return FileResponse(tmp, media_type="application/zip", background=BackgroundTask(os.unlink, tmp), headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{base}-V{v.version}-行程包.zip')}"})

    @app.get("/api/trips/{trip_id}/patches", response_model=list[PatchView])
    def list_patches(trip_id: str, request: Request, status: Literal["proposed", "all"] = "proposed"):
        require_trip(trip_id)
        rows = service.pending_patches(trip_id) if status == "proposed" else service.store.patches(trip_id)
        return [patch_view(p, viewer_of(request)) for p in rows]

    @app.post("/api/trips/{trip_id}/patches/{patch_id}/confirm")
    def confirm_patch(trip_id: str, patch_id: str, body: ConfirmIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        version = TripAgent(service, llm=None).confirm(trip_id, me, patch_id)  # 确认不需要模型
        return {"version": version.version, "changelog": version.changelog}

    @app.post("/api/trips/{trip_id}/patches/{patch_id}/reject", response_model=PatchView)
    def reject_patch(trip_id: str, patch_id: str, request: Request):
        require_trip(trip_id)
        return patch_view(service.reject(trip_id, patch_id), viewer_of(request))

    # ---------- 多人协作（设计规格第 13 节） ----------

    def group_view(trip_id: str) -> dict:
        collab = CollabService(service)
        trip = service.current(trip_id)
        prefs = collab.preferences(trip_id)
        summary = collab.summary(trip_id)
        polls = collab.polls(trip_id)
        filled = {m for m, p in prefs.items() if not p.is_empty()}
        return {
            "members": [{"id": m.id, "name": m.name, "role": m.role, "filled": m.id in filled,
                         "updated_at": prefs[m.id].updated_at.isoformat() if m.id in prefs else None}
                        for m in trip.members],
            "preferences": {m: p.model_dump(mode="json") for m, p in prefs.items()},
            "summary": summary.model_dump(mode="json") if summary else None,
            "summary_stale": collab.summary_is_stale(trip_id, summary) if summary else False,
            "polls": [p.model_dump(mode="json") for p in reversed(polls)],
            "all_filled": len(filled) == len(trip.members),
        }

    @app.get("/api/trips/{trip_id}/group")
    def get_group(trip_id: str):
        """「大家」页签：每人偏好、填写进度、最近一次汇总、投票。"""
        require_trip(trip_id)
        return group_view(trip_id)

    @app.put("/api/trips/{trip_id}/preferences/{member_id}")
    def put_preferences(trip_id: str, member_id: str, form: PreferenceForm):
        require_trip(trip_id)
        CollabService(service).save_preferences(trip_id, member_id, form)
        return group_view(trip_id)

    @app.put("/api/trips/{trip_id}/members/{member_id}/profile")
    def put_profile(trip_id: str, member_id: str, profile: MemberProfile, request: Request):
        """成员本人补充或修改资料（护照、签证、开车、驾照、年龄、体重），改了直接生成新版本。
        上线后只能改自己的（门卫查一遍，这里再查一遍）。请求里没写的字段保持原样。"""
        require_trip(trip_id)
        if invite_mode and acting(request, None) != member_id:
            raise HTTPException(403, "只能修改自己的资料。")
        version, issues = service.update_member_profile(trip_id, member_id, profile)
        return {"changed": version is not None, "version": (version or service.latest(trip_id)).version,
                "changelog": version.changelog if version else [],
                "issues": [i.view(viewer_of(request)) for i in issues]}

    @app.post("/api/trips/{trip_id}/summary")
    def post_summary(trip_id: str, body: ConfirmIn, request: Request):
        """汇总大家的想法（会调用模型）。发起人或编辑者才能发起，控制费用。"""
        require_trip(trip_id)
        me = acting(request, body.member_id)
        member = service.current(trip_id).member(me)
        if member is None or member.role not in ("owner", "editor"):
            raise HTTPException(403, "请发起人或编辑者来汇总")
        CollabService(service).summarize(trip_id, llm(), by=me)
        return group_view(trip_id)

    @app.post("/api/trips/{trip_id}/polls")
    def post_poll(trip_id: str, body: PollIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        CollabService(service).create_poll(trip_id, me, body.question, body.options,
                                           deadline=deadline_in(body.deadline_hours), divergence_id=body.divergence_id)
        return group_view(trip_id)

    @app.post("/api/trips/{trip_id}/polls/{poll_id}/vote")
    def post_vote(trip_id: str, poll_id: str, body: VoteIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        CollabService(service).vote(trip_id, poll_id, me, body.option_id)
        return group_view(trip_id)

    @app.post("/api/trips/{trip_id}/polls/{poll_id}/close")
    def post_close_poll(trip_id: str, poll_id: str, body: ClosePollIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        CollabService(service).close(trip_id, poll_id, me, body.option_id)
        return group_view(trip_id)

    # ---------- 账本（设计规格第 14 节） ----------

    VOUCHER_CATEGORY = {"flight": "flight", "accommodation": "accommodation", "car": "car", "activity": "activity"}

    def draft_expenses_from_voucher(trip_id: str, member_id: str, evidence_id: str, extraction: VoucherExtraction) -> list[str]:
        """凭证上有金额的，生成账目草稿：付款人默认是上传的人（14.3 节），分摊人按凭证上写的人。
        查不到汇率就不生成，不影响凭证。返回生成了哪些草稿（告诉猪猪，免得它再记一次）。"""
        trip = service.current(trip_id)
        created = []
        for b in extraction.bookings:
            if not b.price_amount or not b.price_currency or b.price_amount <= 0:
                continue
            on_voucher = members_on_voucher(trip, b.participants, b.pax, member_id)
            who = "、".join(service.name_of(trip_id, m) for m in on_voucher) if on_voucher else ""
            note = (f"上传凭证时自动生成：按凭证上的人分摊（{who}）" if who
                    else "上传凭证时自动生成：分摊人按默认规则，请核对凭证上是哪几个人") + "；确认实际扣款后入账"
            try:
                e = money.add(trip_id, ExpenseIn(
                    title=(b.title or "预订")[:100], category=VOUCHER_CATEGORY.get(b.kind, "other"), payer=member_id,
                    amount=Decimal(str(b.price_amount)), currency=b.price_currency.upper(), date=date.today(),
                    splits=[Split(member_id=m) for m in on_voucher] if on_voucher else None,
                    evidence_id=evidence_id, status="draft", note=note), member_id)
            except (ServiceError, ValueError):
                continue
            created.append(f"「{e.title}」{fmt(e.amount, e.currency)}，付款人 {service.name_of(trip_id, member_id)}，"
                           + (f"{who} 分摊" if who else "按默认分摊人（待核对）"))
        return created

    @app.get("/api/trips/{trip_id}/money")
    def get_money(trip_id: str):
        require_trip(trip_id)
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/expenses")
    def add_expense(trip_id: str, body: ExpenseBody, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.add(trip_id, body.expense, me)
        return money.summary(trip_id)

    @app.put("/api/trips/{trip_id}/money/expenses/{expense_id}")
    def edit_expense(trip_id: str, expense_id: str, body: ExpenseBody, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.update(trip_id, expense_id, body.expense, me)
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/expenses/{expense_id}/confirm")
    def confirm_expense(trip_id: str, expense_id: str, body: ConfirmIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.confirm(trip_id, expense_id, me)
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/expenses/{expense_id}/dispute")
    def dispute_expense(trip_id: str, expense_id: str, body: DisputeIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.dispute(trip_id, expense_id, me, body.note)
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/expenses/{expense_id}/actual")
    def actual_expense(trip_id: str, expense_id: str, body: ActualIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.set_actual(trip_id, expense_id, body.charged_amount, me, body.charged_currency)
        return money.summary(trip_id)

    @app.delete("/api/trips/{trip_id}/money/expenses/{expense_id}")
    def delete_expense(trip_id: str, expense_id: str, request: Request, member_id: str = ""):
        require_trip(trip_id)
        money.delete(trip_id, expense_id, acting(request, member_id))
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/transfers")
    def add_transfer(trip_id: str, body: TransferIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.add_transfer(trip_id, body.from_member, body.to_member, body.amount, me, body.note)
        return money.summary(trip_id)

    @app.post("/api/trips/{trip_id}/money/transfers/{transfer_id}/confirm")
    def confirm_transfer(trip_id: str, transfer_id: str, body: ConfirmIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.confirm_transfer(trip_id, transfer_id, me)
        return money.summary(trip_id)

    @app.delete("/api/trips/{trip_id}/money/transfers/{transfer_id}")
    def delete_transfer(trip_id: str, transfer_id: str, request: Request, member_id: str = ""):
        require_trip(trip_id)
        money.delete_transfer(trip_id, transfer_id, acting(request, member_id))
        return money.summary(trip_id)

    @app.put("/api/trips/{trip_id}/money/units")
    def put_units(trip_id: str, body: UnitsIn, request: Request):
        require_trip(trip_id)
        me = acting(request, body.member_id)
        money.set_units(trip_id, body.units, me)
        return money.summary(trip_id)

    @app.get("/api/trips/{trip_id}/money.csv")
    def get_money_csv(trip_id: str):
        require_trip(trip_id)
        title = service.current(trip_id).meta.title
        return Response(money.csv(trip_id).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title + '-账目.csv')}"})

    # ---------- 小红书攻略（设计规格 15.4 节） ----------

    def guide_view(trip_id: str, g) -> dict:
        names = {pid: p.name_zh for pid, p in service.places_for(service.current(trip_id)).items()}
        data = g.model_dump(mode="json")
        data["added_by_name"] = service.name_of(trip_id, g.added_by)
        for p in data["extraction"]["places"]:
            p["place_name"] = names.get(p["place_id"]) if p["place_id"] else None
        return data

    @app.get("/api/trips/{trip_id}/guides")
    def list_guides(trip_id: str):
        require_trip(trip_id)
        return [guide_view(trip_id, g) for g in reversed(guides.guides(trip_id))]

    @app.post("/api/trips/{trip_id}/guides")
    def read_guide(trip_id: str, request: Request, member_id: str = Form(""), text: str = Form("", max_length=5000),
                   file: UploadFile | None = File(None)):
        """读一篇小红书攻略：贴链接或整段分享文字；读不到时上传截图（会调用模型）。"""
        require_trip(trip_id)
        member_id = acting(request, member_id)
        image = None
        if file is not None:
            data = file.file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "截图太大，请上传 10MB 以内的图片")
            mime = guess_mime(file.filename or "shot", data)
            if not mime.startswith("image/"):
                raise HTTPException(415, "截图要是图片（PNG、JPG）")
            image = Attachment(data=data, mime=mime, filename=file.filename or "shot")
        elif not text.strip():
            raise HTTPException(400, "请贴小红书链接，或者上传笔记截图")
        try:
            g = guides.read(trip_id, member_id, llm(), text=text, image=image)
        except GuideLimit as exc:
            raise HTTPException(429, f"这个行程今天读攻略的次数用完了（每天 {DAILY_GUIDES_PER_TRIP} 篇），明天再来吧。") from exc
        except GuideError as exc:
            raise HTTPException(422, str(exc)) from exc
        return guide_view(trip_id, g)

    @app.delete("/api/trips/{trip_id}/guides/{guide_id}")
    def delete_guide(trip_id: str, guide_id: str, request: Request, member_id: str = ""):
        require_trip(trip_id)
        try:
            guides.delete(trip_id, guide_id, acting(request, member_id))
        except GuideError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    # ---------- 待确认事项（出发前清单） ----------

    @app.get("/api/trips/{trip_id}/confirm-needs")
    def get_confirm_needs(trip_id: str):
        """订好了但还缺细节、还没变成待办的事项。"""
        require_trip(trip_id)
        return [n.todo.model_dump(mode="json") for n in missing_confirm_todos(service.current(trip_id))]

    @app.post("/api/trips/{trip_id}/confirm-todos", response_model=PatchView)
    def post_confirm_todos(trip_id: str, body: ConfirmIn, request: Request):
        """把待确认事项变成待办：和其他改动一样先提出，确认后才写进行程。"""
        require_trip(trip_id)
        me = acting(request, body.member_id)
        needs = missing_confirm_todos(service.current(trip_id))
        if not needs:
            raise HTTPException(400, "没有需要新增的待确认待办")
        patch = service.propose(trip_id, [{"op": "add", "collection": "todos", "value": n.todo.model_dump(mode="json")}
                                          for n in needs],
                                f"新增 {len(needs)} 条待确认待办", reason="订好了但还缺细节的地方，确认后标完成",
                                author=me)
        return patch_view(patch, viewer_of(request))

    # ---------- 登录（专属邀请链接） ----------

    @app.get("/api/session")
    def get_session(request: Request):
        """现在是什么模式、我加入了哪些行程、在每个行程里是谁。"""
        trips = []
        for trip_id, member_id in request.scope["state"]["memberships"].items():
            if trip_id in service.trip_ids():
                member = service.current(trip_id).member(member_id)
                trips.append({"trip_id": trip_id, "member_id": member_id, "name": member.name if member else member_id,
                              "role": member.role if member else "member"})
        return {"mode": "invite" if invite_mode else "open", "trips": trips, "needs_beta_code": bool(invite_mode and beta_code)}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        """在这台设备上退出（借用别人的手机、电脑打开过时用）。"""
        if auth:
            auth.logout(request.scope["state"].get("session"))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/join/check")
    def join_check(body: JoinIn, request: Request):
        """打开链接前先看看：这是谁的链接；这台设备在这个行程里是不是已经是别人了（是的话网页先问一句）。"""
        if not invite_mode:
            raise HTTPException(400, "本地开发模式不需要邀请链接")
        try:
            trip_id, member_id = auth.peek(body.token)
        except AuthError as exc:
            raise HTTPException(403, str(exc)) from exc
        if trip_id not in service.trip_ids():
            raise HTTPException(403, "这个邀请链接对应的行程已经不在了。")
        trip = service.current(trip_id)
        current = request.scope["state"]["memberships"].get(trip_id)

        def name(m: str) -> str:
            member = trip.member(m)
            return member.name if member else m
        return {"trip_id": trip_id, "title": trip.meta.title, "member_id": member_id, "name": name(member_id),
                "current_member_id": current, "current_name": name(current) if current else None}

    @app.post("/api/join")
    def join(body: JoinIn, request: Request, response: Response):
        if not invite_mode:
            raise HTTPException(400, "本地开发模式不需要邀请链接")
        try:
            session, trip_id, member_id = new_session(request.scope["state"]["session"], body.token)
        except AuthError as exc:
            raise HTTPException(403, str(exc)) from exc
        set_session_cookie(response, session)
        return {"trip_id": trip_id, "member_id": member_id}

    @app.get("/api/trips/{trip_id}/invites")
    def get_invites(trip_id: str, request: Request):
        """发起人看谁有有效的邀请链接（链接本身只在生成时显示一次）。"""
        require_trip(trip_id)
        require_owner(request, trip_id)
        status = auth.invite_status(trip_id) if auth else {}
        return [{"member_id": m.id, "name": m.name, "role": m.role,
                 "has_link": m.id in status, "created_at": status[m.id].isoformat() if m.id in status else None}
                for m in service.current(trip_id).members]

    @app.post("/api/trips/{trip_id}/invites/{member_id}")
    def post_invite(trip_id: str, member_id: str, request: Request):
        """生成某个成员的新链接：旧链接作废，他已登录的设备退出。"""
        require_trip(trip_id)
        me = require_owner(request, trip_id)
        if service.current(trip_id).member(member_id) is None:
            raise HTTPException(404, f"行程里没有成员 {member_id}")
        if not invite_mode:
            raise HTTPException(400, "本地开发模式不需要邀请链接")
        auth.leave_all(trip_id, member_id)
        token = auth.new_invite(trip_id, member_id, created_by=me)
        if member_id == me:   # 给自己重新生成时，这台设备保持登录，不用再点一次
            auth.add_membership(request.scope["state"]["session"], trip_id, member_id)
        return {"member_id": member_id, "token": token}

    def require_owner(request: Request, trip_id: str) -> str:
        me = acting(request, request.query_params.get("member_id"))
        member = service.current(trip_id).member(me)
        if member is None or member.role != "owner":
            raise HTTPException(403, "只有发起人能管理邀请链接")
        return me

    # ---------- 管理（上线后第一次用：载入行程、生成邀请链接） ----------

    @app.get("/api/admin/trips")
    def admin_trips(request: Request):
        require_admin(request)
        return [{"id": t, "title": service.current(t).meta.title,
                 "members": [{"id": m.id, "name": m.name, "role": m.role} for m in service.current(t).members]}
                for t in service.trip_ids()]

    @app.post("/api/admin/demo")
    def admin_demo(request: Request):
        require_admin(request)
        return create_demo(request)

    @app.post("/api/admin/trips/{trip_id}/invites")
    def admin_invites(trip_id: str, request: Request):
        """给行程里每个成员生成新链接（旧的全部作废）。"""
        require_admin(request)
        require_trip(trip_id)
        if not invite_mode:
            raise HTTPException(400, "没有开启登录")
        out = []
        for m in service.current(trip_id).members:
            auth.leave_all(trip_id, m.id)
            out.append({"member_id": m.id, "name": m.name, "role": m.role,
                        "token": auth.new_invite(trip_id, m.id, created_by="admin")})
        return out

    @app.get("/api/trips/{trip_id}/activity")
    def get_activity(trip_id: str, limit: int = 50):
        """动态流：最新的在前。"""
        require_trip(trip_id)
        return [e.model_dump(mode="json") for e in service.activity(trip_id, min(max(limit, 1), 200))]

    return app


def safe_name(text: str, most: int = 60) -> str:
    """文件名里只留安全的字符（行程包里的文件名、下载文件名）。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text)
    name = re.sub(r"\.{2,}", ".", name).strip(" .")
    return name[:most] or "行程"
