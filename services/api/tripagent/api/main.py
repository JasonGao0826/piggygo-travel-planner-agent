"""运行入口：uvicorn tripagent.api.main:app --reload（在 services/api 目录下）。

上线时的设置都从环境变量读（本地开发写在 services/api/.env，上线写在 Render 的 Environment 页面）：
- AUTH_MODE：open（本地开发，不登录）/ invite（上线，用专属邀请链接登录）
- BETA_CODE：新建行程要填的内测邀请码（invite 模式下不设就不能新建行程）；ADMIN_TOKEN：管理页面的口令
- DAILY_LLM_CALLS_PER_TRIP：每个行程每天能调用模型的次数（invite 模式默认 300，open 模式不限；问猪猪一次通常要调用 2–6 次）
- DAILY_LLM_CALLS_TOTAL：全站每天能调用模型的次数（invite 模式默认 3000）
- DAILY_NEW_TRIPS：全站每天最多新建几个行程（invite 模式默认 10，open 模式不限）
- DATA_DIR：数据库、上传的凭证、缓存放在哪里（上线时是持久硬盘）
- COOKIE_SECURE：1 表示登录 cookie 只走 HTTPS（invite 模式默认 1；本地用 http 试运行时设 0）
"""

import logging
import os

from dotenv import load_dotenv

from ..auth import Auth
from ..cache import SqlCache
from ..kb import load_kb
from ..live.monitor import LiveMonitor
from ..live.service import LiveService
from ..llm import ENV_FILE, load_llm
from ..mcp_gateway import MCPGateway, load_servers
from ..paths import API_DIR, DATA_DIR, KB_DATA, UPLOADS
from ..routing import CompositeDriveTimes, OsrmDriveTimes, StaticDriveTimes
from ..service import TripService
from ..store import SqlStore
from .app import create_app

load_dotenv(ENV_FILE)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
DATA_DIR.mkdir(parents=True, exist_ok=True)

places = load_kb()
drive_times = CompositeDriveTimes(
    StaticDriveTimes.from_yaml(KB_DATA / "routing" / "nz_drive_times.yaml"),
    OsrmDriveTimes(places, cache_path=DATA_DIR / "osrm_cache.json", geometry_path=DATA_DIR / "osrm_geometry.json"),
)
store = SqlStore()
cache = SqlCache(store.engine)
# 外部 MCP 服务（机票、汇率、网页搜索）和实时数据（路况、天气）
gateway = MCPGateway(load_servers(API_DIR / "mcp_servers.yaml"), cache=cache, pins_path=DATA_DIR / "mcp_pins.json")
service = TripService(store, places, drive_times, live=LiveService(cache))
mode = os.getenv("AUTH_MODE", "open")
invite = mode == "invite"
if not invite and os.getenv("RENDER"):   # Render 上一定要登录：不登录谁都能看到所有行程
    raise SystemExit("在 Render 上运行时 AUTH_MODE 必须是 invite（现在是 %s）" % mode)
site_limit = os.getenv("DAILY_LLM_CALLS_TOTAL", "3000" if invite else "")
llm_limit = os.getenv("DAILY_LLM_CALLS_PER_TRIP", "300" if invite else "")
trip_limit = os.getenv("DAILY_NEW_TRIPS", "10" if invite else "")
for key in ("ADMIN_TOKEN", "BETA_CODE"):
    if invite and not os.getenv(key):
        logging.getLogger("tripagent").warning("没有设置 %s：%s", key,
                                               "管理页面用不了" if key == "ADMIN_TOKEN" else "新建行程不开放")
app = create_app(
    service, llm_factory=load_llm, uploads_dir=UPLOADS, gateway=gateway,
    auth=Auth(store.engine, mode),
    beta_code=os.getenv("BETA_CODE") or None, admin_token=os.getenv("ADMIN_TOKEN") or None,
    daily_llm_limit=int(llm_limit) if llm_limit else None, daily_trip_limit=int(trip_limit) if trip_limit else None,
    daily_site_llm_limit=int(site_limit) if site_limit else None,
    cookie_secure=os.getenv("COOKIE_SECURE", "1" if invite else "0") == "1",
)
LiveMonitor(service).start()  # 出发前 10 天起，每 15 分钟刷新一次路况和天气
