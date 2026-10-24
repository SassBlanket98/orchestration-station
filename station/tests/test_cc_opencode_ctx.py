"""OpenCode model windows and latest assistant input metrics."""
from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class OpenCodeContext(unittest.TestCase):
    def test_windows_input_tokens_and_cache(self) -> None:
        native = importlib.import_module("native")
        with tempfile.TemporaryDirectory() as home:
            db = Path(home) / ".local/share/opencode/opencode.db"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, directory TEXT, title TEXT, model TEXT, time_archived INT, time_updated INT)")
            conn.execute("CREATE TABLE session_message (session_id TEXT, type TEXT, seq INT, data TEXT)")
            for sid, model in (("known", "claude-sonnet-5-5"), ("unknown", "some-new-model")):
                conn.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,NULL,1000)",
                             (sid, None, "/tmp", sid, json.dumps({"id": model})))
                conn.execute("INSERT INTO session_message VALUES (?,?,?,?)",
                             (sid, "assistant", 1, json.dumps({"tokens": {"input": 1234}})))
            conn.commit()
            native._metric_opencode_db.clear()
            first = native.read_opencode_sessions(home)
            self.assertEqual(first["known"]["ctx_window"], 200_000)
            self.assertIsNone(first["unknown"]["ctx_window"])
            self.assertEqual(first["known"]["ctx_tokens"], 1234)
            with mock.patch.object(native.sqlite3, "connect", side_effect=AssertionError("cache miss")):
                self.assertIs(native.read_opencode_sessions(home), first)
            conn.close()
            native._metric_opencode_db.clear()

    def test_one_megabyte_claude_model(self) -> None:
        native = importlib.import_module("native")
        self.assertEqual(native._nv_opencode_window("claude-haiku-5-5-1m"), 1_000_000)


if __name__ == "__main__":
    unittest.main()
