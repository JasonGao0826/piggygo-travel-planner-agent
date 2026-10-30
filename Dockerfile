# 小猪游记上线用的镜像（Render 按这个文件构建）。
# 一个容器里跑两个程序：
# - Next.js 网页：对外的端口（Render 给的 $PORT），浏览器只和它打交道；
# - FastAPI 后端：只在容器内部的 127.0.0.1:8765，网页把 /api/... 转给它。
# 数据（数据库、上传的凭证、缓存）放在 /data，这是 Render 的持久硬盘，重新部署不会丢。

# ---------- 第一步：构建网页 ----------
FROM node:22-bookworm-slim AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
ENV NEXT_PUBLIC_API_BASE="" \
    BACKEND_URL=http://127.0.0.1:8765 \
    NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------- 第二步：运行环境 ----------
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    DATA_DIR=/data \
    AUTH_MODE=invite \
    PORT=10000
# 跑 Next.js 只需要 node 这一个程序
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY services/api/requirements.txt services/api/requirements.txt
RUN pip install -r services/api/requirements.txt
# 后端直接从源码目录运行（启动脚本里 --app-dir），按仓库目录找 data/ 里的知识库
COPY services/api/mcp_servers.yaml services/api/
COPY services/api/tripagent services/api/tripagent
COPY data data

COPY --from=web /app/apps/web/.next/standalone apps/web
COPY --from=web /app/apps/web/.next/static apps/web/.next/static
COPY --from=web /app/apps/web/public apps/web/public

COPY scripts/docker-start.sh scripts/docker-start.sh
RUN chmod +x scripts/docker-start.sh && mkdir -p /data

EXPOSE 10000
CMD ["scripts/docker-start.sh"]
