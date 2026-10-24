from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import collect
import native


class TestingHomes(unittest.TestCase):
    def write_claude(self, home: str, directory: str, sid: str) -> None:
        folder = Path(home) / directory / "projects/project"
        folder.mkdir(parents=True)
        (folder / f"{sid}.jsonl").write_text(json.dumps({"type": "user", "sessionId": sid, "cwd": "/tmp/project",
                                                        "message": {"role": "user", "content": "hello"}}) + "\n")

    def test_native_scans_testing_harness_homes(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            self.write_claude(home, ".claude-testing", "testing-personal")
            self.write_claude(home, ".claude-team-work-testing", "testing-work")
            codex_dir = Path(home) / ".codex-testing/sessions/2026/10/12"
            codex_dir.mkdir(parents=True)
            (codex_dir / "rollout-testing.jsonl").write_text(json.dumps({"type": "session_meta", "payload": {
                "id": "testing-codex", "cwd": "/tmp/project", "source": "cli"}}) + "\n")

            with mock.patch.dict(os.environ, {"STATION_CLAUDE_CONFIG_DIRS": ".claude-testing:.claude-team-work-testing"}):
                claude = native.read_native_executions("claude", home, 0)
            codex = native.read_native_executions("codex", home, 0)
            found = {item.record.native_id: item.record for item in claude + codex}
            self.assertEqual(found["testing-personal"].harness, "claude")
            self.assertEqual(found["testing-personal"].account, "personal")
            self.assertEqual(found["testing-work"].account, "work")
            self.assertEqual(found["testing-codex"].harness, "codex")

    def test_claude_testing_config_classification(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            for config, expected in ((".claude-testing", "claude"),
                                     (".claude-team-work-testing", "claude work"),
                                     (".claude-team-work", "claude work"), (None, "claude")):
                environ = {"CLAUDE_CONFIG_DIR": os.path.join(home, config)} if config else {}
                self.assertEqual(collect._co_lead_kind(["claude"], home, home, tty=True, environ=environ), expected)

    def test_testing_lead_tags_follow_their_account(self) -> None:
        lead = collect._co_Lead(123, "claude", "live", "/tmp/project", 0)
        for config, expected in (("/tmp/.claude-testing", "CC·P"),
                                 ("/tmp/.claude-team-work-testing", "CC·W")):
            with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": config}):
                self.assertEqual(collect._co_lead_session("/tmp/stage", lead)["tag"], expected)


if __name__ == "__main__":
    unittest.main()
