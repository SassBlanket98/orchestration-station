"""An OpenClaw session's latest actions from its transcript: the newest meaningful events, oldest first, read-only."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace

import collect
import render
from model import Snapshot, Stage

SESSION = "agent:openclaw:main"
CURRENT = "sess-current"


def _msg(seq: int, stamp: str, role: str, content: object) -> tuple[int, str, str]:
    event = {"type": "message", "timestamp": stamp, "message": {"role": role, "content": content}}
    return seq, json.dumps(event), stamp


class OpenClawDoingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "openclaw-agent.sqlite")
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE session_nodes (session_key TEXT, current_session_id TEXT)")
        conn.execute("CREATE TABLE transcript_events (session_id TEXT, seq INTEGER, event_json TEXT, created_at TEXT, event_zstd BLOB)")
        conn.execute("INSERT INTO session_nodes VALUES (?, ?)", (SESSION, CURRENT))
        conn.execute("INSERT INTO session_nodes VALUES (?, ?)", ("agent:openclaw:dashboard:x", "sess-other"))
        rows = [
            _msg(1, "2026-10-11T22:00:00.000Z", "user", "Please fix the build\nand more detail"),
            _msg(2, "2026-10-11T22:00:01.000Z", "assistant", [{"type": "toolCall", "name": "Read", "arguments": {"path": "/x/render.py"}}]),
            _msg(3, "2026-10-11T22:00:02.000Z", "assistant", [{"type": "thinking", "thinking": "hmm"}]),
            _msg(4, "2026-10-11T22:00:03.000Z", "assistant", [{"type": "text", "text": "Looking into it\nsecond line"}]),
            _msg(5, "2026-10-11T22:00:04.000Z", "toolResult", [{"type": "text", "text": "output"}]),
            _msg(6, "2026-10-11T22:00:05.000Z", "assistant", [{"type": "toolCall", "name": "exec", "arguments": {"command": "ls -la\nx"}}]),
            _msg(7, "2026-10-11T22:00:06.000Z", "assistant", [{"type": "toolCall", "name": "Edit", "arguments": {"file_path": "/src/render.py"}}]),
            (8, json.dumps({"type": "custom", "timestamp": "2026-10-11T22:00:07.000Z", "data": {}}), ""),
            _msg(9, "2026-10-11T22:00:08.000Z", "assistant", [{"type": "text", "text": "   "}]),
            _msg(10, "2026-10-11T22:00:09.000Z", "user", "now run tests"),
            _msg(11, "2026-10-11T22:00:10.000Z", "assistant", [{"type": "toolCall", "name": "Bash", "arguments": {}}]),
            (12, "{not json", ""),
            (13, None, ""),
        ]
        for seq, event_json, _ in rows:
            conn.execute("INSERT INTO transcript_events VALUES (?, ?, ?, ?, ?)", (CURRENT, seq, event_json, "", None))
        # an old event kept only compressed: its event_json is null and must be skipped
        conn.execute("INSERT INTO transcript_events VALUES (?, ?, ?, ?, ?)", (CURRENT, 14, None, "", b"\x00\x01"))
        conn.execute("INSERT INTO transcript_events VALUES (?, ?, ?, ?, ?)", ("sess-other", 1, json.dumps(
            {"type": "message", "timestamp": "2026-10-11T23:00:00.000Z", "message": {"role": "user", "content": "elsewhere"}}), "", None))
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_newest_five_actions_oldest_first(self) -> None:
        got = collect._co_openclaw_doing(self.db, SESSION)
        texts = [text for _, text in got]
        self.assertEqual(texts, ["Looking into it", "exec ls -la", "Edit render.py", "asked: now run tests", "Bash"])
        stamps = [ts for ts, _ in got]
        self.assertEqual(stamps, sorted(stamps))
        self.assertAlmostEqual(stamps[0], collect._co_parse_iso("2026-10-11T22:00:03.000Z"))
        self.assertTrue(all(len(text) <= 70 for text in texts))

    def test_limit_and_long_text_clipped(self) -> None:
        conn = sqlite3.connect(self.db)
        long_line = "w" * 200
        conn.execute("INSERT INTO transcript_events VALUES (?, ?, ?, ?, ?)", (CURRENT, 20, json.dumps(
            {"type": "message", "timestamp": "2026-10-11T22:01:00.000Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": long_line}]}}), "", None))
        conn.commit()
        conn.close()
        got = collect._co_openclaw_doing(self.db, SESSION, limit=2)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][1], "Bash")
        self.assertEqual(got[1][1], "w" * 69 + "…")
        self.assertEqual(len(got[1][1]), 70)

    def test_missing_db_or_session_gives_empty(self) -> None:
        self.assertEqual(collect._co_openclaw_doing(os.path.join(self.tmp.name, "nope.sqlite"), SESSION), [])
        self.assertEqual(collect._co_openclaw_doing(self.db, "agent:openclaw:unknown"), [])

    def test_corrupt_json_is_skipped(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO transcript_events VALUES (?, ?, ?, ?, ?)", (CURRENT, 30, "[1, 2", "", None))
        conn.commit()
        conn.close()
        self.assertEqual(len(collect._co_openclaw_doing(self.db, SESSION)), 5)

    def test_render_message_for_openclaw_and_codex(self) -> None:
        stage = Stage(path="x", label="Bot", snap=Snapshot(title="Bot", now=0.0), lead_kind="openclaw", tag="OClaw")
        text = "\n".join(render._rd_g_doing_lines(stage, 200, 0.0))
        self.assertIn("nothing in its transcript yet", text)
        codex = replace(stage, lead_kind="codex", tag="CDX")
        text = "\n".join(render._rd_g_doing_lines(codex, 200, 0.0))
        self.assertIn("no readable activity", text)
        self.assertIn("OpenClaw", text)


if __name__ == "__main__":
    unittest.main()
