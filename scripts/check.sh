#!/usr/bin/env bash
# Run the same checks as CI (.github/workflows/ci.yml) before pushing.
# Usage: scripts/check.sh [station|backend|frontend|workflows ...]   (default: all)
# Keep this in step with ci.yml: a check added there belongs here too.
set -euo pipefail
# A pre-push hook exports GIT_DIR, GIT_INDEX_FILE etc.; left set they aim every git call (tests included) at this repo.
unset $(git rev-parse --local-env-vars)

root="$(cd "$(dirname "$0")/.." && pwd)"
parts=("$@")
[ ${#parts[@]} -eq 0 ] && parts=(station backend frontend workflows)
failed=()

step() { printf '\n== %s\n' "$1"; }
run() { local name="$1"; shift; step "$name"; if ! "$@"; then failed+=("$name"); fi; }

station() {
  cd "$root/station"
  run "station: ruff" uvx ruff@0.17.2 check src tests build.py
  run "station: tests" bash tests/run.sh
  run "station: built file up to date" bash -c 'python3 build.py && git diff --exit-code -- station'
}
backend() {
  cd "$root/dashboard/backend"
  run "backend: sync" uv sync --locked
  run "backend: ruff" uv run ruff check src tests
  run "backend: pytest" uv run pytest -q
}
frontend() {
  cd "$root/dashboard/frontend"
  run "frontend: install" pnpm install --frozen-lockfile
  run "frontend: typecheck" pnpm typecheck
  run "frontend: test" pnpm test
  run "frontend: build" pnpm build
}
workflows() {
  # actionlint catches broken workflow YAML before GitHub does. Optional locally.
  if command -v actionlint >/dev/null; then
    cd "$root"
    run "workflows: actionlint" actionlint
  else
    step "workflows: actionlint not installed, skipped"
  fi
}

for p in "${parts[@]}"; do
  case "$p" in
    station|backend|frontend|workflows) "$p" ;;
    *) echo "unknown part: $p" >&2; exit 2 ;;
  esac
done

if [ ${#failed[@]} -gt 0 ]; then
  printf '\nFAILED:\n'; printf '  %s\n' "${failed[@]}"; exit 1
fi
printf '\nAll checks passed.\n'
