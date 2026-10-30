"""上线用的门卫（ASGI 中间件）：

1. 请求体：大小有上限（按接口分），只认标准的表单类型，其他一律当 JSON 检查；路径里有控制字符直接拒绝；
2. 登录：invite 模式下，/api/trips/<行程>/... 的请求必须来自加入了这个行程的会话；
3. 认人：请求里写的 member_id（查询参数、JSON）一律换成会话里的成员，不能冒充别人；
   偏好、资料这类路径里写着成员的，只能改自己的；
4. 新建行程：先查内测邀请码，再读请求体；同一个来源猜错太多次就暂停一阵（不影响别人）；
5. 限额：每个行程、全站每天调用模型的次数有上限（设计规格 D14）。按实际调用模型的次数算，
   一次对话里猪猪查了几轮就算几次；没调用模型的请求不算。用量记在数据库里，重启不清零；
6. 所有响应加上安全头：浏览器不猜文件类型、页面不能被别的网站嵌进去、接口内容不缓存。

数据库操作放到线程里做，不卡住处理其他请求的主循环。
"""

import email.message
import json
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import date
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, unquote, urlencode

from sqlalchemy import Column, Date, Integer, MetaData, String, Table, insert, select, update
from starlette.concurrency import run_in_threadpool

from ..auth import COOKIE, Auth
from ..llm.base import MODEL_CALLS, ModelBudgetExceeded

TRIP_PATH = re.compile(r"^/api/trips/([^/]+)(/[\s\S]*)?\Z")
# 只能改自己的：路径里的成员必须是登录的成员
OWN_PATHS = [(re.compile(r"^/api/trips/[^/]+/preferences/([^/]+)\Z"), "只能修改自己的偏好。"),
             (re.compile(r"^/api/trips/[^/]+/members/([^/]+)/profile\Z"), "只能修改自己的资料。")]
UPLOAD_PATH = re.compile(r"^/api/trips/[^/]+/(vouchers|evidence|guides)\Z")
# 可能调用模型的请求：进门前先看额度还有没有
LLM_PATHS = [re.compile(p) for p in (
    r"^/api/trips/[^/]+/chat\Z", r"^/api/trips/[^/]+/evidence\Z", r"^/api/trips/[^/]+/evidence/[^/]+/apply\Z",
    r"^/api/trips/[^/]+/summary\Z", r"^/api/trips/[^/]+/guides\Z",
)]
APPLY_PATH = re.compile(r"^/api/trips/[^/]+/evidence/[^/]+/apply\Z")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# 请求体上限：上传文件（10MB 以内，加上表单本身）、新建行程的信息清单、登录这类小请求、其他 JSON
MAX_UPLOAD_BODY = 10 * 1024 * 1024 + 512 * 1024
MAX_INTAKE_BODY = 64 * 1024
MAX_SMALL_BODY = 16 * 1024
MAX_JSON_BODY = 256 * 1024
# 拒绝请求之前，先把这么大以内的请求体读完扔掉：网页的代理还在发请求体时后端就关掉连接的话，
# 用户只会看到"服务器出错"，看不到"文件太大"这类提示
MAX_DRAIN = 16 * 1024 * 1024   # 和 next.config.ts 里转发请求体的上限一样
SECURITY_HEADERS = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY")]
API_HEADERS = [(b"cache-control", b"private, no-store")]
# 内测邀请码：同一个来源 15 分钟内猜错这么多次就暂停 15 分钟；全站再设一个高得多的总上限兜底
BETA_FAILURE_WINDOW_S = 15 * 60
BETA_FAILURE_LIMIT = 10
BETA_FAILURE_SITE_LIMIT = 300
BETA_TRACKED_CLIENTS = 5000

# 兼容旧名字：这次请求里调用了几次模型
LLM_CALLS = MODEL_CALLS

_meta = MetaData()
usage_t = Table(
    "quota_usage", _meta,
    Column("name", String(120), primary_key=True),
    Column("day", Date, primary_key=True),
    Column("used", Integer, nullable=False),
)


class Quota:
    """每天的用量上限，按名字分开计（例如每个行程一个、全站一个）。
    给了数据库就记在数据库里，重启、部署都不清零；不给就只记在内存（测试用）。上限是 0 表示不允许使用。"""

    def __init__(self, daily_limit: int, engine=None):
        self.daily_limit = daily_limit
        self.engine = engine
        self._counts: dict[tuple[str, date], int] = {}
        self._lock = threading.Lock()
        if engine is not None:
            _meta.create_all(engine)

    def used(self, name: str, today: date | None = None) -> int:
        day = today or date.today()
        if self.engine is None:
            return self._counts.get((name, day), 0)
        with self.engine.connect() as c:
            return c.scalar(select(usage_t.c.used).where((usage_t.c.name == name) & (usage_t.c.day == day))) or 0

    def _add(self, name: str, n: int, day: date) -> None:
        if self.engine is None:
            self._counts[(name, day)] = max(0, self._counts.get((name, day), 0) + n)
            return
        with self.engine.begin() as c:
            row = c.scalar(select(usage_t.c.used).where((usage_t.c.name == name) & (usage_t.c.day == day)))
            if row is None:
                c.execute(insert(usage_t).values(name=name, day=day, used=max(0, n)))
            else:
                c.execute(update(usage_t).where((usage_t.c.name == name) & (usage_t.c.day == day))
                          .values(used=max(0, row + n)))

    def take(self, name: str, today: date | None = None) -> bool:
        """还有额度就记一次，返回 True；用完了返回 False。"""
        day = today or date.today()
        with self._lock:
            if self.used(name, day) >= self.daily_limit:
                return False
            self._add(name, 1, day)
            return True

    def add(self, name: str, n: int, today: date | None = None) -> None:
        """补记用量（一次请求里实际调用了好几次模型）。可以超过上限一点，下一次请求就进不来了。"""
        if n:
            with self._lock:
                self._add(name, n, today or date.today())

    def give_back(self, name: str, today: date | None = None) -> None:
        """进门时记的那一次没用上（没调用模型），还回去。"""
        self.add(name, -1, today)


def session_from(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"cookie":
            cookie = SimpleCookie()
            try:
                cookie.load(value.decode("latin-1"))
            except Exception:
                return None
            if COOKIE in cookie:
                return cookie[COOKIE].value
    return None


def client_key(headers) -> str:
    """请求来自哪里：Render 会在 X-Forwarded-For 最后加上真实来源 IP（前面的部分用户自己能伪造）。"""
    forwarded = [v for k, v in headers if k.lower() == b"x-forwarded-for"]
    if forwarded:
        last = forwarded[-1].decode("latin-1").split(",")[-1].strip()
        if last:
            return last
    return "unknown"


def is_form(content_type: bytes) -> bool:
    """和 FastAPI、Starlette 一样判断：只有这两种标准类型才按表单读，其他（包括没写类型）都按 JSON 读。"""
    msg = email.message.Message()
    msg["content-type"] = content_type.decode("latin-1")
    return msg.get_content_type() in ("multipart/form-data", "application/x-www-form-urlencoded")


def body_limit(path: str, form: bool) -> int:
    if form:
        return MAX_UPLOAD_BODY if UPLOAD_PATH.match(path) else MAX_SMALL_BODY
    if path == "/api/trips":
        return MAX_INTAKE_BODY
    if path.startswith(("/api/join", "/api/jobs/", "/api/admin/", "/api/logout")):
        return MAX_SMALL_BODY
    if APPLY_PATH.match(path):
        return MAX_INTAKE_BODY   # 核对过的识别结果，几十 KB 足够
    return MAX_JSON_BODY


def _with_headers(send, api: bool):
    extra = SECURITY_HEADERS + (API_HEADERS if api else [])

    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            present = {k.lower() for k, _ in headers}
            headers += [(k, v) for k, v in extra if k not in present]
            message = {**message, "headers": headers}
        await send(message)
    return wrapped


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


async def _drain(receive, most: int) -> None:
    """把请求体读完扔掉（不存），最多读 most 字节。"""
    seen = 0
    while seen <= most:
        message = await receive()
        if message["type"] != "http.request":
            return
        seen += len(message.get("body", b""))
        if not message.get("more_body", False):
            return


class SecurityMiddleware:
    def __init__(self, app, auth: Auth | None = None, quota: Quota | None = None, site_quota: Quota | None = None,
                 beta_code: str | None = None):
        self.app = app
        self.auth = auth
        self.quota = quota            # 每个行程每天
        self.site_quota = site_quota  # 全站每天
        self.beta_code = beta_code
        self._beta_failures: OrderedDict[str, deque[float]] = OrderedDict()   # 来源 → 猜错的时间
        self._beta_site: deque[float] = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        send = _with_headers(send, api=path.startswith("/api/"))
        headers = scope.get("headers", [])
        lengths = [v for k, v in headers if k.lower() == b"content-length"]
        has_body = method in ("POST", "PUT", "PATCH", "DELETE")
        declared = int(lengths[0]) if len(lengths) == 1 and lengths[0].isdigit() else None

        async def reject(status: int, detail: str):
            if has_body and (declared is None or declared <= MAX_DRAIN):
                await _drain(receive, MAX_DRAIN)
            return await _reply(send, status, detail)

        if CONTROL.search(path):
            return await reject(400, "网址不对。")
        state = scope.setdefault("state", {})
        enabled = bool(self.auth and self.auth.enabled)
        session = session_from(scope) if enabled else None
        memberships = await run_in_threadpool(self.auth.memberships, session) if enabled else {}
        state["session"], state["memberships"] = session, memberships
        m = TRIP_PATH.match(path)
        trip_id = unquote(m.group(1)) if m else None

        # ---------- 1. 请求体的类型和大小 ----------
        types = [v for k, v in headers if k.lower() == b"content-type"]
        if len(types) > 1 or len(lengths) > 1:
            return await reject(400, "请求格式不对。")
        form = is_form(types[0]) if types else False
        limit = body_limit(path, form)
        if has_body and lengths and (declared is None or declared > limit):
            return await reject(413, "文件太大，请上传 10MB 以内的文件。" if UPLOAD_PATH.match(path) else "请求内容太大了。")
        if has_body and form and not lengths:
            return await reject(411, "上传时缺少文件大小信息，请换个浏览器再试。")

        # ---------- 2. 新建行程：先看内测邀请码，不读请求体 ----------
        if enabled and path == "/api/trips" and method == "POST":
            if not self.beta_code:
                return await reject(403, "新建行程暂未开放。")
            client = client_key(headers)
            if self._beta_locked(client):
                return await reject(429, "内测邀请码试错次数太多了，请 15 分钟后再试。")
            given = dict((k.lower(), v) for k, v in headers).get(b"x-beta-code", b"").strip()
            if not secrets.compare_digest(given, self.beta_code.encode()):
                self._beta_failed(client)
                return await reject(403, "内测邀请码不对。请找小猪游记团队要邀请码。")

        # ---------- 3. 行程权限和身份 ----------
        member = None
        if enabled and trip_id:
            member = memberships.get(trip_id)
            if member is None:
                return await reject(403 if memberships else 401, "请用发起人发给你的专属邀请链接打开这个行程。")
            state["member_id"] = member
            for pattern, detail in OWN_PATHS:
                pm = pattern.match(path)
                if pm and unquote(pm.group(1)) != member:
                    return await reject(403, detail)
            # 查询参数里的 member_id 换成会话里的成员
            qs = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
            if any(k == "member_id" for k, _ in qs):
                scope["query_string"] = urlencode([(k, member if k == "member_id" else v) for k, v in qs]).encode()

        # ---------- 4. 非表单的请求体：读进来（有上限），把 member_id 换成登录身份 ----------
        # 表单（上传文件）的接口在代码里直接用会话身份；其他请求体 FastAPI 都会当 JSON 读，这里也一样处理。
        if has_body and not form:
            body = b""
            more = True
            while more:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body += message.get("body", b"")
                if len(body) > limit:
                    return await reject(413, "请求内容太大了。")
                more = message.get("more_body", False)
            if member is not None:
                try:
                    data = json.loads(body or b"null")
                except (ValueError, RecursionError):
                    return await _reply(send, 400, "请求格式不对。")
                if isinstance(data, dict) and "member_id" in data:
                    data["member_id"] = member
                    body = json.dumps(data).encode("utf-8")   # 转义成 ASCII：单独的代理字符也能原样交给后面校验
            scope["headers"] = [(k, v) for k, v in headers if k.lower() != b"content-length"] + \
                [(b"content-length", str(len(body)).encode())]
            sent = False
            original = receive

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original()
            receive = replay

        # ---------- 5. 模型额度 ----------
        if trip_id and method == "POST" and any(p.match(path) for p in LLM_PATHS) and (self.quota or self.site_quota):
            return await self._metered(scope, receive, send, trip_id, reject)
        return await self.app(scope, receive, send)

    # ---------- 内测邀请码试错 ----------

    def _prune(self, times: deque, now: float) -> None:
        while times and now - times[0] > BETA_FAILURE_WINDOW_S:
            times.popleft()

    def _beta_locked(self, client: str) -> bool:
        now = time.monotonic()
        self._prune(self._beta_site, now)
        times = self._beta_failures.get(client)
        if times is not None:
            self._prune(times, now)
        return len(self._beta_site) >= BETA_FAILURE_SITE_LIMIT or (times is not None and len(times) >= BETA_FAILURE_LIMIT)

    def _beta_failed(self, client: str) -> None:
        now = time.monotonic()
        self._beta_site.append(now)
        times = self._beta_failures.pop(client, None) or deque()
        times.append(now)
        self._beta_failures[client] = times           # 放到最后：最近出错的来源
        while len(self._beta_failures) > BETA_TRACKED_CLIENTS:
            self._beta_failures.popitem(last=False)   # 记太多来源时，丢掉最久没出错的

    # ---------- 模型额度 ----------

    async def _metered(self, scope, receive, send, trip_id: str, reject):
        """进门前看额度还有没有；之后每调用一次模型扣一次（在调用之前扣，用完就停）。"""
        quotas = [(q, n) for q, n in ((self.quota, trip_id), (self.site_quota, "site")) if q is not None]
        for quota, name in quotas:
            if await run_in_threadpool(quota.used, name) >= quota.daily_limit:
                return await reject(429, quota_text(quota, name))
        token = MODEL_CALLS.set(lambda: charge(quotas))
        try:
            await self.app(scope, receive, send)
        finally:
            MODEL_CALLS.reset(token)


def quota_text(quota: Quota, name: str) -> str:
    who = "今天全站" if name == "site" else "这个行程今天"
    return f"{who}找猪猪的次数用完了（每天 {quota.daily_limit} 次），明天再来吧。"


def charge(quotas: list[tuple[Quota, str]]) -> None:
    """扣一次模型调用。哪个额度用完了就停下，已经扣的退回去。在处理请求的线程里调用。"""
    taken = []
    for quota, name in quotas:
        if not quota.take(name):
            for q, n in taken:
                q.give_back(n)
            raise ModelBudgetExceeded(quota_text(quota, name))
        taken.append((quota, name))
