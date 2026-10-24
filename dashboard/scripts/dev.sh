#!/usr/bin/env bash
# Development run: backend API on 127.0.0.1:8744 in the background, Vite dev server in the foreground.
# No --reload: the backend is a plain uvicorn run from main(), so restart this script to pick up backend changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_BIN="$ROOT/backend/.venv/bin/stagewatch"

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  pnpm --dir "$ROOT/frontend" install --frozen-lockfile
fi
(cd "$ROOT/backend" && uv sync --quiet)

# Started directly (not through uv run) so that $! is the server process itself.
"$BACKEND_BIN" &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT

echo "backend:  http://127.0.0.1:8744/api/health"
echo "frontend: http://127.0.0.1:5191  (proxies /api to the backend)"
pnpm --dir "$ROOT/frontend" dev
