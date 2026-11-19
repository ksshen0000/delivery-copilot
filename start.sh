#!/usr/bin/env bash
# 一键启动交付实施助手。
#
#   ./start.sh          启动，浏览器打开 http://localhost:8000
#   ./start.sh dev      开发模式：后端热重载 + 前端 Vite（http://localhost:5173）
#   PORT=9000 ./start.sh
#
# 首次运行会创建 backend/.env 并提示填写 API key；前端代码有改动时自动重新构建。
set -euo pipefail
cd "$(dirname "$0")"

PORT=${PORT:-8000}
HOST=${HOST:-127.0.0.1}
MODE=${1:-run}

say()  { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*"; exit 1; }

[[ $MODE == run || $MODE == dev ]] || fail "未知参数：$MODE（可用：dev）"
command -v uv >/dev/null || fail "缺少 uv，安装方法见 https://docs.astral.sh/uv/"
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  fail "端口 $PORT 已被占用（可能已经在运行）。换端口：PORT=9000 ./start.sh"
fi

# 1. 配置
if [[ ! -f backend/.env ]]; then
  cp backend/.env.example backend/.env
  chmod 600 backend/.env
  fail "已创建 backend/.env，请填写 LLM_API_KEY 后重新运行 ./start.sh"
fi
if ! grep -Eq '^LLM_API_KEY=[^[:space:]#]+' backend/.env || grep -Eq '^LLM_API_KEY=sk-\.\.\.' backend/.env; then
  warn "backend/.env 中还没有填写 LLM_API_KEY，可以上传和检索文档，但不能对话"
fi

# 2. 前端依赖和构建（只在需要时执行）
frontend_install() {
  command -v pnpm >/dev/null || fail "缺少 pnpm：npm install -g pnpm"
  if [[ ! -d frontend/node_modules || frontend/pnpm-lock.yaml -nt frontend/node_modules/.modules.yaml ]]; then
    say "安装前端依赖…"
    (cd frontend && pnpm install --silent)
  fi
}
if [[ $MODE == dev ]]; then
  frontend_install
else
  stale=$([[ -f frontend/dist/index.html ]] && find frontend/src frontend/index.html frontend/package.json \
    -newer frontend/dist/index.html -print -quit || echo missing)
  if [[ -n $stale ]]; then
    if command -v pnpm >/dev/null; then
      frontend_install
      say "构建前端…"
      out=$(cd frontend && pnpm -s build 2>&1) || { echo "$out"; fail "前端构建失败"; }
    elif [[ $stale == missing ]]; then
      fail "前端还没有构建，需要 pnpm：npm install -g pnpm"
    else
      warn "前端代码有改动但没有 pnpm，继续使用旧的构建"
    fi
  fi
fi

# 3. 启动（uv run 会自动安装和同步后端依赖）
cd backend
if [[ $MODE == dev ]]; then
  (cd ../frontend && API_PORT=$PORT exec pnpm dev --clearScreen false) &
  trap 'kill $! 2>/dev/null' EXIT
  say "开发模式：前端 http://localhost:5173 ，后端 http://$HOST:$PORT"
  uv run uvicorn app.main:create_app --factory --reload --host "$HOST" --port "$PORT"
else
  say "交付实施助手：http://localhost:$PORT  （Ctrl+C 停止）"
  exec uv run uvicorn app.main:create_app --factory --host "$HOST" --port "$PORT" --log-level warning
fi
