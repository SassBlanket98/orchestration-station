"""Headless native runs keep their exact launching Claude session and worker identity."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock

import collect
import native
from model import ExecutionEvidence, ExecutionRecord, Snapshot, Stage, Worker
from registry import execution_key


def run(harness: str = "codex", *, role: str = "worker", cwd: str = "/p/.stage/review") -> ExecutionEvidence:
    return ExecutionEvidence(ExecutionRecord(execution_key(harness, "personal", "run-1"), harness=harness,
                                             account="personal", native_id="run-1", role=role,
                                             cwd=cwd, started=100, activity=110))


class HeadlessAttachment(unittest.TestCase):
    def setUp(self) -> None:
        self.cache_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache_dir.cleanup)
        self.collector = collect.GlobalCollector(["/p"], quota=False,
                                                 headless_cache=f"{self.cache_dir.name}/headless.json")
        self.addCleanup(self.collector.close)
        self.collector._co_boot = 0
        self.collector._co_clk = 1
        self.procs = {10: (1, 0, 0, 10, 1), 20: (1, 0, 0, 100, 0)}
        self.collector._co_proc_table = lambda: self.procs
        argv = {10: ["claude"], 20: ["codex", "exec", "task"]}
        env = {10: {}, 20: {"CLAUDE_PID": "10", "CLAUDE_CODE_SESSION_ID": "lead-sid"}}
        for name, fn in (("_co_argv", lambda pid: argv.get(pid, [])),
                         ("_co_cwd", lambda pid: "/p/.stage/review" if pid == 20 else "/p"),
                         ("_co_environ", lambda pid: env.get(pid, {}))):
            patch = mock.patch.object(collect, name, side_effect=fn)
            patch.start()
            self.addCleanup(patch.stop)

    def test_live_env_link_attaches_worker_to_exact_claude_account(self) -> None:
        item = run()
        self.collector._co_link_headless([item])
        key = execution_key("claude", "personal", "lead-sid")
        self.assertEqual((item.record.role, item.record.parent_id, item.record.root_id, item.record.linkage),
                         ("worker", key, key, "env"))
        lead = Stage(path="/p/.stage/main", label="p", session_id="lead-sid", lead_kind="claude",
                     lead_state="live", snap=Snapshot(), solo=True)
        rows = self.collector._co_execution_stages([item], 120, True)
        self.assertEqual(collect._co_attach_executions([lead] + rows), [lead])
        self.assertEqual([worker.native_id for worker in lead.snap.workers], ["run-1"])

    def test_missing_claude_session_file_uses_child_environment(self) -> None:
        self.collector._co_link_headless([run()])
        self.assertEqual(self.collector._co_child_sessions, {10: "lead-sid"})
        lead = collect._co_Lead(10, "claude", "live", "/p", 10)
        with mock.patch("builtins.open", side_effect=OSError):
            meta = collect._co_lead_session("/p/.stage/main", lead, self.collector._co_child_sessions[10])
        self.assertEqual(meta["session_id"], "lead-sid")

    def test_link_survives_exit_and_finished_run_is_not_active(self) -> None:
        self.collector._co_link_headless([run()])
        self.procs = {10: self.procs[10]}
        item = run()
        self.collector._co_link_headless([item])
        (stage,) = self.collector._co_execution_stages([item], 200, True)
        self.assertEqual(item.record.linkage, "env")
        self.assertEqual(stage.live, 0)
        self.assertEqual(stage.state, "idle")
        self.assertEqual(stage.snap.workers[0].state, "noreport")

    def test_run_seen_headless_stays_worker_after_exit_even_if_native_says_lead(self) -> None:
        self.collector._co_link_headless([run()])
        self.procs = {10: self.procs[10]}
        item = run(role="lead")
        self.collector._co_link_headless([item])
        self.assertEqual(item.record.role, "worker")

    def test_unattached_runs_share_one_labelled_bucket(self) -> None:
        self.procs = {}
        items = [run(), ExecutionEvidence(ExecutionRecord(execution_key("codex", "personal", "run-2"),
                harness="codex", account="personal", native_id="run-2", role="worker",
                cwd="/p/.stage/other", started=105, activity=115))]
        self.collector._co_link_headless(items)
        rows = self.collector._co_execution_stages(items, 200, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].unlinked, rows[0].unattached, len(rows[0].snap.workers)), (False, True, 2))
        self.assertEqual(rows[0].state, "idle")

    def test_self_rooted_headless_runs_share_unattached_bucket(self) -> None:
        self.procs = {}
        first, second = run(), run()
        second.record.execution_id = execution_key("codex", "personal", "run-2")
        second.record.native_id = "run-2"
        first.record.root_id = first.record.execution_id
        second.record.root_id = second.record.execution_id
        self.collector._co_link_headless([first, second])
        rows = self.collector._co_execution_stages([first, second], 200, True)
        self.assertEqual([(row.unlinked, row.unattached, len(row.snap.workers)) for row in rows],
                         [(False, True, 2)])

    def test_stage_session_file_recovers_live_lead_session(self) -> None:
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as stage:
            Path(stage, "worker.session").write_text(json.dumps({
                "lead_pid": 10, "lead_session": "stage-lead-sid", "lead_pid_start": 10,
                "launched_at": 11,
            }), encoding="utf-8")
            worker = Worker("worker", "running", launched=11)
            self.collector._co_stage_sessions(stage, [worker], self.procs)
            self.assertEqual(self.collector._co_child_session(10), "stage-lead-sid")

            self.procs[10] = (1, 0, 0, 11, 1)
            self.collector._co_stage_sessions(stage, [worker], self.procs)
            self.assertEqual(self.collector._co_child_session(10), "")
            self.procs = {20: self.procs[20]}
            self.collector._co_stage_sessions(stage, [], self.procs)
            self.assertNotIn(10, self.collector._co_stage_lead_sessions)

    def test_stage_worker_folder_merges_native_worker(self) -> None:
        key = execution_key("claude", "personal", "lead-sid")
        item = run(cwd="/p/.stage/main/review")
        item.record.parent_id = item.record.root_id = key
        item.record.linkage = "env"
        (execution,) = self.collector._co_execution_stages([item], 200, True)
        stage = Stage(path="/p/.stage/main#unlinked", dir="/p/.stage/main", label="p",
                      snap=Snapshot(workers=[Worker("review", "running", pid=20)]), unlinked=True)
        lead = Stage(path="/p/.stage/lead", label="p", session_id="lead-sid", lead_kind="claude",
                     lead_state="live", snap=Snapshot())
        rows = collect._co_attach_executions([lead, stage, execution])
        self.assertEqual(rows, [lead, stage])
        self.assertEqual(stage.snap.workers[0].native_id, "run-1")
        self.assertEqual(stage.snap.workers[0].session, "lead-sid")

    def test_opencode_run_is_worker_and_hosted_session_has_no_fake_pid(self) -> None:
        item = run("opencode", role="lead")
        with mock.patch.object(collect, "_co_argv", side_effect=lambda pid: ["opencode", "run"] if pid == 20 else []):
            self.collector._co_link_headless([item])
        self.assertEqual(item.record.role, "worker")
        hosted = collect._co_Lead(0, "opencode", "background", "/p", 100, "ses-1")
        self.assertEqual(hosted.pid, 0)
        self.assertIsNone(Stage(path="x", label="p", lead_pid=hosted.pid or None).lead_pid)

    def test_finished_old_run_cannot_take_new_process_or_link(self) -> None:
        old = run(role="lead")
        old.record.started = 80
        old.record.activity = 90
        old.record.ended = 91
        self.collector._co_link_headless([old])
        self.assertIsNone(old.record.pid)
        self.assertEqual(old.record.role, "lead")
        self.assertNotIn(old.record.execution_id, self.collector._co_headless_links)

    def test_different_pid_and_stale_identity_cannot_match(self) -> None:
        other = run(role="lead")
        other.record.pid = 21
        self.collector._co_link_headless([other])
        self.assertEqual(other.record.pid, 21)
        other.record.pid = 20
        other.record.pid_start = 999
        other.record.boot_id = "oldboot"
        self.collector._co_link_headless([other])
        self.assertEqual(other.record.pid_start, 999)
        self.assertNotIn(other.record.execution_id, self.collector._co_headless_links)

    def test_nonlive_record_needs_close_start(self) -> None:
        other = run(role="lead")
        other.record.started = 95
        other.record.activity = 105
        self.collector._co_link_headless([other])
        self.assertIsNone(other.record.pid)

    def test_activity_before_process_start_rejects_match(self) -> None:
        other = run(role="lead")
        other.record.started = 98
        other.record.activity = 97
        self.collector._co_link_headless([other])
        self.assertIsNone(other.record.pid)

    def test_hosted_opencode_row_wins_stage_dedupe(self) -> None:
        hosted = Stage(path="hosted", label="remote work", session_id="ses-1", lead_kind="opencode",
                       lead_state="background", state="active", snap=Snapshot(workers=[Worker("a", "running")]))
        stage = Stage(path="stage", dir="/p/.stage/main", label="p", session_id="ses-1",
                      lead_state="gone", state="closed", snap=Snapshot(workers=[Worker("b", "done")]))
        (winner,) = collect._co_dedupe_sessions([hosted, stage])
        self.assertEqual((winner.path, winner.label, winner.lead_state), ("hosted", "remote work", "background"))
        self.assertEqual(len(winner.snap.workers), 2)

    def test_same_folder_collision_keeps_executions_separate(self) -> None:
        lead = Stage(path="lead", label="p", session_id="lead-sid", lead_kind="claude", snap=Snapshot())
        stage = Stage(path="stage", dir="/p/.stage/main", label="p",
                      snap=Snapshot(workers=[Worker("review", "running", pid=20)]))
        first = run(cwd="/p/.stage/main/review")
        first.result = "OLD RESULT"
        second = run(cwd="/p/.stage/main/review")
        second.record.execution_id = execution_key("codex", "personal", "run-2")
        second.record.native_id = "run-2"
        second.record.started = 110
        second.record.activity = 120
        rows = self.collector._co_execution_stages([first, second], 130, True)
        out = collect._co_attach_executions([lead, stage] + rows)
        self.assertEqual(stage.snap.workers[0].native_id, "")
        self.assertEqual({worker.native_id for row in out for worker in row.snap.workers
                          if worker.native_id}, {"run-1", "run-2"})

    def test_unattached_runs_across_projects_share_one_bucket(self) -> None:
        self.procs = {}
        first = run(cwd="/a/project")
        second = run(cwd="/b/project")
        second.record.execution_id = execution_key("codex", "personal", "run-2")
        second.record.native_id = "run-2"
        self.collector._co_link_headless([first, second])
        rows = self.collector._co_execution_stages([first, second], 200, True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(len(r.snap.workers) for r in rows), [1, 1])
        self.assertFalse(any(r.unattached for r in rows))   # not inside a stage tree: still a project row

    def test_unicode_claude_pid_does_not_break_link_scan(self) -> None:
        with mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_PID": "²", "CLAUDE_CODE_SESSION_ID": "sid"}):
            self.collector._co_link_headless([run()])
        self.assertEqual(self.collector._co_child_sessions, {})

    def test_self_parent_link_is_ignored(self) -> None:
        item = run("claude", role="worker")
        self.procs = {}
        self.collector._co_headless_links[item.record.execution_id] = (item.record.native_id, "personal")
        self.collector._co_link_headless([item])
        self.assertEqual(item.record.parent_id, "")

    def test_headless_cache_is_bounded_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/headless.json"
            first = collect.GlobalCollector(["/p"], quota=False, headless_cache=path)
            self.addCleanup(first.close)
            for n in range(2002):
                key = f"run-{n}"
                first._co_remember_headless(key, ("lead-sid", "personal"))
            first._co_save_headless()
            second = collect.GlobalCollector(["/p"], quota=False, headless_cache=path)
            self.addCleanup(second.close)
            self.assertEqual(len(second._co_headless_ids), 2000)
            self.assertEqual(len(second._co_headless_links), 2000)
            self.assertNotIn("run-0", second._co_headless_ids)
            self.assertIn("run-2001", second._co_headless_ids)
            second._co_proc_table = dict
            item = run(role="lead")
            item.record.execution_id = "run-2001"
            second._co_link_headless([item])
            self.assertEqual(item.record.role, "worker")


class RcAncestry(unittest.TestCase):
    setUp = HeadlessAttachment.setUp

    def test_rc_sdk_child_without_lineage_uses_ancestor_and_sidecar(self) -> None:
        self.procs = {28149: (1, 0, 0, 10, 1), 28437: (28149, 0, 0, 100, 0)}
        argv = {28149: ["claude", "rc"], 28437: ["claude", "--print", "--sdk-url", "https://example.test"]}
        item = run("claude", cwd="/p")
        sidecar = {"sessionId": "rc-sid", "cwd": "/p", "name": "RC project", "updatedAt": 110000}
        with mock.patch.object(collect, "_co_argv", side_effect=lambda pid: argv.get(pid, [])), \
                mock.patch.object(collect, "_co_cwd", return_value="/p"), \
                mock.patch.object(collect, "_co_environ", return_value={}), \
                mock.patch.object(collect, "_co_claude_sidecar", return_value=sidecar):
            self.collector._co_link_headless([item])
            key = execution_key("claude", "personal", "rc-sid")
            self.assertEqual((item.record.parent_id, item.record.root_id), (key, key))
            self.assertEqual(self.collector._co_child_session(28149), "rc-sid")
            lead = collect._co_Lead(28149, "claude", "live", "/p", 10)
            meta = collect._co_lead_session("", lead, self.collector._co_child_session(28149))
        self.assertEqual((meta["session_id"], meta["session_name"]), ("rc-sid", "RC project"))

    def test_status_parent_supports_non_claude_lead(self) -> None:
        item = run()
        with mock.patch.object(collect, "_co_environ", return_value={
                "STATUS_PARENT_SESSION": "parent-sid", "STATUS_PARENT_HARNESS": "opencode"}):
            self.collector._co_link_headless([item])
        key = execution_key("opencode", "personal", "parent-sid")
        self.assertEqual((item.record.parent_id, item.record.root_id), (key, key))


class NativeClassification(unittest.TestCase):
    def test_codex_exec_rollout_is_worker(self) -> None:
        with mock.patch.object(native, "_nv_time", return_value=100):
            rec = native._nv_start({"type": "session_meta"}, {"id": "run", "source": "exec", "cwd": "/p"},
                                   "codex", "personal", native.Path("/tmp/synthetic.jsonl"), 100)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.role, "worker")

    def test_claude_sdk_entrypoint_is_worker(self) -> None:
        rec = native._nv_start({"type": "user", "sessionId": "run", "cwd": "/p", "entrypoint": "sdk-cli"}, {},
                               "claude", "personal", native.Path("/tmp/synthetic.jsonl"), 100)
        self.assertEqual(rec.role, "worker")

    def test_claude_later_sdk_marker_is_worker(self) -> None:
        state = {"record": None, "brief": "", "result": "", "oversized": False}
        path = native.Path("/tmp/synthetic.jsonl")
        native._nv_apply(state, {"type": "file-history-snapshot", "sessionId": "run", "cwd": "/p"},
                         "claude", "personal", path)
        native._nv_apply(state, {"type": "user", "sessionId": "run", "cwd": "/p", "entrypoint": "sdk-cli"},
                         "claude", "personal", path)
        self.assertEqual(state["record"].role, "worker")


if __name__ == "__main__":
    unittest.main()
