"""D2: no empty execution rows, one worker per native run, worktree runs under their project, one Bot project."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import collect
from logparse import LogState
from model import (
    DONE,
    RUNNING,
    ExecutionEvidence,
    ExecutionRecord,
    Snapshot,
    Stage,
    Step,
    Worker,
)
from registry import execution_key

NOW = 1_792_830_000.0
PEER = collect.OPENCLAW_OWN_PEERS[0]


def record(identity: str, *, harness: str = "codex", account: str = "personal", native_id: str = "",
           role: str = "lead", cwd: str = "/p/project", parent_id: str = "", root_id: str = "",
           state: str = "idle", outcome: str = "unknown", started: float = NOW - 600,
           activity: float = NOW - 300, ended: float | None = None, source: str = "registry") -> ExecutionRecord:
    return ExecutionRecord(identity, harness=harness, account=account, native_id=native_id, role=role, cwd=cwd,
                           parent_id=parent_id, root_id=root_id, state=state, outcome=outcome, started=started,
                           activity=activity, ended=ended, source=source)


def collector(openclaw_home: str | None = None) -> collect.GlobalCollector:
    """A collector with an empty process table: nothing it builds is live."""
    g = collect.GlobalCollector([], quota=False, openclaw_home=openclaw_home)
    g._co_proc_table = dict
    return g


def station(session_id: str = "lead-sid", workers: list[Worker] | None = None) -> Stage:
    return Stage(path="/p/.stage", label="p", dir="/p/.stage", session_id=session_id, lead_kind="claude",
                 lead_state="live", snap=Snapshot(workers=list(workers or [])), solo=not workers)


class EmptyExecutionRows(unittest.TestCase):
    """1. An execution stage with no workers, no dir and no activity time is never emitted."""

    def stages(self, items: list[ExecutionEvidence]) -> list[Stage]:
        g = collector()
        self.addCleanup(g.close)
        return g._co_execution_stages(items, NOW, False)

    def test_native_sighting_with_nothing_to_show_is_not_a_row(self) -> None:
        sighting = record("run", harness="cursor", cwd="", started=0, activity=0,
                          source="/home/x/.cursor/agent.jsonl")
        self.assertEqual(self.stages([ExecutionEvidence(sighting)]), [])

    def test_registered_execution_with_no_time_is_not_a_row(self) -> None:
        self.assertEqual(self.stages([ExecutionEvidence(record("bare", cwd="", started=0, activity=0))]), [])

    def test_a_recent_lead_with_a_cwd_and_no_workers_is_kept(self) -> None:
        sighting = record("lead", harness="codex", cwd="/p/project", source="/home/x/rollout.jsonl",
                          activity=NOW - 120)
        (stage,) = self.stages([ExecutionEvidence(sighting)])
        self.assertEqual((stage.execution_id, stage.cwd, stage.snap.workers), ("lead", "/p/project", []))

    def test_registered_execution_with_a_time_is_kept(self) -> None:
        (stage,) = self.stages([ExecutionEvidence(record("timed"))])
        self.assertEqual(stage.execution_id, "timed")

    def test_native_sighting_with_a_worker_is_kept(self) -> None:
        lead = record("lead", source="/home/x/transcript.jsonl")
        child = record("child", role="worker", parent_id="lead", root_id="lead", source="/home/x/transcript.jsonl")
        (stage,) = self.stages([ExecutionEvidence(lead), ExecutionEvidence(child)])
        self.assertEqual([worker.execution_id for worker in stage.snap.workers], ["child"])

    def test_native_sighting_with_a_live_process_is_kept(self) -> None:
        sighting = record("live", source="/home/x/transcript.jsonl")
        g = collector()
        self.addCleanup(g.close)
        g._co_execution_live = lambda rec, boot_id=None: True
        (stage,) = g._co_execution_stages([ExecutionEvidence(sighting)], NOW, False)
        self.assertEqual(stage.execution_id, "live")


class OneWorkerPerRun(unittest.TestCase):
    """2. One worker per native run; registry counters reach the worker's log."""

    def test_execution_record_counters_reach_the_workers_log(self) -> None:
        item = record("run", harness="openclaw", account="openclaw", native_id="key")
        item.tokens_in, item.tokens_out, item.tokens_cache, item.cost = 11, 22, 33, 0.25
        worker = collect._co_execution_worker(ExecutionEvidence(item), NOW, False)
        self.assertEqual((worker.log.tokens_in, worker.log.tokens_out, worker.log.tokens_cache, worker.log.cost),
                         (11, 22, 33, 0.25))

    def test_db_row_and_registry_row_of_one_run_fold_keeping_both_halves(self) -> None:
        start, end = NOW - 900, NOW - 600
        db_row = Worker("Map the tree", DONE, title="Map the tree", start=start, end=end, pid=4242,
                        log=LogState(steps=[Step(ts=start + 5, kind="tool", tool="read", text="collect.py")],
                                     tokens_in=100))
        registry_row = Worker("Map the tree", DONE, title="Map the tree", start=start, end=end, pid=4242,
                              execution_id="exec-1", native_id="ses_child", goal="Map it", brief_text=["brief"])
        row = station(workers=[db_row, registry_row])

        self.assertEqual(collect._co_attach_executions([row]), [row])
        (merged,) = row.snap.workers
        self.assertEqual((merged.execution_id, merged.native_id, merged.goal), ("exec-1", "ses_child", "Map it"))
        self.assertEqual(merged.brief_text, ["brief"])
        self.assertEqual([step.tool for step in merged.log.steps], ["read"])
        self.assertEqual(merged.log.tokens_in, 100)

    def test_two_runs_of_one_stage_that_only_share_a_time_stay_two(self) -> None:
        start, end = NOW - 900, NOW - 600
        first = Worker("first", DONE, start=start, end=end, native_id="ses_a")
        second = Worker("second", DONE, start=start, end=end, native_id="ses_b")
        row = station(workers=[first, second])

        collect._co_attach_executions([row])

        self.assertEqual([worker.name for worker in row.snap.workers], ["first", "second"])

    def test_same_native_id_folds_even_when_the_times_differ(self) -> None:
        first = Worker("db", DONE, start=NOW - 900, end=NOW - 600, native_id="ses_a",
                       log=LogState(steps=[Step(ts=NOW - 890, kind="tool", tool="read", text="x")]))
        second = Worker("registry", DONE, start=NOW - 800, end=NOW - 700, native_id="ses_a", goal="the goal")
        row = station(workers=[first, second])

        collect._co_attach_executions([row])

        (merged,) = row.snap.workers
        self.assertEqual((merged.name, merged.goal, len(merged.log.steps)), ("db", "the goal", 1))

    def test_two_concurrent_workers_with_different_names_never_merge(self) -> None:
        """Only one side carries a native id; time alone must not join them."""
        start = NOW - 120
        db_row = Worker("Map the tree", RUNNING, start=start, native_id="ses_child")
        other = Worker("Sweep the logs", RUNNING, start=start + 1)
        self.assertFalse(collect._co_same_run_worker(db_row, other))
        row = station(workers=[db_row, other])

        collect._co_attach_executions([row])

        self.assertEqual([worker.name for worker in row.snap.workers], ["Map the tree", "Sweep the logs"])

    def test_same_name_time_and_pid_still_merge(self) -> None:
        start, end = NOW - 900, NOW - 600
        left = Worker("w", DONE, start=start, end=end, pid=4242)
        right = Worker("w", DONE, start=start + 1, end=end, pid=4242, native_id="ses_a", execution_id="exec-1")
        self.assertTrue(collect._co_same_run_worker(left, right))

    def test_two_same_named_claude_workers_a_second_apart_with_different_pids_stay_two(self) -> None:
        """The review's case: same name and near start, different processes, only one with an execution id."""
        start = NOW - 120
        first = Worker("builder", RUNNING, start=start, pid=4100)
        second = Worker("builder", RUNNING, start=start + 1, pid=4200, execution_id="exec-2")
        self.assertFalse(collect._co_same_run_worker(first, second))
        row = station(workers=[first, second])

        collect._co_attach_executions([row])

        self.assertEqual([(worker.name, worker.pid) for worker in row.snap.workers],
                         [("builder", 4100), ("builder", 4200)])

    def test_a_run_record_linkage_tag_alone_never_merges_two_rows(self) -> None:
        """Tagging both rows `run-record` is not proof: the records themselves must name both."""
        start, end = NOW - 900, NOW - 600
        first = Worker("w", DONE, start=start, end=end, linkage="run-record")
        second = Worker("w", DONE, start=start + 1, end=end, linkage="run-record")
        row = station(workers=[first, second])

        collect._co_attach_executions([row])

        self.assertEqual(len(row.snap.workers), 2)

    def test_a_row_named_by_its_session_id_joins_the_named_row_only_with_a_record(self) -> None:
        """A record row has no title, so it is named by its session id; the record that launched both still decides."""
        start, end = NOW - 900, NOW - 600
        run = {"schema": 1, "name": "f2-andon-fix", "harness": "cursor", "cwd": "/p", "pid": 6200,
               "started": start, "native_id": "sess-2", "lead_session": "lead-sid"}
        named = Worker("f2-andon-fix", DONE, start=start, end=end, pid=6200)
        by_id = Worker("sess-2", DONE, start=start + 1, end=end, native_id="sess-2")
        self.assertFalse(collect._co_same_run_worker(named, by_id), "no record, no proof")
        self.assertTrue(collect._co_same_run_worker(named, by_id, [run]))
        other = Worker("sess-3", DONE, start=start + 1, end=end, native_id="sess-3")
        self.assertFalse(collect._co_same_run_worker(named, other, [run]), "a different session id is another run")

    def test_a_real_dispatch_run_record_joins_the_two_halves_it_launched(self) -> None:
        """One actual run record names both the native row and its launching pid; no record means no proof."""
        start, end = NOW - 900, NOW - 600
        run = {"schema": 1, "name": "probe_ds", "harness": "opencode", "cwd": "/p", "pid": 5150,
               "started": start, "native_id": "ses_a", "lead_session": "lead-sid"}
        native_row = Worker("probe_ds", DONE, start=start, end=end, native_id="ses_a")
        pid_row = Worker("probe_ds", DONE, start=start + 1, end=end, pid=5150)
        self.assertFalse(collect._co_same_run_worker(native_row, pid_row))
        self.assertTrue(collect._co_same_run_worker(native_row, pid_row, [run]))

        with tempfile.TemporaryDirectory() as stage:
            row = Stage(path=stage, label="p", dir=stage, session_id="lead-sid", snap=Snapshot(workers=[
                Worker("probe_ds", DONE, start=start, end=end, native_id="ses_a"),
                Worker("probe_ds", DONE, start=start + 1, end=end, pid=5150)]))
            collect._co_attach_executions([row])
            self.assertEqual(len(row.snap.workers), 2, "without the record the two rows stay apart")

            with open(os.path.join(stage, "probe_ds.run.json"), "w", encoding="utf-8") as fh:
                json.dump(run, fh)
            collect._co_attach_executions([row])
            self.assertEqual([(worker.name, worker.native_id) for worker in row.snap.workers],
                             [("probe_ds", "ses_a")])

    def test_a_richer_later_duplicate_replaces_the_entry_it_folds_into(self) -> None:
        start, end = NOW - 900, NOW - 600
        registry_row = Worker("w", DONE, start=start, end=end, execution_id="exec-1", native_id="ses_a", goal="g")
        db_row = Worker("w", DONE, start=start, end=end, native_id="ses_a",
                        log=LogState(steps=[Step(ts=start + 5, kind="tool", tool="read", text="a"),
                                            Step(ts=start + 6, kind="tool", tool="edit", text="b")]))
        row = station(workers=[registry_row, db_row])

        collect._co_attach_executions([row])

        (merged,) = row.snap.workers
        self.assertEqual([step.tool for step in merged.log.steps], ["read", "edit"])
        self.assertEqual((merged.execution_id, merged.goal), ("exec-1", "g"))


class WorktreeRuns(unittest.TestCase):
    """3. Worker runs inside <project>/.stage/<worktree> belong to the project, or to the session that launched them."""

    def worktree(self, root: str) -> str:
        path = os.path.join(root, ".stage", "ops", "wt-a")
        os.makedirs(path)
        return path

    def run_in(self, worktree: str) -> ExecutionEvidence:
        item = record("run-1", harness="opencode", role="worker", cwd=worktree, native_id="ses_run",
                      root_id=f"unattached:{worktree}", started=NOW - 600)
        return ExecutionEvidence(item)

    def test_without_a_link_it_is_one_unattached_run_under_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worktree = self.worktree(root)
            g = collector()
            self.addCleanup(g.close)
            (stage,) = g._co_execution_stages([self.run_in(worktree)], NOW, False)
            self.assertEqual((stage.dir, stage.label, stage.unattached),
                             (root, os.path.basename(root), True))
            self.assertEqual(len(stage.snap.workers), 1)

    def test_a_run_record_without_a_native_id_still_finds_its_launching_session(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worktree = self.worktree(root)
            with open(os.path.join(root, ".stage", "ops", "a.run.json"), "w", encoding="utf-8") as fh:
                json.dump({"schema": 1, "name": "a", "harness": "opencode", "native_id": "",
                           "cwd": worktree, "started": NOW - 600,
                           "lead_session": "lead-sid", "lead_harness": "claude"}, fh)
            lead = station()
            g = collector()
            self.addCleanup(g.close)
            rows = collect._co_attach_executions([lead] + g._co_execution_stages([self.run_in(worktree)], NOW, False))
            self.assertEqual(rows, [lead])
            self.assertEqual([worker.native_id for worker in lead.snap.workers], ["ses_run"])

    def test_a_parent_link_attaches_the_run_to_its_session(self) -> None:
        key = execution_key("claude", "personal", "lead-sid")
        child = record("run-1", harness="codex", role="worker", parent_id=key, root_id=key,
                       cwd="/p/.stage/review")
        lead = station()
        g = collector()
        self.addCleanup(g.close)
        self.assertEqual(collect._co_attach_executions([lead] + g._co_execution_stages([ExecutionEvidence(child)],
                                                                                       NOW, False)), [lead])
        self.assertEqual(len(lead.snap.workers), 1)


def openclaw_home(sessions: list[tuple[str, float, str, str]], cron_jobs: dict[str, str] | None = None) -> str:
    """A fake home with Bot's session table: (session_key, age in seconds, created_via, label)."""
    home = tempfile.mkdtemp()
    agent_dir = os.path.join(home, ".openclaw", "agents", "openclaw", "agent")
    os.makedirs(agent_dir)
    conn = sqlite3.connect(os.path.join(agent_dir, "openclaw-agent.sqlite"))
    conn.execute("CREATE TABLE session_nodes (session_key TEXT, display_name TEXT, label TEXT, "
                 "last_activity_at INTEGER, created_at INTEGER, updated_at INTEGER, entry_json TEXT, created_via TEXT)")
    for key, age, via, label in sessions:
        ms = int((NOW - age) * 1000)
        conn.execute("INSERT INTO session_nodes VALUES (?,?,?,?,?,?,?,?)",
                     (key, "", label, ms, ms - 60_000, ms, json.dumps({"model": "m1"}), via))
    conn.commit()
    conn.close()
    if cron_jobs:
        state_dir = os.path.join(home, ".openclaw", "state")
        os.makedirs(state_dir)
        conn = sqlite3.connect(os.path.join(state_dir, "openclaw.sqlite"))
        conn.execute("CREATE TABLE cron_jobs (job_id TEXT, name TEXT)")
        conn.executemany("INSERT INTO cron_jobs VALUES (?,?)", list(cron_jobs.items()))
        conn.commit()
        conn.close()
    return home


class BotProject(unittest.TestCase):
    """4. One Bot project, names from the key, workspace runs folded under it."""

    def rows(self, home: str) -> dict[str, Stage]:
        return {stage.session_id: stage for stage in collect._co_openclaw_stages(NOW, False, home)}

    def test_every_row_groups_on_one_project_root_and_is_named_from_its_key(self) -> None:
        home = openclaw_home([("agent:openclaw:main", 60, "", ""),
                          (f"agent:openclaw:telegram:direct:{PEER}", 60, "channel", ""),
                          (f"agent:openclaw:telegram:openclaw:direct:{PEER}", 90, "channel", ""),
                          ("agent:openclaw:dashboard:d1", 90, "operator", "monitor Test"),
                          ("agent:openclaw:cron:job-1", 30, "cron", "Automation: old-name")],
                         cron_jobs={"job-1": "nightly sweep"})
        rows = self.rows(home)
        root = os.path.join(home, ".openclaw", "agents", "openclaw")
        self.assertEqual({stage.dir for stage in rows.values()}, {root})
        self.assertEqual({stage.label for stage in rows.values()}, {"Bot"})
        self.assertEqual(rows["agent:openclaw:main"].display_name, "main")
        self.assertEqual(rows[f"agent:openclaw:telegram:direct:{PEER}"].display_name, "telegram · direct")
        self.assertEqual(rows[f"agent:openclaw:telegram:openclaw:direct:{PEER}"].display_name, "telegram · openclaw direct")
        self.assertEqual(rows["agent:openclaw:dashboard:d1"].display_name, "dashboard · monitor Test")
        self.assertEqual(rows["agent:openclaw:cron:job-1"].display_name, "nightly sweep")
        self.assertEqual((rows["agent:openclaw:cron:job-1"].cluster, rows["agent:openclaw:main"].cluster), ("cron", ""))

    def test_a_run_in_openclaw_workspace_folds_under_openclaw(self) -> None:
        home = openclaw_home([])
        workspace = os.path.join(home, ".openclaw", "workspace-openclaw")
        os.makedirs(workspace)
        item = record("run", harness="codex", role="worker", cwd=workspace,
                      native_id="01a1", root_id=f"unattached:{workspace}")
        g = collector(openclaw_home=home)
        self.addCleanup(g.close)
        (stage,) = g._co_execution_stages([ExecutionEvidence(item)], NOW, False)
        self.assertEqual((stage.dir, stage.label, stage.unattached),
                         (os.path.join(home, ".openclaw", "agents", "openclaw"), "Bot", True))

    def test_a_run_with_a_known_requester_attaches_to_that_openclaw_session(self) -> None:
        home = openclaw_home([("agent:openclaw:cron:job-1", 30, "cron", "Automation: old-name")],
                         cron_jobs={"job-1": "nightly sweep"})
        workspace = os.path.join(home, ".openclaw", "workspace-openclaw")
        parent = execution_key("openclaw", "openclaw", "agent:openclaw:cron:job-1")
        child = record("run", harness="codex", role="worker", parent_id=parent, root_id=parent,
                       cwd=workspace, native_id="01a1")
        (openclaw,) = collect._co_openclaw_stages(NOW, False, home)
        g = collector()
        self.addCleanup(g.close)
        rows = collect._co_attach_executions([openclaw] + g._co_execution_stages([ExecutionEvidence(child)], NOW, False))
        self.assertEqual(rows, [openclaw])
        self.assertEqual([worker.native_id for worker in openclaw.snap.workers], ["01a1"])


class NativeChildTags(unittest.TestCase):
    """5. Native subagents of an Bot or OpenCode session are SUB, and a fold keeps that provenance."""

    def test_an_openclaw_subagent_carries_native_linkage(self) -> None:
        worker = collect._co_openclaw_worker("agent:openclaw:subagent:abc", "researcher", {"model": "m1"}, 0.0, NOW, NOW)
        self.assertEqual(worker.linkage, "native")
        row = station(workers=[worker])
        collect._co_level_tags([row])
        self.assertEqual(worker.level_tag, "SUB")

    def opencode_child(self) -> Worker:
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local", "share", "opencode"))
        conn = sqlite3.connect(os.path.join(home, ".local", "share", "opencode", "opencode.db"))
        conn.execute("CREATE TABLE session_v2 (id TEXT, parent_id TEXT, title TEXT, agent TEXT, model TEXT,"
                     " time_created INTEGER, time_updated INTEGER, time_idle INTEGER, idle_outcome TEXT,"
                     " time_archived INTEGER)")
        ms = int(NOW * 1000)
        conn.execute("INSERT INTO session_v2 VALUES ('kid', 'sid', 'map the tree', 'general', ?, ?, ?, ?, ?, NULL)",
                     (json.dumps({"id": "deepseek-v4"}), ms - 60_000, ms - 5_000, ms - 5_000, "succeeded"))
        conn.commit()
        conn.close()
        with mock.patch.dict(os.environ, {"HOME": home}):
            (worker,) = collect._co_opencode_children("sid", NOW)
        return worker

    def test_an_opencode_subagent_carries_native_linkage(self) -> None:
        worker = self.opencode_child()
        self.assertEqual(worker.linkage, "native")
        row = station(workers=[worker])
        collect._co_level_tags([row])
        self.assertEqual(worker.level_tag, "SUB")

    def test_a_fold_keeps_native_linkage_over_unknown(self) -> None:
        start, end = NOW - 900, NOW - 600
        registry_row = Worker("map the tree", DONE, start=start, end=end, pid=4242, execution_id="exec-1")
        native_row = Worker("map the tree", DONE, start=start, end=end, pid=4242, linkage="native")
        row = station(workers=[registry_row, native_row])

        collect._co_attach_executions([row])
        collect._co_level_tags([row])

        (merged,) = row.snap.workers
        self.assertEqual(merged.linkage, "native")
        self.assertEqual(merged.level_tag, "SUB")


def cron_home() -> tuple[str, sqlite3.Connection, str]:
    """A fake home with OpenClaw's state database in WAL mode, kept open so renames stay in the -wal file."""
    home = tempfile.mkdtemp()
    state_dir = os.path.join(home, ".openclaw", "state")
    os.makedirs(state_dir)
    db = os.path.join(state_dir, "openclaw.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE cron_jobs (job_id TEXT, name TEXT)")
    conn.execute("INSERT INTO cron_jobs VALUES ('job-1', 'old name')")
    conn.commit()
    return home, conn, db


class CronNameCache(unittest.TestCase):
    """6. A cron rename that only reaches the -wal file is seen; the cache also expires on its own."""

    def setUp(self) -> None:
        collect._co_openclaw_cron_cache.clear()

    def test_a_rename_that_only_reaches_the_wal_is_seen(self) -> None:
        home, conn, _ = cron_home()
        self.addCleanup(conn.close)
        self.assertEqual(collect._co_openclaw_cron_names(home), {"job-1": "old name"})
        conn.execute("UPDATE cron_jobs SET name = 'new name' WHERE job_id = 'job-1'")
        conn.commit()
        self.assertEqual(collect._co_openclaw_cron_names(home), {"job-1": "new name"})

    def test_the_cache_is_reread_after_sixty_seconds(self) -> None:
        home, conn, _ = cron_home()
        self.addCleanup(conn.close)
        with mock.patch.object(collect.time, "monotonic", side_effect=[1000.0, 1030.0, 1061.0]), \
                mock.patch.object(collect.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            collect._co_openclaw_cron_names(home)
            collect._co_openclaw_cron_names(home)     # 30 s later: still cached
            self.assertEqual(connect.call_count, 1)
            collect._co_openclaw_cron_names(home)     # 61 s later: read again
            self.assertEqual(connect.call_count, 2)


if __name__ == "__main__":
    unittest.main()
