# 网页前端（apps/web）

旅行规划助手的网页。Next.js（App Router）+ TypeScript + Tailwind，手机优先，桌面也能用。
网页只调用后端接口（`services/api`），业务逻辑都在后端。

## 页面

| 地址 | 内容 |
|---|---|
| `/` | 我的行程：行程列表、载入新西兰演示行程、新建行程 |
| `/trips/<id>` | 行程主页：总览 / 每日 / 项目 / 住宿与交通 / 待办 / 版本，右侧（手机上是“对话”页签）和助手对话、确认或撤回改动、上传凭证、下载 Word |
| `/new` | 新建行程问卷（行程信息 → 成员 → 已有预订 → 确认提交），草稿自动存在本机 |
| `/jobs/<jobId>` | 生成进度，每 3 秒刷新；需要补充信息时可以直接回答 |

页签内容直接用后端的报告模型（`GET /api/trips/<id>/report`）渲染，和 Word 报告是同一份内容。
后端还没开放的接口（上传凭证、新建行程、生成进度）会显示“功能即将上线”。

## 第一次安装

需要 Node.js 20 以上。下面是 Windows PowerShell 写法，一行一行运行：

```powershell
cd apps\web
npm install
```

## 开发运行

先按 `services/api/README.md` 启动后端（默认 `http://localhost:8765`），再开一个 PowerShell 窗口：

```powershell
cd apps\web
npm run dev
```

浏览器打开 <http://localhost:3000>。后端只允许 `http://localhost:3000` 跨域访问，所以前端要用 3000 端口。

后端不在默认地址时，先设置环境变量再启动：

```powershell
$env:NEXT_PUBLIC_API_BASE = "http://localhost:8765"
npm run dev
```

也可以在 `apps\web` 下新建 `.env.local`，写一行 `NEXT_PUBLIC_API_BASE=http://localhost:8765`（这个文件不进 git）。

## 检查和打包

```powershell
cd apps\web
npm run lint
npm run build
npm run start
```

## 代码结构

| 位置 | 作用 |
|---|---|
| `lib/api.ts` | 接口类型（按 `packages/schema` 手写）和调用函数 |
| `lib/tabs.ts` | 报告章节 → 页签的对应规则（按标题关键词，不认识的放进总览） |
| `components/report.tsx` | 报告内容块：表格（手机上在容器里横向滚动，保留行底色）、卡片、标题、列表 |
| `components/TripView.tsx` | 行程主页 |
| `components/ChatPanel.tsx`、`PatchCard.tsx` | 对话和改动提案卡片 |
| `components/EvidenceDialog.tsx` | 上传凭证、核对识别结果、应用 |
| `components/IntakeWizard.tsx` | 新建行程问卷 |
| `components/JobView.tsx` | 生成进度 |

“我是谁”按行程保存在浏览器的 localStorage 里；读写失败（例如隐私模式）不影响使用。
