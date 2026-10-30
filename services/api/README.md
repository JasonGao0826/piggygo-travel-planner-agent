# 后端

旅行规划助手的后端。设计见 [docs/设计规格.md](../../docs/设计规格.md)。

## 目前有什么

| 模块 | 文件 | 作用 |
|---|---|---|
| 数据结构 | `tripagent/schema/` | 行程（唯一真相源）、景点、决策账本、改动提案、版本、问卷 |
| 校验器 | `tripagent/validator/` | 规则 R01–R26，用代码检查行程（规格第 11 节）；R21–R24 和实时数据、公众假日比对 |
| 改动与版本 | `tripagent/state.py`、`tripagent/service.py` | 提出改动 → 校验 → 确认 → 新版本；锁定保护；并发冲突；决策账本 |
| 车程 | `tripagent/routing.py` | 先查人工校准车程表（`data/routing/`），再用 OSRM 按坐标计算并缓存 |
| 存储 | `tripagent/store.py` | 内存存储（测试用）和数据库存储（本地 SQLite，正式环境 PostgreSQL） |
| 后端接口 | `tripagent/api/` | FastAPI：行程、报告、对话、改动确认、凭证上传、从清单生成行程 |
| 凭证识别、凭证夹 | `tripagent/evidence.py`、`tripagent/vault.py` | 从截图、PDF 或确认邮件（.eml）识别预订信息；原文件存进凭证夹，大家都能看，随行程包下载 |
| 首次生成 | `tripagent/planner/` | 从信息清单生成整份行程：模型选路线和景点，代码排时间线，校验器把关 |
| 景点知识库 | `tripagent/kb.py`、`data/kb/nz/places.yaml` | 读取经官网核实的景点资料 |
| 回放测试数据 | `tripagent/evals.py`、`tests/eval/replay_nz.yaml` | 新西兰案例的 13 步多轮修改（规格附录 A） |
| Word 报告 | `tripagent/report/` | 从行程数据生成可编辑的 Word 报告：原生表格、按地点配色、横向 A4（规格 17.2、17.3 节） |
| 模型适配层 | `tripagent/llm/` | 统一的模型接口；目前实现了 OpenAI（Responses API），另有测试用的剧本模型 |
| 对话 agent | `tripagent/agent/` | 对话修改流程：查记忆 → 调用工具 → 提出改动 → 用户确认（规格第 9、10、12 节） |
| MCP 网关 | `tripagent/mcp_gateway.py`、`mcp_servers.yaml` | 连接外部 MCP 服务（Kiwi 机票、Frankfurter 汇率、Exa 搜索）：白名单、指纹、缓存（规格 22.3 节） |
| 实时数据 | `tripagent/live/` | NZTA 路况、MetService 预警、Open-Meteo 预报、公众假日；后台定时刷新（规格 22.4 节） |
| 预订辅助 | `tripagent/planner/enrich.py`、`tripagent/links.py` | 生成时给还没订的航班找真实候选，给住宿附搜索链接（规格 22.5 节） |
| 日历导出 | `tripagent/calendar.py` | 导出 .ics，导入手机日历后有提醒 |
| 账本 | `tripagent/money/`、`tripagent/agent/money_tools.py` | 记账、多币种折算、零头分配、最少转账结算、情侣合并、还款、导出（规格第 14 节） |
| 出发前清单 | `tripagent/checklist.py` | 每天要带的东西、出发前清单、待确认事项（规则 R26） |
| 小红书攻略 | `tripagent/guides.py` | 读取分享链接或截图，提取地点和建议，对应到知识库（规格 15.4 节） |
| 多人协作 | `tripagent/collab/`、`tripagent/agent/collab_tools.py` | 每人偏好、意见汇总（代码比数值，模型判断项目冲突）、投票、动态流（规格第 13 节） |
| 登录 | `tripagent/auth.py`、`tripagent/api/security.py` | 每人专属邀请链接、会话 cookie；按登录身份认人、只能看加入的行程；内测邀请码、每日模型额度（规格 13.2 节） |
| 文件位置 | `tripagent/paths.py` | 知识库在仓库的 `data/`；数据库、上传的文件、缓存在 `DATA_DIR`（上线时是持久硬盘） |

上线用的设置都是环境变量，说明在 `tripagent/api/main.py` 开头；部署步骤见 [docs/上线指南.md](../../docs/上线指南.md)。还没做：正式部署（等注册账号）、待办认领。

## 运行测试

下面的命令都是 Windows PowerShell 写法，一行一行运行（PowerShell 5 不支持 `&&`）。

第一次需要建虚拟环境（需要 Python 3.11 以上）：

```powershell
cd services\api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

之后每次运行：

```powershell
cd services\api
.\.venv\Scripts\python.exe -m pytest -q
```

## 测试数据

`tests/fixtures/` 是按新西兰计划 V8 整理的冻结数据：

- `nz_trip_v8.yaml`：10 天行程，105 个事件。文件开头列出了原资料没写清楚、做了假设的地方。
- `nz_places.yaml`：测试用景点数据（取自 V3 报告，固定不变，保证测试结果稳定）。
- `nz_drive_times.yaml`：测试用静态车程表。
- `nz_decisions.yaml`：决策账本（骑马必须在格林诺奇、峡湾改自驾、不去萤火虫洞、必玩项目）。

## 启动后端接口

```powershell
cd services\api
.\.venv\Scripts\python.exe -m uvicorn tripagent.api.main:app --port 8765
```

接口文档在 http://localhost:8765/docs 。数据存在 `services/api/data/`（不进 git）。

## 从信息清单生成行程（会产生 API 费用，约 0.3–1 美元）

```powershell
cd services\api
.\.venv\Scripts\python.exe scripts\try_generate.py
```

默认用回放测试里的新西兰清单；也可以传入自己的清单文件（TripIntake 格式）。路线框架会自动确认，生成的 Word 在 `output/` 下。

## 配置模型

复制 `.env.example` 为 `.env`，填入 `OPENAI_API_KEY`。`.env` 不进 git，不要把 key 发到聊天里或提交到仓库。

## 和 agent 对话（会产生 API 费用）

```powershell
cd services\api
.\.venv\Scripts\python.exe scripts\chat.py
```

用新西兰 V8 行程试用。改动要输入"确认"才会生效；`/报告` 生成当前版本的 Word，`/版本` 查看版本历史。数据目前存在内存里，退出后不保留。

## 跑真实模型的场景测试（会产生 API 费用）

```powershell
cd services\api
.\.venv\Scripts\python.exe scripts\\try_scenarios.py
```

5 个场景：问答、否决过的方案、上传凭证、新决定、和航班冲突的要求。会打印 agent 调用了哪些工具、回复和费用，并自动检查结果。普通的 `pytest` 不联网、不花钱。

## 生成 Word 报告

```powershell
cd services\api
.\.venv\Scripts\python.exe scripts\\render_report.py
```

默认用新西兰 V8 测试行程，生成到仓库根目录的 `output/新西兰行程报告.docx`（`output/` 不纳入 git）。报告内容先整理成 `tripagent/report/model.py` 里的章节和表格，再由 `docx_render.py` 写成 Word，测试直接检查内容。

## 导出 JSON Schema

```powershell
cd services\api
.\.venv\Scripts\python.exe scripts\\export_schema.py
```

导出到 `packages/schema/`，之后前端用它生成 TypeScript 类型。
