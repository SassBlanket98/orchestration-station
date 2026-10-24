"""Codex station identity agrees with the native rollout identity."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import collect
import native
from model import Snapshot, Stage


class CodexPairing(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.cwd = str(self.home / "project")
        (self.home / "project").mkdir()
        self.codex_home = self.home / ".codex"
        self.rollouts = self.codex_home / "sessions" / "2026" / "10" / "11"
        self.rollouts.mkdir(parents=True)

    def rollout(self, sid: str, started: str, mtime: float) -> None:
        path = self.rollouts / f"rollout-{sid}.jsonl"
        rows = [
            {"type": "session_meta", "timestamp": started,
             "payload": {"id": sid, "cwd": self.cwd, "source": "cli"}},
            {"type": "turn_context", "payload": {"cwd": self.cwd, "model": "gpt-test", "effort": "high"}},
            {"type": "event_msg", "payload": {"detail": "x" * 70000}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    def test_large_rollout_pairs_station_with_its_native_execution(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        self.rollout("lead-one", "2026-10-11T20:00:00Z", started + 30)
        lead = collect._co_Lead(42, "codex", "live", self.cwd, started + 1)

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            meta = collect._co_lead_session(str(self.home / "project" / ".stage"), lead)
        native_rows = native.read_native_executions("codex", str(self.home), started + 60)
        self.assertEqual(len(native_rows), 1)
        record = native_rows[0].record
        station = Stage(path="station", label="project", snap=Snapshot(), lead_pid=42,
                        lead_kind="codex", lead_state="live", session_id=meta["session_id"])
        execution = Stage(path="execution", label="project", snap=Snapshot(),
                          execution_id=record.execution_id, native_id=record.native_id,
                          account=record.account, lead_kind=record.harness)

        rows = collect._co_attach_executions([station, execution])

        self.assertEqual(meta["session_id"], "lead-one")
        self.assertEqual(meta["lead_model"], "gpt-test")
        self.assertEqual(rows, [station])

    def test_newer_same_cwd_rollout_does_not_replace_live_lead_identity(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        self.rollout("lead-one", "2026-10-11T20:00:00Z", started + 30)
        self.rollout("lead-two", "2026-10-11T20:05:00Z", started + 600)
        lead = collect._co_Lead(42, "codex", "live", self.cwd, started + 1)

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            meta = collect._co_lead_session(str(self.home / "project" / ".stage"), lead)
        records = {item.record.native_id: item.record for item in
                   native.read_native_executions("codex", str(self.home), started + 650)}
        station = Stage(path="station", label="project", snap=Snapshot(), lead_pid=42,
                        lead_kind="codex", lead_state="live", session_id=meta["session_id"])
        executions = [Stage(path=f"execution:{sid}", label="project", snap=Snapshot(),
                            execution_id=record.execution_id, native_id=record.native_id,
                            account=record.account, lead_kind=record.harness)
                      for sid, record in records.items()]

        rows = collect._co_attach_executions([station, *executions])

        self.assertEqual(meta["session_id"], "lead-one")
        self.assertEqual(len(rows), 2)
        self.assertIs(rows[0], station)
        self.assertEqual(rows[1].native_id, "lead-two")

    def test_t3_host_process_can_precede_its_session_rollout(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        self.rollout("t3-session", "2026-10-11T20:00:00Z", started + 30)
        lead = collect._co_Lead(42, "t3 code", "background", self.cwd, started - 3600)

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            meta = collect._co_lead_session(str(self.home / "project" / ".stage"), lead)

        self.assertEqual(meta["session_id"], "t3-session")



    def lead_session(self, since: float) -> dict:
        lead = collect._co_Lead(42, "codex", "live", self.cwd, since)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)}):
            return collect._co_lead_session(str(self.home / "project" / ".stage"), lead)

    def test_closest_rollout_wins_among_spawned_workers(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        self.rollout("lead-one", "2026-10-11T20:00:01Z", started + 30)
        self.rollout("worker-a", "2026-10-11T20:00:09Z", started + 31)
        self.rollout("worker-b", "2026-10-11T20:00:12Z", started + 32)
        self.assertEqual(self.lead_session(started)["session_id"], "lead-one")

    def test_tied_rollouts_stay_unpaired(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        self.rollout("one", "2026-10-11T20:00:01Z", started + 30)
        self.rollout("two", "2026-10-11T20:00:01Z", started + 31)
        self.assertEqual(self.lead_session(started)["session_id"], "")

    def test_session_meta_line_over_64k_still_pairs(self) -> None:
        started = datetime(2026, 10, 11, 20, 0, tzinfo=UTC).timestamp()
        path = self.rollouts / "rollout-big.jsonl"
        meta = {"type": "session_meta", "timestamp": "2026-10-11T20:00:00Z",
                "payload": {"id": "big", "cwd": self.cwd, "source": "cli", "dynamic_tools": "t" * 90000}}
        path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
        self.assertEqual(self.lead_session(started + 1)["session_id"], "big")


if __name__ == "__main__":
    unittest.main()
