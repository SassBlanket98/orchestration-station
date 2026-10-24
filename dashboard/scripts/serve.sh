#!/usr/bin/env bash
# Build the SPA, then run the dashboard server on 127.0.0.1:<port> (default 8744).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  pnpm --dir "$ROOT/frontend" install --frozen-lockfile
fi
pnpm --dir "$ROOT/frontend" build

cd "$ROOT/backend"
exec uv run stagewatch
