"""Durable dispatch identity separates finished workers from hosted leads."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_opencode_sessions import DAEMON, NOW, daemon_argv, make_home
from test_step5b_attach import run

import collect
from model import Snapshot, Stage
from registry import execution_key


class RunRecords(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stage = self.root / ".stage/step5c/triage"
        self.stage.mkdir(parents=True)
        self.collector = collect.GlobalCollector([str(self.root)], quota=False,
                                                 headless_cache=str(self.root / "cache.json"))
        self.addCleanup(self.collector.close)
        self.collector._co_proc_table = dict
        self.collector._co_found = [(str(self.stage), "triage")]

    def record(self, **changes: object) -> None:
        data = {"schema": 1, "name": "review", "harness": "opencode", "native_id": "run-1",
                "cwd": str(self.root), "started": 100, "ended": 120, "pid": 20,
                "lead_session": "lead-sid", "lead_harness": "claude", "lead_pid": 10,
                "stage": str(self.stage)}
        data.update(changes)
        (self.stage / "review.run.json").write_text(json.dumps(data), encoding="utf-8")

    def test_finished_record_attaches_native_lead_as_worker(self) -> None:
        for harness in ("opencode", "cursor"):
            with self.subTest(harness=harness):
                self.record(harness=harness)
                self.collector._co_load_run_records()
                item = run(harness, role="lead", cwd=str(self.root))
                item.record.ended = 120
                self.collector._co_link_headless([item])
                key = execution_key("claude", "personal", "lead-sid")
                self.assertEqual((item.record.role, item.record.parent_id, item.record.root_id),
                                 ("worker", key, key))
                lead = Stage(path="lead", label="p", session_id="lead-sid", lead_kind="claude", snap=Snapshot())
                rows = collect._co_attach_executions([lead] + self.collector._co_execution_stages([item], 130, True))
                self.assertEqual(rows, [lead])
                self.assertEqual([w.native_id for w in lead.snap.workers], ["run-1"])

    def test_record_attaches_to_work_account_lead(self) -> None:
        self.record()
        self.collector._co_load_run_records()
        item = run("opencode", role="lead", cwd=str(self.root))
        lead = run("claude", role="lead", cwd=str(self.root))
        lead.record.native_id = "lead-sid"
        lead.record.account = "work"
        lead.record.execution_id = execution_key("claude", "work", "lead-sid")
        self.collector._co_link_headless([item, lead])
        self.assertEqual(item.record.parent_id, lead.record.execution_id)
        station = Stage(path="lead", label="work", session_id="lead-sid", lead_kind="claude work", snap=Snapshot())
        rows = collect._co_attach_executions([station] + self.collector._co_execution_stages([item], 130, True))
        self.assertEqual(rows, [station])
        self.assertEqual([w.native_id for w in station.snap.workers], ["run-1"])

    def test_record_uses_live_leads_config_without_native_evidence(self) -> None:
        self.record()
        self.collector._co_load_run_records()
        self.collector._co_proc_table = lambda: {10: (1, 0, 0, 10, 1)}
        item = run("opencode", role="lead", cwd=str(self.root))
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CONFIG_DIR": "/home/test/.claude-team-work"}), \
                mock.patch.object(collect, "_co_argv", return_value=[]):
            self.collector._co_link_headless([item])
        self.assertEqual(item.record.parent_id, execution_key("claude", "work", "lead-sid"))

    def test_env_parent_resolves_work_account_lead(self) -> None:
        self.collector._co_boot, self.collector._co_clk = 0, 1
        self.collector._co_proc_table = lambda: {20: (1, 0, 0, 100, 0)}
        item = run("opencode", role="lead", cwd=str(self.root))
        lead = run("claude", role="lead", cwd=str(self.root))
        lead.record.native_id = "lead-sid"
        lead.record.account = "work"
        lead.record.execution_id = execution_key("claude", "work", "lead-sid")
        with mock.patch.object(collect, "_co_argv", return_value=["opencode", "run", "task"]), \
                mock.patch.object(collect, "_co_cwd", return_value=str(self.root)), \
                mock.patch.object(collect, "_co_environ", return_value={
                    "STATUS_PARENT_SESSION": "lead-sid", "STATUS_PARENT_HARNESS": "claude"}):
            self.collector._co_link_headless([item, lead])
        self.assertEqual(item.record.parent_id, lead.record.execution_id)

    def test_live_interactive_stage_chat_stays_lead(self) -> None:
        for harness in ("cursor", "opencode"):
            with self.subTest(harness=harness):
                item = run(harness, role="lead", cwd=str(self.stage))
                item.record.pid, item.record.pid_start = 20, 100
                self.collector._co_proc_table = lambda: {20: (1, 0, 0, 100, 1)}
                with mock.patch.object(collect, "_co_argv", return_value=[harness]):
                    self.collector._co_link_headless([item])
                self.assertEqual(item.record.role, "lead")
                self.assertEqual(item.record.parent_id, "")

    def test_live_stage_chat_without_native_pid_stays_lead(self) -> None:
        for harness, argv in (("cursor", ["cursor-agent"]), ("opencode", ["opencode"])):
            with self.subTest(harness=harness):
                item = run(harness, role="lead", cwd=str(self.stage))
                # Reconciliation marks native chats without process identity as finished.
                item.record.state, item.record.ended = "finished", 110
                self.collector._co_proc_table = lambda: {20: (1, 0, 0, 100, 1)}
                with mock.patch.object(collect, "_co_argv", return_value=argv), \
                        mock.patch.object(collect, "_co_cwd", return_value=str(self.stage)):
                    self.collector._co_link_headless([item])
                self.assertEqual(item.record.role, "lead")

    def test_a_record_in_a_dispatch_run_directory_below_the_stage_is_found(self) -> None:
        run = self.stage / "review5"
        run.mkdir()
        (run / "w1.run.json").write_text(json.dumps({"schema": 1, "harness": "opencode", "native_id": "ses_below"}))
        self.collector._co_load_run_records()
        self.assertIn("ses_below", self.collector._co_run_records)

    def test_record_scan_is_shallow_and_caches_unchanged_files(self) -> None:
        self.record()
        hidden = self.stage / "checkout/deep"
        hidden.mkdir(parents=True)
        (hidden / "hidden.run.json").write_text(
            json.dumps({"schema": 1, "harness": "cursor", "native_id": "hidden"}))
        self.collector._co_load_run_records()
        self.assertEqual(set(self.collector._co_run_records), {"run-1"})
        with mock.patch("builtins.open", side_effect=AssertionError("unchanged record reread")), \
                mock.patch.object(collect.os, "scandir", side_effect=AssertionError("unchanged directory rescanned")):
            self.collector._co_load_run_records()
        self.record(native_id="run-2")
        self.collector._co_load_run_records()
        self.assertEqual(set(self.collector._co_run_records), {"run-2"})
        self.record(native_id="run-3", name="longer name")
        self.collector._co_load_run_records()
        self.assertEqual(set(self.collector._co_run_records), {"run-3"})
        (self.stage / "extra.run.json").write_text(
            json.dumps({"schema": 1, "harness": "cursor", "native_id": "extra"}))
        self.collector._co_load_run_records()
        self.assertEqual(set(self.collector._co_run_records), {"run-3", "extra"})
        (self.stage / "extra.run.json").unlink()
        (self.stage / "review.run.json").unlink()
        self.collector._co_load_run_records()
        self.assertEqual(self.collector._co_run_records, {})

    def test_unrecorded_pid_zero_hosted_session_stays_lead(self) -> None:
        item = run("opencode", role="lead", cwd=str(self.root))
        item.record.pid = 0
        self.collector._co_load_run_records()
        self.collector._co_link_headless([item])
        self.assertEqual(item.record.role, "lead")
        self.assertEqual(item.record.parent_id, "")

    def test_nested_logs_and_records_exclude_hosted_ghosts(self) -> None:
        self.record()
        (self.stage / "x.log").write_text('{"sessionID":"ses_nested"}\n', encoding="utf-8")
        self.collector._co_load_run_records()
        home = make_home([(sid, None, str(self.root), 1, 1, "succeeded", "Review")
                          for sid in ("run-1", "ses_nested", "hosted")])
        self.addCleanup(shutil.rmtree, home)
        with mock.patch.dict("os.environ", {"HOME": home}):
            leads = collect._co_service_leads(DAEMON, daemon_argv, [], 100, NOW - 10_000,
                                            set(self.collector._co_run_records))
        self.assertEqual([lead.sid for lead in leads], ["hosted"])

    def test_incomplete_or_invalid_records_do_not_flip_roles(self) -> None:
        for changes in ({"native_id": ""}, {"schema": 2}, {"schema": True}, {"schema": 1.0}, {"harness": []}):
            with self.subTest(changes=changes):
                self.record(**changes)
                self.collector._co_load_run_records()
                item = run("opencode", role="lead", cwd=str(self.root))
                self.collector._co_link_headless([item])
                self.assertEqual(item.record.role, "lead")

    def test_stage_cwd_is_worker_fallback_without_record(self) -> None:
        item = run("cursor", role="lead", cwd=str(self.stage))
        self.collector._co_link_headless([item])
        self.assertEqual(item.record.role, "worker")

    def test_concurrent_same_cwd_and_start_keep_exact_parents(self) -> None:
        self.record()
        second = json.loads((self.stage / "review.run.json").read_text())
        second.update(native_id="run-2", lead_session="other-lead")
        (self.stage / "other.run.json").write_text(json.dumps(second), encoding="utf-8")
        self.collector._co_load_run_records()
        items = [run("opencode", role="lead", cwd=str(self.root)) for _ in range(2)]
        items[1].record.native_id = "run-2"
        items[1].record.execution_id = execution_key("opencode", "personal", "run-2")
        self.collector._co_link_headless(items)
        self.assertEqual([item.record.parent_id for item in items], [
            execution_key("claude", "personal", sid) for sid in ("lead-sid", "other-lead")])

    def test_nested_log_scan_still_reads_only_first_four_kib(self) -> None:
        (self.stage / "late.log").write_text(" " * 4096 + '{"sessionID":"ses_late"}', encoding="utf-8")
        self.assertEqual(collect._co_stage_run_sessions(str(self.root)), set())

    def test_run_id_exclusion_applies_outside_record_directory(self) -> None:
        home = make_home([("run-1", None, "/elsewhere", 1, 1, "succeeded", "Review")])
        self.addCleanup(shutil.rmtree, home)
        with mock.patch.dict("os.environ", {"HOME": home}):
            leads = collect._co_service_leads(DAEMON, daemon_argv, [], 100, NOW - 10_000, {"run-1"})
        self.assertEqual(leads, [])

    def test_build_preloads_records_before_execution_discovery(self) -> None:
        self.record()

        def executions(now: float) -> list:
            self.assertIn("run-1", self.collector._co_run_records)
            return []

        with mock.patch.object(self.collector, "_co_refresh"), \
                mock.patch.object(self.collector, "_co_executions", side_effect=executions), \
                mock.patch.object(self.collector, "_co_lead_map", return_value={}):
            self.collector._co_build(130, True)
