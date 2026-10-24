"""Test fixtures opt into the configured OpenClaw integration used by its dedicated tests."""
from __future__ import annotations

import os

os.environ["STATION_OPENCLAW_AGENT"] = "openclaw"
os.environ["STATION_OPENCLAW_PEERS"] = "test-peer"
