from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import UTC, datetime
from unittest import mock

import collect
from logparse import LogState
from model import (
    DONE,
    IDLE_KEEP_S,
    RESCAN_S,
    RUNNING,
    SESSION_GAP_S,
    ExecutionEvidence,
    ExecutionRecord,
    GlobalSnapshot,
    LeadInfo,
    QuotaAccount,
    Snapshot,
    Stage,
    Worker,
)

DEAD_PID = 4194304  # beyond a normal pid_max, so /proc never has it
LIVE_PID = 4000001  # reported alive only by the mocked liveness check
NOW = 1_800_000_000.0
RECENT = NOW - 60
OLD = NOW - IDLE_KEEP_S - 3600
IDLE = NOW - 3 * SESSION_GAP_S   # quiet past a session, still inside the keep window


def iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=UTC).isoformat()


def write(path: str, text: str = "", mtime: float | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def make_stage(root: str, rel: str) -> str:
    stage = os.path.join(root, rel, ".stage")
    os.makedirs(stage, exist_ok=True)
    return stage


def add_worker(stage: str, mtime: float, name: str = "w", pid: int = DEAD_PID) -> None:
    # the worker's own dir keeps Collector from treating its pid file as a service
    os.makedirs(os.path.join(stage, name), exist_ok=True)
    write(os.path.join(stage, name + ".pid"), str(pid), mtime)
    write(os.path.join(stage, name + ".log"), "", mtime)


def only_live(pid: int, *_rest: object) -> bool:
    return pid == LIVE_PID


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.ledger = os.path.join(home.name, "runs.jsonl")  # never the real ledger
        # the real /proc holds real sessions (this one included): none may become a virtual station here
        patch = mock.patch.object(collect, "_co_orphan_leads", return_value={})
        patch.start()
        self.addCleanup(patch.stop)

    def make(self, roots: list[str] | None = None, quota: bool = False) -> collect.GlobalCollector:
        g = collect.GlobalCollector(roots or [self.root], ledger=self.ledger, quota=quota)
        self.addCleanup(g.close)
        return g

    def test_quick_snapshot_skips_native_scan_and_full_follows(self) -> None:
        g = self.make()
        with mock.patch.object(collect, "read_native_executions", return_value=[]) as scan, \
                mock.patch.dict(collect._co_native_cache, clear=True):
            g.snapshot(now=NOW, quick=True)
            scan.assert_not_called()
            self.assertFalse(g._co_quick)
            g.snapshot(now=NOW)
            self.assertTrue(scan.called)   # a quick pass leaves nothing cached that would hide the full scan

    def test_snapshot_build_error_is_reported(self) -> None:
        g = self.make()
        with mock.patch.object(g, "_co_build", side_effect=RuntimeError("broken fixture")):
            snap = g.snapshot(now=NOW)
        self.assertEqual(snap.errors, ["collector: RuntimeError: broken fixture"])

    def test_snapshot_build_error_keeps_last_good_generated_counts(self) -> None:
        g = self.make()
        good = GlobalSnapshot(now=NOW - 1, hidden_idle=4, dismissed=2)
        with mock.patch.object(g, "_co_build", side_effect=[good, RuntimeError("broken fixture")]):
            self.assertIs(g.snapshot(now=NOW), good)
            failed = g.snapshot(now=NOW + 1)
        self.assertEqual((failed.now, failed.hidden_idle, failed.dismissed), (good.now, 4, 2))

    def test_lead_start_scopes_session_and_keeps_older_live_worker(self) -> None:
        old_done = Worker("old", DONE, start=NOW - 500, end=NOW - 400)
        old_live = Worker("still-running", RUNNING, start=NOW - 300)
        new = Worker("new", RUNNING, start=NOW - 100)
        start, hidden = collect._co_scope_session([old_done, old_live, new], [], NOW, False, NOW - 200)
        visible = [w.name for w in (old_done, old_live, new) if w.start >= start or w.state in ("running", "quiet", "stalled")]
        self.assertEqual(start, NOW - 200)
        self.assertEqual(hidden, 1)
        self.assertEqual(visible, ["still-running", "new"])

    def test_new_lead_hides_all_workers_started_before_its_start(self) -> None:
        old_live = Worker("old-live", RUNNING, start=NOW - 300)
        new = Worker("new-lead-worker", RUNNING, start=NOW - 100)
        visible = collect._co_filter_session([old_live, new], NOW - 200, False, lead_active=True)
        self.assertEqual([worker.name for worker in visible], ["new-lead-worker"])
        _, hidden = collect._co_scope_session([old_live, new], [], NOW, False, NOW - 200, lead_active=True)
        self.assertEqual(hidden, 1)

    def test_closed_lead_keeps_older_running_worker_until_it_finishes(self) -> None:
        old_live = Worker("still-running", RUNNING, start=NOW - 300)
        visible = collect._co_filter_session([old_live], NOW - 200, False, lead_active=False)
        self.assertEqual([worker.name for worker in visible], ["still-running"])

    def test_missing_lead_uses_gap_fallback(self) -> None:
        earlier = Worker("earlier", DONE, start=NOW - 10_000, end=NOW - 9000)
        latest = Worker("latest", DONE, start=NOW - 100, end=NOW - 50)
        start, hidden = collect._co_scope_session([earlier, latest], [], NOW, False)
        self.assertEqual(start, latest.start)
        self.assertEqual(hidden, 1)


class FindStagesTest(Base):
    def test_depth_limit(self) -> None:
        make_stage(self.root, "a/b/c")  # level 4: found
        make_stage(self.root, "a/b/c/d")  # level 5: too deep
        found = collect.find_stages([self.root], depth=4)
        self.assertEqual(found, [(os.path.join(self.root, "a/b/c/.stage"), "c")])

    def test_nested_stage_is_skipped(self) -> None:
        stage = make_stage(self.root, "p")
        make_stage(stage, "inner")
        found = collect.find_stages([self.root])
        self.assertEqual(found, [(stage, "p")])

    def test_subfolder_stage_found_and_sorted_by_label(self) -> None:
        proj = make_stage(self.root, "proj")
        sub = make_stage(self.root, "proj/sub")
        found = collect.find_stages([self.root])
        self.assertEqual(found, [(proj, "proj"), (sub, "proj/sub")])

    def test_dot_dirs_and_build_dirs_skipped(self) -> None:
        for name in (".hidden", "node_modules", "__pycache__", "target", "dist", "build"):
            make_stage(self.root, f"{name}/x")
        ok = make_stage(self.root, "ok")
        self.assertEqual(collect.find_stages([self.root]), [(ok, "ok")])

    def test_symlinks_not_followed(self) -> None:
        real = make_stage(self.root, "real/x")
        os.symlink(os.path.join(self.root, "real"), os.path.join(self.root, "link"))
        os.makedirs(os.path.join(self.root, "y"))
        os.symlink(os.path.dirname(real), os.path.join(self.root, "y", ".stage"))
        self.assertEqual(collect.find_stages([self.root]), [(real, "x")])

    def test_root_holding_stage_labels_from_its_parent(self) -> None:
        root = os.path.join(self.root, "system-stuff")
        top = make_stage(root, "")
        ports = make_stage(root, "ports")
        self.assertEqual(
            collect.find_stages([root]),
            [(top, "system-stuff"), (ports, "system-stuff/ports")],
        )

    def test_plain_root_labels_from_root(self) -> None:
        projects = os.path.join(self.root, "Projects")
        backend = make_stage(projects, "worker-dashboard/backend")
        self.assertEqual(
            collect.find_stages([projects]), [(backend, "backend")]
        )
        parent = make_stage(projects, "worker-dashboard")
        self.assertEqual(collect.find_stages([projects]),
                         [(parent, "worker-dashboard"), (backend, "worker-dashboard/backend")])

    def test_any_hidden_dir_with_status_sh_is_a_stage(self) -> None:
        staging = os.path.join(self.root, "proj", ".staging")
        os.makedirs(staging)
        open(os.path.join(staging, "station"), "w").close()
        open(os.path.join(staging, "PROGRESS.md"), "w").close()
        os.makedirs(os.path.join(self.root, "proj", ".cache"))
        agents = os.path.join(self.root, ".agents")
        os.makedirs(agents)
        open(os.path.join(agents, "station"), "w").close()
        self.assertEqual(collect.find_stages([self.root]), [(staging, "proj"), (agents, os.path.basename(self.root))])

    def test_missing_and_file_roots_ignored(self) -> None:
        file_root = os.path.join(self.root, "plain.txt")
        write(file_root, "x")
        missing = os.path.join(self.root, "nope")
        self.assertEqual(collect.find_stages([missing, file_root]), [])


class IdleTest(Base):
    def test_idle_hidden_unless_all_history(self) -> None:
        add_worker(make_stage(self.root, "old"), OLD)
        add_worker(make_stage(self.root, "fresh"), RECENT)
        g = self.make()
        snap = g.snapshot(now=NOW)
        self.assertEqual([s.label for s in snap.stages], ["fresh"])
        self.assertEqual(snap.hidden_idle, 1)
        full = g.snapshot(now=NOW, all_history=True)
        self.assertTrue(full.all_history)
        self.assertEqual(sorted(s.label for s in full.stages), ["fresh", "old"])
        self.assertEqual(full.hidden_idle, 0)

    def test_idle_stage_snapshot_is_not_built(self) -> None:
        fresh = make_stage(self.root, "fresh")
        add_worker(fresh, RECENT)
        add_worker(make_stage(self.root, "old"), OLD)
        built: list[str] = []
        real = collect.Collector.snapshot

        def spy(collector: collect.Collector, now: float | None = None, all_history: bool = False, procs=None, lead_since=None, lead_active=False):
            built.append(collector._co_stage)
            return real(collector, now, all_history, procs, lead_since, lead_active)

        g = self.make()
        with mock.patch.object(collect.Collector, "snapshot", spy):
            g.snapshot(now=NOW)
        self.assertEqual(built, [fresh])

    def test_quiet_stage_inside_keep_window_is_listed_with_its_age(self) -> None:
        add_worker(make_stage(self.root, "quiet"), IDLE)
        snap = self.make().snapshot(now=NOW)
        self.assertEqual([s.label for s in snap.stages], ["quiet"])
        self.assertEqual(snap.hidden_idle, 0)

    def test_stage_without_files_is_idle(self) -> None:
        make_stage(self.root, "empty")
        snap = self.make().snapshot(now=NOW)
        self.assertEqual(snap.stages, [])
        self.assertEqual(snap.hidden_idle, 1)

    def test_live_pid_keeps_old_stage_visible(self) -> None:
        add_worker(make_stage(self.root, "running"), OLD, pid=LIVE_PID)
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = self.make().snapshot(now=NOW)
        self.assertEqual([(s.label, s.live) for s in snap.stages], [("running", 1)])
        self.assertEqual(snap.hidden_idle, 0)


class OrderTest(Base):
    def test_rows_sort_by_active_idle_closed_then_activity(self) -> None:
        rows = [
            Stage(path="/closed", label="z", state="closed", activity=NOW),
            Stage(path="/idle-old", label="b", state="idle", activity=NOW - 600),
            Stage(path="/active-old", label="a", state="active", activity=NOW - 120),
            Stage(path="/idle-new", label="a", state="idle", activity=NOW - 300),
            Stage(path="/active-new", label="b", state="active", activity=NOW - 30),
        ]
        collect._co_order_stages(rows)
        self.assertEqual([row.path for row in rows],
                         ["/active-new", "/active-old", "/idle-new", "/idle-old", "/closed"])

    def test_duplicate_session_id_prefers_real_stage_with_workers_and_empty_ids_stay_distinct(self) -> None:
        virtual = Stage(path="/r/.stage#7", label="virtual", session_id="7c9c058f")
        worker = Worker("w", DONE)
        real = Stage(path="/r/.stage", dir="/r/.stage", label="Projects/sample-project",
                     snap=Snapshot(workers=[worker]), session_id="7c9c058f")
        empty_a = Stage(path="/r/.stage#a", label="codex")
        empty_b = Stage(path="/r/.stage#b", label="opencode")
        rows = collect._co_dedupe_sessions([virtual, real, empty_a, empty_b])
        self.assertEqual(rows, [real, empty_a, empty_b])

    def test_active_then_idle_by_recency_then_label(self) -> None:
        add_worker(make_stage(self.root, "b-live"), OLD, name="w", pid=LIVE_PID)
        add_worker(make_stage(self.root, "z-recent"), NOW - 60)
        add_worker(make_stage(self.root, "a-older"), NOW - 600)
        add_worker(make_stage(self.root, "b-tie"), NOW - 1200)
        add_worker(make_stage(self.root, "a-tie"), NOW - 1200)
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = self.make().snapshot(now=NOW)
        self.assertEqual(
            [s.label for s in snap.stages],
            ["b-live", "z-recent", "a-older", "a-tie", "b-tie"],   # running workers keep a row active even with no lead
        )
        self.assertEqual(snap.stages[1].last_activity, NOW - 60)

    def test_each_session_row_holds_only_its_own_tasks(self) -> None:
        stage = make_stage(self.root, "proj")
        add_worker(stage, NOW - 900, name="old-a")
        add_worker(stage, NOW - 100, name="new-b")
        write(os.path.join(stage, "old-a.session"), json.dumps({"lead_session": "s-old"}))
        write(os.path.join(stage, "new-b.session"), json.dumps({"lead_session": "s-new"}))
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = self.make().snapshot(now=NOW)
        rows = {s.session_id: s for s in snap.stages}
        self.assertEqual(set(rows), {"s-old", "s-new"})
        self.assertEqual([[w.name for w in rows[sid].snap.workers] for sid in ("s-old", "s-new")],
                         [["old-a"], ["new-b"]])
        for sid, name in (("s-old", "old-a"), ("s-new", "new-b")):
            held = [n for g in rows[sid].snap.groups for n in g.workers]
            self.assertEqual(held, [name])                       # no task of the other session leaks in
            self.assertTrue(rows[sid].snap.groups[0].current)


class LeadTest(Base):
    def stage_at(self) -> str:
        stage = make_stage(self.root, "p")
        add_worker(stage, RECENT)
        return stage

    def lead(self) -> LeadInfo | None:
        snap = self.make().snapshot(now=NOW)
        self.assertEqual(len(snap.stages), 1)
        return snap.stages[0].lead

    def write_ledger(self, rows: list[dict[str, str]]) -> None:
        write(self.ledger, "".join(json.dumps(row) + "\n" for row in rows))

    def test_fresh_lead_json(self) -> None:
        stage = self.stage_at()
        write(
            os.path.join(stage, "lead.json"),
            json.dumps(
                {
                    "lead_label": "Luna",
                    "lead_model": "gpt-x",
                    "phase": "build",
                    "stream": "docs/streams/g-collect.md",
                    "updated_at": iso(NOW - 120),
                }
            ),
        )
        self.assertEqual(self.lead(), LeadInfo(model="Luna", phase="build", stream="g-collect"))

    def test_lead_model_used_without_label(self) -> None:
        stage = self.stage_at()
        write(os.path.join(stage, "lead.json"), json.dumps({"lead_model": "opus", "updated_at": iso(NOW)}))
        self.assertEqual(self.lead(), LeadInfo(model="opus"))

    def test_stale_lead_json_falls_back_to_ledger(self) -> None:
        stage = self.stage_at()
        write(os.path.join(stage, "lead.json"), json.dumps({"lead_label": "Luna", "updated_at": iso(OLD)}))
        self.write_ledger([{"ts": iso(NOW - 300), "stage": stage, "lead": "opus"}])
        self.assertEqual(self.lead(), LeadInfo(model="opus"))

    def test_ledger_only(self) -> None:
        stage = self.stage_at()
        self.write_ledger([{"ts": iso(NOW - 300), "stage": stage, "lead": "Luna"}])
        self.assertEqual(self.lead(), LeadInfo(model="Luna"))

    def test_ledger_tilde_path_is_expanded(self) -> None:
        stage = self.stage_at()
        home = os.path.expanduser("~")
        if not stage.startswith(home + os.sep):
            self.skipTest("stage is outside home")
        self.write_ledger([{"ts": iso(NOW - 300), "stage": "~" + stage[len(home):], "lead": "Luna"}])
        self.assertEqual(self.lead(), LeadInfo(model="Luna"))

    def test_newest_ledger_row_for_stage_wins(self) -> None:
        stage = self.stage_at()
        self.write_ledger(
            [
                {"ts": iso(NOW - 100), "stage": stage, "lead": "New"},
                {"ts": iso(NOW - 900), "stage": stage, "lead": "Old"},
            ]
        )
        self.assertEqual(self.lead(), LeadInfo(model="New"))

    def test_stale_ledger_row_gives_none(self) -> None:
        stage = self.stage_at()
        self.write_ledger([{"ts": iso(OLD), "stage": stage, "lead": "opus"}])
        self.assertIsNone(self.lead())

    def test_other_stage_in_ledger_ignored(self) -> None:
        self.stage_at()
        self.write_ledger([{"ts": iso(NOW - 60), "stage": "/elsewhere/.stage", "lead": "opus"}])
        self.assertIsNone(self.lead())

    def test_missing_ledger_gives_none(self) -> None:
        self.stage_at()
        self.assertIsNone(self.lead())

    def test_binary_lead_json_gives_no_crash(self) -> None:
        stage = self.stage_at()
        with open(os.path.join(stage, "lead.json"), "wb") as fh:
            fh.write(b"\xff\xfe{ not json")
        self.assertIsNone(self.lead())

    def test_bad_lead_json_uses_ledger(self) -> None:
        stage = self.stage_at()
        with open(os.path.join(stage, "lead.json"), "wb") as fh:
            fh.write(b"\xff\xfe{ not json")
        self.write_ledger([{"ts": iso(NOW - 300), "stage": stage, "lead": "opus"}])
        self.assertEqual(self.lead(), LeadInfo(model="opus"))

    def test_unparseable_updated_at_uses_file_mtime(self) -> None:
        stage = self.stage_at()
        write(
            os.path.join(stage, "lead.json"),
            json.dumps({"lead_label": "Luna", "updated_at": "soon"}),
            mtime=RECENT,
        )
        self.assertEqual(self.lead(), LeadInfo(model="Luna"))

    def test_lead_json_not_an_object_gives_none(self) -> None:
        stage = self.stage_at()
        write(os.path.join(stage, "lead.json"), "[1, 2]")
        self.assertIsNone(self.lead())

    def test_lead_json_as_directory_gives_no_crash(self) -> None:
        stage = self.stage_at()
        os.makedirs(os.path.join(stage, "lead.json"))
        self.assertIsNone(self.lead())


class TotalsTest(Base):
    def test_a_subscription_route_keeps_no_cost_in_the_totals(self) -> None:
        """7. The gate runs once, before aggregation: a subscription's tokens count, its USD does not."""
        make_stage(self.root, "a")

        def fake(
            collector: collect.Collector, now: float | None = None, all_history: bool = False, procs=None, lead_since=None, lead_active=False
        ) -> Snapshot:
            return Snapshot(workers=[
                Worker("sub", DONE, model="luna", log=LogState(tokens_in=10, cost=1.25)),
                Worker("api", DONE, account="openrouter", log=LogState(tokens_in=5, cost=0.50)),
            ])

        g = self.make()
        with mock.patch.object(collect.Collector, "snapshot", fake):
            snap = g.snapshot(now=NOW, all_history=True)
        self.assertEqual((snap.tokens_total, snap.cost_total), (15, 0.50))

    def test_sums_and_none_handling(self) -> None:
        """Totals come from the final worker rows of each stage, not from per-stage totals cached earlier."""
        for name in ("a", "b", "c"):
            make_stage(self.root, name)
        values: dict[str, tuple[int | None, float | None]] = {}

        def fake(
            collector: collect.Collector, now: float | None = None, all_history: bool = False, procs=None, lead_since=None, lead_active=False
        ) -> Snapshot:
            tokens, cost = values[os.path.basename(os.path.dirname(collector._co_stage))]
            return Snapshot(workers=[Worker("w", DONE, account="openrouter",
                                            log=LogState(tokens_in=tokens, cost=cost))])

        cases: list[tuple[dict[str, tuple[int | None, float | None]], tuple[int | None, float | None]]] = [
            ({"a": (100, 0.5), "b": (50, None), "c": (None, None)}, (150, 0.5)),
            ({"a": (None, None), "b": (None, None), "c": (None, None)}, (None, None)),
            ({"a": (None, 0.25), "b": (None, None), "c": (None, None)}, (None, 0.25)),
        ]
        g = self.make()
        for case, expected in cases:
            values.clear()
            values.update(case)
            with mock.patch.object(collect.Collector, "snapshot", fake):
                snap = g.snapshot(now=NOW, all_history=True)
            self.assertEqual((snap.tokens_total, snap.cost_total), expected)


class ExecutionTotalsTest(Base):
    """6. Stage and global totals are recomputed from the final worker rows, after retention."""

    def snapshot_with(self, items: list[ExecutionEvidence]) -> GlobalSnapshot:
        g = self.make()
        g._co_proc_table = dict
        with mock.patch.object(g, "_co_executions", return_value=items), \
                mock.patch.object(collect, "_co_openclaw_stages", return_value=[]):
            return g.snapshot(now=NOW)

    def root_and_child(self, total_tokens: int, cost: float) -> list[ExecutionEvidence]:
        root = ExecutionRecord("root", harness="claude", account="openrouter", role="lead", cwd=self.root,
                               started=NOW - 600, activity=NOW - 300, state="finished")
        child = ExecutionRecord("kid", harness="claude", account="openrouter", role="worker", parent_id="root",
                                root_id="root", cwd=self.root, started=NOW - 500, activity=NOW - 300,
                                state="finished", outcome="success")
        child.tokens_in, child.cost = total_tokens, cost
        return [ExecutionEvidence(root), ExecutionEvidence(child)]

    def test_an_execution_stage_totals_its_own_worker_rows(self) -> None:
        snap = self.snapshot_with(self.root_and_child(100, 0.50))
        stage = next(stage for stage in snap.stages if stage.execution_id == "root")
        self.assertEqual((stage.snap.tokens_total, stage.snap.cost_total), (100, 0.50))
        self.assertEqual((snap.tokens_total, snap.cost_total), (100, 0.50))

    def test_a_worker_removed_by_retention_no_longer_counts(self) -> None:
        old = ExecutionRecord("old", harness="claude", account="openrouter", role="lead", cwd=self.root,
                              started=NOW - 3 * 86400, activity=NOW - 3 * 86400, state="finished")
        old.tokens_in, old.cost = 100, 0.50
        recent = ExecutionRecord("recent", harness="claude", account="openrouter", role="lead", cwd=self.root,
                                 started=NOW - 600, activity=NOW - 300, state="finished")
        child = ExecutionRecord("recent-kid", harness="claude", account="openrouter", role="worker",
                                parent_id="recent", root_id="recent", cwd=self.root, started=NOW - 500,
                                activity=NOW - 300, state="finished", outcome="success")
        child.tokens_in, child.cost = 40, 0.20
        snap = self.snapshot_with([ExecutionEvidence(old), ExecutionEvidence(recent), ExecutionEvidence(child)])
        self.assertEqual([stage.execution_id for stage in snap.stages], ["recent"])
        self.assertEqual((snap.tokens_total, snap.cost_total), (40, 0.20))


class CollectorLifetimeTest(Base):
    def test_collector_dropped_when_stage_vanishes(self) -> None:
        a = make_stage(self.root, "a")
        b = make_stage(self.root, "b")
        g = self.make()
        with mock.patch.object(collect.time, "monotonic", return_value=0.0):
            g.snapshot(now=NOW, all_history=True)
        keep_b = g.collector_for(b)
        self.assertIsNotNone(g.collector_for(a))
        shutil.rmtree(os.path.dirname(a))
        with mock.patch.object(collect.time, "monotonic", return_value=RESCAN_S + 1.0):
            g.snapshot(now=NOW, all_history=True)
        self.assertIsNone(g.collector_for(a))
        self.assertIs(g.collector_for(b), keep_b)

    def test_rescan_is_throttled(self) -> None:
        make_stage(self.root, "a")
        g = self.make()
        with mock.patch.object(collect.time, "monotonic", return_value=100.0):
            g.snapshot(now=NOW, all_history=True)
        b = make_stage(self.root, "b")
        with mock.patch.object(collect.time, "monotonic", return_value=100.0 + RESCAN_S - 1):
            snap = g.snapshot(now=NOW, all_history=True)
        self.assertEqual([s.label for s in snap.stages], ["a"])
        self.assertIsNone(g.collector_for(b))
        with mock.patch.object(collect.time, "monotonic", return_value=100.0 + RESCAN_S + 1):
            snap = g.snapshot(now=NOW, all_history=True)
        self.assertEqual([s.label for s in snap.stages], ["a", "b"])

    def test_detail_and_unknown_stage(self) -> None:
        stage = make_stage(self.root, "p")
        add_worker(stage, RECENT)
        write(os.path.join(stage, "w", "REPORT.md"), "## Summary\n- done\n")
        g = self.make()
        g.snapshot(now=NOW)
        self.assertIsNone(g.collector_for("/nowhere/.stage"))
        self.assertEqual(g.detail(stage, "w", "report"), ["## Summary", "- done"])
        self.assertEqual(g.detail("/nowhere/.stage", "w", "report"), [])
        self.assertEqual(g.detail(stage, "w", "live"), [])

    def test_one_quota_reader_marks_in_use(self) -> None:
        stage = make_stage(self.root, "p")
        add_worker(stage, RECENT)
        write(os.path.join(stage, "w.model"), "gpt-luna")
        accounts = [QuotaAccount(name="codex"), QuotaAccount(name="claude")]
        with mock.patch.object(collect.Collector, "_co_run_quota", return_value=accounts):
            g = self.make(quota=True)
            deadline = time.monotonic() + 5
            while not g._co_quota._co_quota_ok() and time.monotonic() < deadline:
                time.sleep(0.01)
        snap = g.snapshot(now=NOW)
        self.assertTrue(snap.quota_ok)
        self.assertEqual([(a.name, a.in_use) for a in snap.quota], [("codex", True), ("claude", False)])

    def test_quota_reader_attaches_only_a_cap_that_cost_something(self) -> None:
        payload = {"subscriptions": [{"id": "opencode-go", "account": "opencode-go:desktop-linux", "limits": []},
                                     {"id": "claude", "account": "claude:desktop-linux", "limits": []}],
                   "extra": [{"account": "opencode-go:desktop-linux", "usd": 7.5, "method": "measured", "startAt": 1_000_000},
                             {"account": "claude:desktop-linux", "usd": 0, "method": "vendor", "startAt": 1_000_000},
                             {"account": "cursor:desktop-linux", "usd": None, "method": "unknown", "startAt": 1_000_000}]}
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload))
        c = collect.Collector(self.root, quota=False)
        self.addCleanup(c.close)
        c._co_quota_cmd = ["quota"]
        with mock.patch.object(collect.subprocess, "run", return_value=done):
            accounts = c._co_run_quota()
        self.assertEqual([(a.name, a.extra is not None) for a in accounts], [("opencode-go", True), ("claude", False)])
        self.assertEqual((accounts[0].extra.usd, accounts[0].extra.since), (7.5, 1000.0))

    def test_default_collector_keeps_quota_thread(self) -> None:
        with mock.patch.dict(os.environ, {"STATUS_QUOTA_CMD": "/bin/false", "STATUS_OPENROUTER_CMD": "/bin/false"}):
            c = collect.Collector(self.root)
            self.addCleanup(c.close)
        self.assertIsNotNone(c._co_thread)
        off = collect.Collector(self.root, quota=False)
        self.addCleanup(off.close)
        self.assertIsNone(off._co_thread)


if __name__ == "__main__":
    unittest.main()


class LeadCtxSyntheticTest(unittest.TestCase):
    def test_zero_usage_synthetic_message_is_skipped(self) -> None:
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        cwd = "/x/proj"
        os.makedirs(os.path.join(home, ".claude", "sessions"))
        proj = os.path.join(home, ".claude", "projects", "-x-proj")
        os.makedirs(proj)
        with open(os.path.join(home, ".claude", "sessions", "42.json"), "w") as fh:
            json.dump({"sessionId": "s1", "cwd": cwd}, fh)
        real = {"type": "assistant", "message": {"usage": {
            "input_tokens": 2, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 90}}}
        synth = {"type": "assistant", "message": {"usage": {
            "input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}
        with open(os.path.join(proj, "s1.jsonl"), "w") as fh:
            fh.write(json.dumps(real) + "\n" + json.dumps(synth) + "\n")
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(collect._co_lead_ctx(42, "claude"), 102)


class LeadCtxCompactTest(unittest.TestCase):
    def ctx_after(self, rows: list[dict]) -> int | None:
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, ".claude", "sessions"))
        proj = os.path.join(home, ".claude", "projects", "-x-proj")
        os.makedirs(proj)
        with open(os.path.join(home, ".claude", "sessions", "43.json"), "w") as fh:
            json.dump({"sessionId": "s2", "cwd": "/x/proj"}, fh)
        with open(os.path.join(proj, "s2.jsonl"), "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        with mock.patch.dict(os.environ, {"HOME": home}):
            return collect._co_lead_ctx(43, "claude")

    def test_a_compact_newer_than_the_last_reply_sets_the_window(self) -> None:
        reply = {"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": 130000}}}
        boundary = {"type": "system", "subtype": "compact_boundary",
                    "compactMetadata": {"trigger": "manual", "preTokens": 130001, "postTokens": 9600}}
        self.assertEqual(self.ctx_after([reply, boundary]), 9600)

    def test_a_reply_after_the_compact_wins(self) -> None:
        boundary = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"postTokens": 9600}}
        reply = {"type": "assistant", "message": {"usage": {"input_tokens": 1, "cache_read_input_tokens": 14999}}}
        self.assertEqual(self.ctx_after([boundary, reply]), 15000)


class ClosedLeadStationTest(Base):
    def test_closed_lead_with_nothing_running_stays_listed_as_idle(self) -> None:
        stage = os.path.join(self.root, "p", ".stage")
        os.makedirs(stage)
        write(os.path.join(stage, "PROGRESS.md"), "09:55 recent milestone\n", NOW - 60)
        add_worker(stage, NOW - 120)
        g = self.make()
        g._co_last_lead_since[stage] = NOW - 600     # a lead was seen here; none is running now
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = g.snapshot(now=NOW)
        self.assertEqual([st.path for st in snap.stages], [stage + "#unlinked"])
        self.assertTrue(snap.stages[0].unlinked)
        self.assertEqual(snap.stages[0].lead_state, "gone")
        self.assertEqual(snap.stages[0].state, "idle")

    def test_station_untouched_past_the_keep_window_is_hidden(self) -> None:
        stage = os.path.join(self.root, "p", ".stage")
        os.makedirs(stage)
        write(os.path.join(stage, "PROGRESS.md"), "09:55 old milestone\n", NOW - IDLE_KEEP_S - 60)
        g = self.make()
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = g.snapshot(now=NOW)
        self.assertEqual((snap.stages, snap.hidden_idle), ([], 1))


class StageOrderTest(Base):
    def test_running_first_then_finished_then_empty(self) -> None:
        for name, kind in (("a-empty", None), ("b-done", "done"), ("c-run", "run")):
            stage = os.path.join(self.root, name, ".stage")
            os.makedirs(stage)
            write(os.path.join(stage, "PROGRESS.md"), "09:55 x\n", NOW - 60)
            if kind:
                add_worker(stage, NOW - 30, pid=LIVE_PID if kind == "run" else DEAD_PID)
                if kind == "done":
                    write(os.path.join(stage, "w", "REPORT.md"), "## Summary\nok\n", NOW - 20)
        g = self.make()
        with mock.patch.object(collect, "_co_pid_live", side_effect=only_live):
            snap = g.snapshot(now=NOW, all_history=True)
        self.assertEqual([os.path.basename(os.path.dirname(st.path)) for st in snap.stages][:3],
                         ["c-run", "b-done", "a-empty"])   # a row with running workers is active


class ClosedAgeOutTests(unittest.TestCase):
    def test_closed_session_is_only_cost_bounded_before_the_ops_window(self) -> None:
        """The early filter is a hard cost bound (14 days); the real 24 h decision waits for work state."""
        from collect import _co_closed_aged_out
        from model import Stage
        now = 1_000_000_000.0
        def stage(state: str, age: float, live: int = 0) -> Stage:
            return Stage(path="/x", label="x", state=state, live=live, activity=now - age)
        loose = collect._co_loose_keep_s
        self.assertTrue(_co_closed_aged_out(stage("closed", loose + 60), now))
        self.assertFalse(_co_closed_aged_out(stage("closed", loose - 60), now))
        self.assertFalse(_co_closed_aged_out(stage("closed", 3 * 86400), now),
                         "three days old is the ops window's call, after attach_work")
        self.assertFalse(_co_closed_aged_out(stage("idle", loose + 60), now))
        self.assertFalse(_co_closed_aged_out(stage("closed", loose + 60, live=1), now))
