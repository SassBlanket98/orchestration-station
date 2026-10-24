"""worker-session lineage tests: temporary stage dirs and a scrubbed environment only."""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "tools" / "worker-session"
loader = importlib.machinery.SourceFileLoader("worker_session", str(TOOL))
spec = importlib.util.spec_from_loader("worker_session", loader)
tool = importlib.util.module_from_spec(spec)
loader.exec_module(tool)

_SCRUB = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "OPENCODE_SESSION_ID", "CURSOR_SESSION_ID",
          "CLAUDE_CONFIG_DIR", "CLAUDE_PID", "STATUS_PARENT_SESSION", "STATUS_PARENT_HARNESS", "STATUS_ROOT_SESSION", "STATUS_DEPTH")


class Lineage(unittest.TestCase):
    def run_tool(self, env: dict[str, str], *flags: str) -> tuple[int, str, dict]:
        with tempfile.TemporaryDirectory() as stage:
            clean = {k: v for k, v in os.environ.items() if k not in _SCRUB}
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.dict(os.environ, {**clean, **env}, clear=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = tool.main(["worker-session", *flags, stage, "w1"])
            record = json.loads((Path(stage) / "w1.session").read_text()) if code == 0 else {}
            return code, out.getvalue(), record

    def test_top_level_lead_is_its_own_root(self):
        code, out, rec = self.run_tool({"CLAUDE_CODE_SESSION_ID": "root-1"}, "--env")
        self.assertEqual(code, 0)
        self.assertEqual((rec["lead_session"], rec["root_session"], rec["parent_session"], rec["depth"]), ("root-1", "root-1", None, 0))
        lines = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual(lines, {"STATUS_DEPTH": "1", "STATUS_PARENT_SESSION": "root-1", "STATUS_PARENT_HARNESS": "claude", "STATUS_ROOT_SESSION": "root-1"})

    def test_nested_lead_keeps_root_and_names_parent(self):
        env = {"CODEX_THREAD_ID": "mid-2", "STATUS_PARENT_SESSION": "root-1", "STATUS_PARENT_HARNESS": "claude", "STATUS_ROOT_SESSION": "root-1", "STATUS_DEPTH": "1"}
        _, out, rec = self.run_tool(env, "--env")
        self.assertEqual((rec["lead_session"], rec["parent_session"], rec["root_session"], rec["depth"]), ("mid-2", "root-1", "root-1", 1))
        lines = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual((lines["STATUS_PARENT_SESSION"], lines["STATUS_ROOT_SESSION"], lines["STATUS_DEPTH"]), ("mid-2", "root-1", "2"))

    def test_hidden_own_id_passes_the_inherited_ancestor_down(self):
        # A harness that exposes no session id: lead_session stays null (never faked), the chain still reaches the root.
        env = {"STATUS_PARENT_SESSION": "root-1", "STATUS_PARENT_HARNESS": "claude", "STATUS_ROOT_SESSION": "root-1", "STATUS_DEPTH": "1"}
        _, out, rec = self.run_tool(env, "--env")
        self.assertIsNone(rec["lead_session"])
        self.assertEqual((rec["parent_session"], rec["root_session"]), ("root-1", "root-1"))
        lines = dict(line.split("=", 1) for line in out.splitlines())
        self.assertEqual((lines["STATUS_PARENT_SESSION"], lines["STATUS_PARENT_HARNESS"], lines["STATUS_DEPTH"]), ("root-1", "claude", "2"))

    def test_records_the_launchers_ancestor_pids(self):
        _, _, rec = self.run_tool({})
        if os.getppid() > 1:  # PID-namespace init is intentionally omitted from the ancestry record.
            self.assertIn(os.getppid(), rec["ancestors"])
        self.assertTrue(all(isinstance(p, int) and p > 1 for p in rec["ancestors"]))

    def test_no_ids_anywhere_emits_only_depth(self):
        _, out, rec = self.run_tool({}, "--env")
        self.assertIsNone(rec["lead_session"])
        self.assertIsNone(rec["root_session"])
        self.assertEqual(out.strip(), "STATUS_DEPTH=1")

    def test_unsafe_inherited_ids_are_dropped(self):
        env = {"STATUS_PARENT_SESSION": "x\nSTATUS_ROOT_SESSION=evil", "STATUS_ROOT_SESSION": "a b", "CLAUDE_CODE_SESSION_ID": "ok-1;rm"}
        _, out, rec = self.run_tool(env, "--env")
        self.assertIsNone(rec["parent_session"])
        self.assertIsNone(rec["lead_session"])
        self.assertEqual(out.strip(), "STATUS_DEPTH=1")

    def test_depth_is_bounded_and_garbage_ignored(self):
        _, _, rec = self.run_tool({"STATUS_DEPTH": "99999"})
        self.assertEqual(rec["depth"], 0)
        _, out, _ = self.run_tool({"STATUS_DEPTH": "40"}, "--env")
        self.assertEqual(out.strip(), "STATUS_DEPTH=32")

    def test_plain_mode_prints_no_env_lines(self):
        _, out, _ = self.run_tool({"CLAUDE_CODE_SESSION_ID": "root-1"})
        self.assertNotIn("STATUS_", out)

    def test_old_reader_field_unchanged(self):
        # collect._co_worker_session reads only lead_session; the schema bump must not move it.
        _, _, rec = self.run_tool({"CLAUDE_CODE_SESSION_ID": "root-1"})
        self.assertEqual(rec["lead_session"], "root-1")


if __name__ == "__main__":
    unittest.main()
