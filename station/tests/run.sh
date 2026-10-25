#!/usr/bin/env bash
# Run the suite. The OpenClaw tests need the optional integration switched on (it is off by default).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# Fixtures hold wall-clock times and macOS `ps` output recorded at UTC+2. POSIX form (sign flipped) needs no zone database, which macOS runners can lack.
export TZ=UTC-2
export STATION_OPENCLAW_AGENT="${STATION_OPENCLAW_AGENT:-openclaw}" STATION_OPENCLAW_PEERS="${STATION_OPENCLAW_PEERS:-test-peer}"
PYTHONPATH=../src exec python3 -m unittest "$@"
