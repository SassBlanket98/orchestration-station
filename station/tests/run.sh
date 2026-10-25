#!/usr/bin/env bash
# Run the suite. The OpenClaw tests need the optional integration switched on (it is off by default).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# Fixtures hold wall-clock times and macOS `ps` output recorded at UTC+2 (POSIX names flip the sign).
export TZ=Etc/GMT-2
# Pin the environment so results do not depend on the caller: no inherited repo (a hook's GIT_DIR would send the
# tests' git calls to the real repo), no user/system git config, a scratch HOME, and colour on.
unset $(git rev-parse --local-env-vars) NO_COLOR FORCE_COLOR COLUMNS LINES
home="$(mktemp -d)"; trap 'rm -rf "$home"' EXIT
export HOME="$home" GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 TERM=xterm-256color
export STATION_OPENCLAW_AGENT="${STATION_OPENCLAW_AGENT:-openclaw}" STATION_OPENCLAW_PEERS="${STATION_OPENCLAW_PEERS:-test-peer}"
PYTHONPATH=../src python3 -m unittest "$@"
