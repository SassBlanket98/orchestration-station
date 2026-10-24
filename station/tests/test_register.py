"""Producer tests use fake child programs, not model calls."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class Producer(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("register"), "launch/lifecycle producer is missing")
        return importlib.import_module("register")

    def test_brief_exists_before_child_starts(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            meta = {"schema": 1, "execution_id": "a" * 64, "harness": "other", "role": "worker",
                    "goal": "synthetic launch", "brief": "the complete assigned brief"}
            command = [sys.executable, "-c", "from pathlib import Path; import sys; assert Path(sys.argv[1]).read_text() == 'the complete assigned brief'",
                       str(Path(root) / "artifacts" / ("a" * 64) / "brief.txt")]
            self.assertEqual(api.launch_execution(root, meta, command), 0)
            record = api.read_registry(root).records[0]
            self.assertEqual(record.outcome, "success")
            self.assertEqual(record.exit_code, 0)
            self.assertTrue(record.ended)

    def test_nonzero_exit_is_failed_without_report(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            meta = {"schema": 1, "execution_id": "b" * 64, "harness": "other", "role": "worker", "brief": "fail deliberately"}
            self.assertEqual(api.launch_execution(root, meta, [sys.executable, "-c", "raise SystemExit(7)"]), 7)
            self.assertEqual(api.read_registry(root).records[0].outcome, "failed")

    def test_failed_child_start_is_not_phantom_running(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            meta = {"schema": 1, "execution_id": "e" * 64, "harness": "other", "role": "worker", "brief": "launch"}
            self.assertNotEqual(api.launch_execution(root, meta, ["/nonexistent-status-test-executable"]), 0)
            record = api.read_registry(root).records[0]
            self.assertEqual(record.outcome, "failed")
            self.assertEqual(record.state, "finished")

    def test_cursor_registry_failure_does_not_block_harness(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            child = subprocess.run([sys.executable, api.__file__, "--root", root, "ingest", "--harness", "cursor"],
                                   input=json.dumps({"session_id": "s", "hook_event_name": "sessionStart"}),
                                   text=True, capture_output=True, env={**os.environ, "STATUS_EXECUTION_ID": "bad"}, check=False)
            self.assertEqual(json.loads(child.stdout), {"permission": "allow", "continue": True})

    def test_oversized_brief_is_rejected_not_silently_truncated(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError):
            api.write_execution(root, {"schema": 1, "execution_id": "f" * 64, "harness": "codex",
                                       "brief": "x" * (16 * 1024 * 1024 + 1)}, "start")

    def test_late_start_cannot_reverse_completion_or_erase_brief(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            base = {"schema": 1, "execution_id": "c" * 64, "harness": "codex", "native_id": "worker", "role": "worker"}
            api.write_execution(root, {**base, "brief": "original", "goal": "original goal", "started": 10}, "start")
            api.write_execution(root, {**base, "ended": 20, "outcome": "success", "state": "finished"}, "finish")
            api.write_execution(root, {**base, "started": 11, "brief": "replacement"}, "start")
            record = api.read_registry(root).records[0]
            self.assertEqual(record.outcome, "success")
            self.assertEqual(record.started, 10)
            self.assertEqual(api.read_execution_artifact(root, record.execution_id, "brief"), ["original"])

    def test_hook_stop_is_turn_idle_not_session_completion(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            api.ingest_hook(root, "claude", "work", {"session_id": "lead", "hook_event_name": "UserPromptSubmit", "prompt": "inspect module", "cwd": "/tmp"})
            api.ingest_hook(root, "claude", "work", {"session_id": "lead", "hook_event_name": "Stop"})
            record = api.read_registry(root).records[0]
            self.assertEqual(record.state, "idle")
            self.assertEqual(record.outcome, "unknown")
            self.assertEqual(record.goal, "inspect module")

    def test_concurrent_activity_keeps_complete_record(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root:
            meta = {"schema": 1, "execution_id": "d" * 64, "harness": "codex", "role": "lead", "brief": "preserved"}
            api.write_execution(root, meta, "start")
            code = "import register,sys; register.write_execution(sys.argv[1], {'schema':1,'execution_id':'d'*64,'harness':'codex','role':'lead','activity':42}, 'activity')"
            procs = [subprocess.Popen([sys.executable, "-c", code, root], stdin=subprocess.DEVNULL) for _ in range(4)]
            for proc in procs:
                self.assertEqual(proc.wait(), 0)
            record = api.read_registry(root).records[0]
            self.assertEqual(record.activity, 42)
            self.assertEqual(api.read_execution_artifact(root, record.execution_id, "brief"), ["preserved"])
            self.assertEqual(json.loads((Path(root) / "records" / ("d" * 64 + ".json")).read_text())["revision"], 5)


class HookEvents(unittest.TestCase):
    """Hook ingestion uses synthetic payloads only; no harness is launched."""

    def api(self):
        return importlib.import_module("register")

    def clean_env(self):
        return mock.patch.dict(os.environ, {k: "" for k in ("STATUS_EXECUTION_ID", "STATUS_PARENT_EXECUTION_ID", "STATUS_ROOT_EXECUTION_ID")})

    def records(self, api, root):
        return {record.execution_id: record for record in api.read_registry(root).records}

    def test_subagent_tool_event_attributes_to_child_not_lead(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root, self.clean_env():
            api.ingest_hook(root, "claude", "personal", {"hook_event_name": "SessionStart", "session_id": "s1"})
            api.ingest_hook(root, "claude", "personal", {"hook_event_name": "PostToolUse", "session_id": "s1", "agent_id": "a1", "tool_name": "Read"})
            lead, child = api.execution_key("claude", "personal", "s1"), api.execution_key("claude", "personal", "a1")
            records = self.records(api, root)
            self.assertEqual(records[child].role, "worker")
            self.assertEqual(records[child].parent_id, lead)
            self.assertEqual(records[child].current_action, "Read")
            self.assertEqual(records[lead].current_action, "SessionStart")

    def test_stop_is_idle_and_later_tool_event_cannot_reverse_finished_child(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as root, self.clean_env():
            api.ingest_hook(root, "codex", "personal", {"hook_event_name": "SessionStart", "session_id": "s2"})
            api.ingest_hook(root, "codex", "personal", {"hook_event_name": "Stop", "session_id": "s2"})
            lead = api.execution_key("codex", "personal", "s2")
            self.assertEqual(self.records(api, root)[lead].state, "idle")
            self.assertIsNone(self.records(api, root)[lead].ended)
            api.ingest_hook(root, "claude", "personal", {"hook_event_name": "SubagentStop", "session_id": "s2", "agent_id": "a2", "status": "completed"})
            api.ingest_hook(root, "claude", "personal", {"hook_event_name": "PostToolUse", "session_id": "s2", "agent_id": "a2", "tool_name": "Bash"})
            child = self.records(api, root)[api.execution_key("claude", "personal", "a2")]
            self.assertEqual((child.state, child.outcome), ("finished", "success"))

    def test_lead_pid_from_payload_records_start_identity(self):
        api = self.api()
        if not os.path.exists("/proc/self/stat"):
            self.skipTest("no procfs")
        with tempfile.TemporaryDirectory() as root, self.clean_env():
            api.ingest_hook(root, "codex", "personal", {"hook_event_name": "SessionStart", "session_id": "s3", "pid": os.getpid()})
            record = self.records(api, root)[api.execution_key("codex", "personal", "s3")]
            self.assertEqual(record.pid, os.getpid())
            self.assertIsNotNone(record.pid_start)

    def test_cursor_usage_error_still_answers_neutral(self):
        api = self.api()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = api.register_main(["--root", root, "ingest", "--harness", "cursor", "--account"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"permission": "allow", "continue": True})

    def test_cursor_equals_form_usage_error_answers_neutral(self):
        api = self.api()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = api.register_main(["--root", root, "ingest", "--harness=cursor", "--account"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"permission": "allow", "continue": True})

    def test_cursor_malformed_payload_answers_neutral(self):
        api = self.api()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root, mock.patch("sys.stdin", io.StringIO("not json")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = api.register_main(["--root", root, "ingest", "--harness", "cursor"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"permission": "allow", "continue": True})


class HardenedProducer(unittest.TestCase):
    """Regression tests for reviewer findings on the hook producer."""

    def api(self):
        return importlib.import_module("register")

    def test_intermediate_symlink_component_is_refused(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "registry"
            target = Path(tmp) / "elsewhere"
            base.mkdir()
            target.mkdir()
            os.symlink(target, base / "link")
            with self.assertRaises(ValueError):
                api._sr_dir(base / "link" / "child", base)
            self.assertFalse((target / "child").exists())

    def test_symlinked_artifacts_folder_is_refused_without_writing_outside(self):
        api = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "registry"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            os.symlink(outside, root / "artifacts")
            with self.assertRaises(ValueError):
                api.write_execution(str(root), {"schema": 1, "execution_id": "9" * 64, "harness": "codex",
                                                "brief": "secret brief"}, "start")
            self.assertEqual(list(outside.iterdir()), [])

    def test_oversized_existing_record_is_replaced_not_wedged(self):
        api = self.api()
        identity = "8" * 64
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "records").mkdir()
            record = Path(root) / "records" / (identity + ".json")
            record.write_text('{"schema": 1, "goal": "' + "a" * (300 * 1024) + '"}')
            written = api.write_execution(root, {"schema": 1, "execution_id": identity, "harness": "codex",
                                                 "role": "lead", "brief": "fresh"}, "start")
            self.assertEqual(written.revision, 1)
            self.assertLess(record.stat().st_size, 256 * 1024)

    def test_invalid_existing_record_is_replaced_not_wedged(self):
        api = self.api()
        identity = "7" * 64
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "records").mkdir()
            (Path(root) / "records" / (identity + ".json")).write_text("not json {")
            written = api.write_execution(root, {"schema": 1, "execution_id": identity, "harness": "codex",
                                                 "role": "lead"}, "start")
            self.assertEqual(written.revision, 1)
            self.assertEqual(api.read_registry(root).records[0].execution_id, identity)

    def test_cursor_word_elsewhere_is_not_treated_as_cursor_hook(self):
        api = self.api()
        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                self.assertRaises(SystemExit):
            api.register_main(["--root", root, "ingest", "--account", "cursor"])
        self.assertNotIn("permission", out.getvalue())

    def test_failure_message_names_exception_type_only(self):
        api = self.api()
        secret = "PAYLOAD-SECRET-VALUE"
        err = io.StringIO()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root, mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "s"}))), \
                mock.patch.object(api, "ingest_hook", side_effect=RuntimeError(secret)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = api.register_main(["--root", root, "ingest", "--harness", "codex"])
        self.assertEqual(code, 1)
        self.assertIn("RuntimeError", err.getvalue())
        self.assertNotIn(secret, err.getvalue())
        self.assertNotIn(secret, out.getvalue())


if __name__ == "__main__":
    unittest.main()
