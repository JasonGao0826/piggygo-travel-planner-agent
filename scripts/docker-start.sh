#!/usr/bin/env bash
# 容器启动脚本（Dockerfile 的 CMD）：先启动后端，等它就绪，再启动网页。
# 任何一个程序退出，整个容器就退出，Render 会自动重启。
# 收到停止信号时，先让两个程序正常收尾（处理完手上的请求）再退出。
# 后端只能开一个进程：生成行程的后台任务记在进程里。
set -uo pipefail
cd /app
mkdir -p "${DATA_DIR:-/data}"

stop() {
  kill -TERM $(jobs -p) 2>/dev/null
  wait
  exit 0
}
trap stop TERM INT

python -m uvicorn tripagent.api.main:app --app-dir services/api --host 127.0.0.1 --port 8765 &

for _ in $(seq 1 90); do
  python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=2)' 2>/dev/null && break
  sleep 1
done

# Next.js 默认只听容器的主机名，要改成所有网卡，Render 才连得上
HOSTNAME=0.0.0.0 PORT="${PORT:-10000}" node apps/web/server.js &

wait -n
status=$?
kill -TERM $(jobs -p) 2>/dev/null
wait
exit "$status"
