from __future__ import annotations

import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

import collect
from model import (
    DONE,
    NOREPORT,
    RUNNING,
    ExecutionEvidence,
    ExecutionRecord,
    RegistrySnapshot,
    Snapshot,
    Stage,
    Worker,
)


def record(identity: str, *, native_id: str = "", role: str = "worker", cwd: str = "/same",
           parent_id: str = "", root_id: str = "", linkage: str = "unknown", pid: int | None = None,
           pid_start: int | None = None, state: str = "running", outcome: str = "unknown",
           started: float = 100.0, activity: float = 100.0, goal: str = "", purpose_source: str = "") -> ExecutionRecord:
    return ExecutionRecord(identity, harness="codex", account="personal", native_id=native_id,
                           role=role, parent_id=parent_id, root_id=root_id, linkage=linkage, cwd=cwd,
                           pid=pid, pid_start=pid_start, boot_id="boot", state=state, outcome=outcome,
                           started=started, activity=activity, goal=goal, purpose_source=purpose_source)


class ReconcileTests(unittest.TestCase):
    def rows(self, registry=(), native=(), procs=None, now=500.0):
        return collect.reconcile_executions(RegistrySnapshot(records=list(registry)), list(native), procs or {}, now)[0]

    def test_same_cwd_leads_and_same_worker_names_stay_independent(self):
        a = record("a", native_id="lead-a", role="lead")
        b = record("b", native_id="lead-b", role="lead")
        wa = record("wa", native_id="worker-a", parent_id="a", root_id="a", linkage="registered")
        wb = record("wb", native_id="worker-b", parent_id="b", root_id="b", linkage="registered")
        merged = self.rows((a, b, wa, wb))
        owners = {item.record.execution_id: item.record.parent_id for item in merged}
        self.assertEqual(owners, {"a": "", "b": "", "wa": "a", "wb": "b"})
        self.assertEqual(len(merged), 4)

    def test_pid_reuse_does_not_attach_process_to_old_execution(self):
        old = record("old", pid=4242, pid_start=11, started=1)
        proc = {4242: (1, 0, 0, 99, 0)}
        merged = self.rows((old,), procs=proc)
        self.assertEqual(merged[0].record.execution_id, "old")
        self.assertEqual(merged[0].record.state, "finished")
        self.assertEqual(merged[0].record.outcome, "unknown")

    def test_detached_worker_keeps_explicit_owner_without_cwd_match(self):
        owner = record("owner", native_id="lead", role="lead", cwd="/project/a")
        worker = record("worker", native_id="child", cwd="/tmp/detached", parent_id="owner",
                        root_id="owner", linkage="registered")
        merged = self.rows((owner, worker))
        child = next(item.record for item in merged if item.record.execution_id == "worker")
        self.assertEqual((child.parent_id, child.root_id, child.linkage), ("owner", "owner", "registered"))

    def test_nested_parent_preserves_immediate_parent_and_root(self):
        root = record("root", native_id="root", role="lead")
        parent = record("parent", native_id="parent", parent_id="root", root_id="root", linkage="native")
        child = record("child", native_id="child", parent_id="parent", root_id="root", linkage="native")
        merged = self.rows((root, parent, child))
        item = next(x.record for x in merged if x.record.execution_id == "child")
        self.assertEqual((item.parent_id, item.root_id), ("parent", "root"))

    def test_cycles_and_conflicting_roots_are_visible_unlinked_with_diagnostics(self):
        cycle_a = record("cycle-a", parent_id="cycle-b", root_id="cycle-a", linkage="native")
        cycle_b = record("cycle-b", parent_id="cycle-a", root_id="cycle-a", linkage="native")
        root = record("root", role="lead")
        other = record("other", role="lead")
        conflict = record("conflict", parent_id="root", root_id="other", linkage="registered")
        merged, diagnostics = collect.reconcile_executions(
            RegistrySnapshot(records=[cycle_a, cycle_b, root, other, conflict]), [], {}, 500.0)
        by_id = {item.record.execution_id: item.record for item in merged}
        self.assertEqual((by_id["cycle-a"].parent_id, by_id["cycle-a"].linkage), ("", "conflict"))
        self.assertEqual((by_id["conflict"].parent_id, by_id["conflict"].root_id), ("", ""))
        self.assertTrue(any("cycle" in line for line in diagnostics))
        self.assertTrue(any("conflicts" in line for line in diagnostics))

    def test_a_missing_parent_with_a_known_root_is_unlinked_not_a_crash(self):
        root = record("root", role="lead")
        orphan = record("orphan", parent_id="ghost", root_id="root", linkage="registered")
        merged, diagnostics = collect.reconcile_executions(RegistrySnapshot(records=[root, orphan]), [], {}, 500.0)
        by_id = {item.record.execution_id: item.record for item in merged}
        self.assertEqual((by_id["orphan"].parent_id, by_id["orphan"].root_id), ("", ""))
        self.assertTrue(any("parent missing" in line for line in diagnostics))

    def test_duplicate_registered_native_process_sightings_merge_once(self):
        reg = record("id", native_id="session", pid=55, pid_start=44, linkage="registered")
        native = ExecutionEvidence(record("different", native_id="session", linkage="native"), brief="task")
        merged = self.rows((reg,), (native,), {55: (1, 0, 0, 44, 0)})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].record.execution_id, "id")
        self.assertEqual(merged[0].record.linkage, "registered")
        self.assertEqual(merged[0].brief, "task")

    def test_zero_exit_code_is_not_overwritten_by_native_evidence(self):
        reg = record("x", state="finished", outcome="unknown")
        reg.exit_code = 0
        native_rec = record("x", state="finished", outcome="success")
        native_rec.exit_code = 1
        merged = self.rows((reg,), (ExecutionEvidence(native_rec),))
        self.assertEqual(merged[0].record.exit_code, 0)
        self.assertEqual(merged[0].record.outcome, "success")

    def test_empty_and_unknown_fields_still_fill_from_native_evidence(self):
        reg = record("x", native_id="", state="finished", outcome="unknown", goal="")
        reg.exit_code = None
        native_rec = record("x", native_id="native-x", state="finished", outcome="success", goal="from native")
        native_rec.exit_code = 3
        merged = self.rows((reg,), (ExecutionEvidence(native_rec),))
        self.assertEqual((merged[0].record.native_id, merged[0].record.outcome, merged[0].record.goal),
                         ("native-x", "success", "from native"))
        self.assertEqual(merged[0].record.exit_code, 3)

    def test_boot_id_is_read_once_per_reconcile(self):
        procs = {10: (1, 0, 0, 5, 0), 11: (10, 0, 0, 5, 0), 12: (11, 0, 0, 5, 0)}
        registry = [record(f"p{n}", pid=n, pid_start=5, state="running") for n in (10, 11, 12)]
        with mock.patch.object(collect, "_co_read_text", return_value="boot\n") as read:
            merged = self.rows(registry, procs=procs)
        self.assertEqual(read.call_count, 1)
        self.assertEqual({item.record.state for item in merged}, {"running"})

    def test_missing_brief_does_not_create_success_evidence(self):
        item = self.rows((record("x", state="finished", outcome="unknown"),))[0]
        self.assertEqual(item.record.outcome, "unknown")
        self.assertFalse(item.record.output_available)
        self.assertEqual(item.brief, "")

    def test_worker_hydration_keeps_full_brief_and_result_separate(self):
        item = ExecutionEvidence(record("x", outcome="unknown"), brief="Goal: one\n" + "b" * 500,
                                 result="partial output")
        worker = collect._co_execution_worker(item, 500.0, False)
        self.assertEqual("\n".join(worker.brief_text), item.brief)
        self.assertEqual(worker.result_text, ["partial output"])
        self.assertEqual(worker.state, NOREPORT)
        self.assertEqual(worker.outcome, "unknown")
        self.assertTrue(worker.output_available)


class CollectorHydrationTests(unittest.TestCase):
    def test_disappeared_headless_worker_has_unknown_outcome(self):
        c = collect.Collector("", quota=False)
        self.addCleanup(c.close)
        worker = Worker("headless", RUNNING, pid=4242, start=100.0)
        c._co_ghosts[("scope", 4242)] = (worker, None)
        with mock.patch.object(collect, "_co_pid_live", return_value=False):
            gone = c._co_procs_as_workers(200.0, [], {}, {}, set(), set(), "/project", "scope")
        self.assertEqual(gone[0].state, NOREPORT)
        self.assertEqual(gone[0].outcome, "unknown")

    def test_virtual_stage_detail_uses_registry_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            execution_id = "a" * 64
            artifacts = Path(root) / "artifacts" / execution_id
            artifacts.mkdir(parents=True)
            (artifacts / "brief.txt").write_text("Full task brief\n")
            (artifacts / "result.txt").write_text("Completed evidence\n")
            g = collect.GlobalCollector([], registry_root=root, quota=False)
            self.addCleanup(g.close)
            worker = Worker("virtual-worker", DONE, execution_id=execution_id)
            stage_path = "/virtual/project/.stage#4242"
            g._co_last_stages = [Stage(path=stage_path, label="virtual", snap=Snapshot(workers=[worker]))]
            g._co_execution_evidence = {execution_id: ExecutionEvidence(record(execution_id), "observe", "Full task brief", "Completed evidence")}
            self.assertEqual(g.detail(stage_path, "virtual-worker", "brief"), ["Full task brief"])
            self.assertEqual(g.detail(stage_path, "virtual-worker", "report"), ["Completed evidence"])

    def test_two_leads_with_same_cwd_make_independent_virtual_rows(self):
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        roots = [record("lead-one", native_id="one", role="lead"),
                 record("lead-two", native_id="two", role="lead")]
        stages = g._co_execution_stages([ExecutionEvidence(item) for item in roots], 500.0, True)
        self.assertEqual({stage.execution_id for stage in stages}, {"lead-one", "lead-two"})
        self.assertEqual(len(stages), 2)

    def test_detached_registry_worker_remains_visible_without_owner_stage(self):
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        child = record("detached", parent_id="missing-owner", root_id="missing-owner", linkage="registered",
                       state="finished", outcome="unknown")
        (stage,) = g._co_execution_stages([ExecutionEvidence(child)], 500.0, True)
        self.assertEqual(stage.execution_id, "missing-owner")
        self.assertEqual([worker.execution_id for worker in stage.snap.workers], ["detached"])
        self.assertEqual(stage.snap.workers[0].parent_id, "missing-owner")

    def test_stage_carries_native_id_outcome_and_purpose_source_as_fields(self):
        bare = Stage(path="p", label="l")
        self.assertEqual((bare.native_id, bare.outcome, bare.purpose_source), ("", "", ""))
        self.assertTrue({"native_id", "outcome", "purpose_source"} <= {f.name for f in fields(Stage)})
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        root = record("lead-x", native_id="native-x", role="lead", state="finished", outcome="success",
                      purpose_source="goal-file")
        (stage,) = g._co_execution_stages([ExecutionEvidence(root)], 500.0, True)
        self.assertEqual((stage.native_id, stage.outcome, stage.purpose_source), ("native-x", "success", "goal-file"))

    def test_registry_records_outside_roots_are_dropped_but_cwdless_records_kept(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            inside = record("in", role="lead", cwd=str(Path(root) / "proj"))
            elsewhere = record("out", role="lead", cwd=str(Path(outside) / "proj"))
            cwdless = record("nocwd", role="lead", cwd="")
            g = collect.GlobalCollector([root], registry_root=str(Path(root) / "registry"), quota=False)
            self.addCleanup(g.close)
            g._co_proc_table = dict
            snapshot = RegistrySnapshot(records=[inside, elsewhere, cwdless])
            with mock.patch.object(collect, "read_registry", return_value=snapshot), \
                    mock.patch.object(collect, "read_native_executions", return_value=[]), \
                    mock.patch.dict(collect._co_native_cache, clear=True):
                items = g._co_executions(500.0)
            self.assertEqual({item.record.execution_id for item in items}, {"in", "nocwd"})

    def test_registry_only_unlinked_worker_is_visible_without_a_pid(self):
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        item = record("hosted", role="worker", parent_id="", root_id="", linkage="unknown",
                      state="finished", outcome="unknown")
        (stage,) = g._co_execution_stages([ExecutionEvidence(item, brief="assigned")], 500.0, True)
        self.assertEqual([worker.execution_id for worker in stage.snap.workers], ["hosted"])
        self.assertEqual(stage.lead_state, "gone")

    def test_unlinked_stage_worker_is_not_adopted_by_start_time(self):
        with tempfile.TemporaryDirectory() as root:
            stage_dir = Path(root) / "project" / ".stage"
            stage_dir.mkdir(parents=True)
            (stage_dir / "station").write_text("", encoding="utf-8")
            (stage_dir / "PROGRESS.md").write_text("", encoding="utf-8")
            g = collect.GlobalCollector([root], registry_root=str(Path(root) / "registry"), quota=False)
            self.addCleanup(g.close)
            stage = str(stage_dir)
            g._co_collectors[stage] = collect.Collector(stage, quota=False)
            worker = Worker("unlinked", DONE, start=300.0)
            g._co_refresh = lambda: setattr(g, "_co_found", [(stage, "project")])
            g._co_lead_map = lambda: {stage: collect._co_Lead(1, "claude", "live", str(stage_dir.parent), 200.0)}
            g._co_proc_table = dict
            g._co_idle = lambda *_args: False
            with mock.patch.object(collect.Collector, "snapshot", return_value=Snapshot(
                title="project", now=500.0, workers=[worker])), mock.patch.object(
                    collect, "_co_lead_session", return_value={"session_id": "lead", "session_name": "",
                                                               "tag": "", "lead_model": "", "effort": ""}):
                snapshot = g.snapshot(now=500.0, all_history=True)
            self.assertEqual(len(snapshot.stages), 2)
            lead_row = next(row for row in snapshot.stages if row.path == stage)
            unlinked_row = next(row for row in snapshot.stages if row.unlinked)
            self.assertEqual(lead_row.snap.workers, [])
            self.assertEqual([item.name for item in unlinked_row.snap.workers], ["unlinked"])

    def test_idle_hidden_stage_still_learns_lead_session_from_dot_session(self):
        with tempfile.TemporaryDirectory() as root:
            stage_dir = Path(root) / "project" / ".stage"
            stage_dir.mkdir(parents=True)
            (stage_dir / "station").write_text("", encoding="utf-8")
            (stage_dir / "PROGRESS.md").write_text("", encoding="utf-8")
            (stage_dir / "worker.session").write_text(
                '{"lead_pid": 4242, "lead_session": "lead-sid", "launched_at": 300, "lead_pid_start": 700}',
                encoding="utf-8")
            g = collect.GlobalCollector([root], registry_root=str(Path(root) / "registry"), quota=False)
            self.addCleanup(g.close)
            stage = str(stage_dir)
            g._co_collectors[stage] = collect.Collector(stage, quota=False)
            g._co_refresh = lambda: setattr(g, "_co_found", [(stage, "project")])
            g._co_lead_map = dict
            g._co_proc_table = lambda: {4242: (1, 1, 1, 700, 0)}
            g._co_idle = lambda *_args: True
            with mock.patch.object(collect.Collector, "snapshot", return_value=Snapshot(
                title="project", now=500.0, workers=[])):
                g.snapshot(now=500.0)
            self.assertEqual(g._co_child_session(4242), "lead-sid")


class MergeAbsentFieldTests(unittest.TestCase):
    def test_zero_timestamps_are_absent_so_native_values_fill_them(self):
        reg = record("x", state="finished", outcome="success")
        reg.started = 0.0
        reg.activity = 0.0
        reg.ended = None
        native_rec = record("x", state="finished", outcome="success", started=1700000000.0, activity=1700000000.0)
        merged = collect.reconcile_executions(RegistrySnapshot(records=[reg]),
                                              [ExecutionEvidence(native_rec)], {}, 500.0)[0]
        self.assertEqual((merged[0].record.started, merged[0].record.activity), (1700000000.0, 1700000000.0))

    def test_int_zero_timestamps_are_absent_so_native_values_fill_them(self):
        reg = record("x", state="finished", outcome="success")
        reg.started = 0
        reg.activity = 0
        reg.ended = 0
        native_rec = record("x", state="finished", outcome="success", started=1700000000.0, activity=1700000001.0)
        native_rec.ended = 1700000002.0
        merged = collect.reconcile_executions(RegistrySnapshot(records=[reg]),
                                              [ExecutionEvidence(native_rec)], {}, 500.0)[0]
        self.assertEqual((merged[0].record.started, merged[0].record.activity, merged[0].record.ended),
                         (1700000000.0, 1700000001.0, 1700000002.0))

    def test_zero_exit_code_stays_present_and_is_not_overwritten(self):
        reg = record("x", state="finished", outcome="unknown")
        reg.exit_code = 0
        native_rec = record("x", state="finished", outcome="unknown")
        native_rec.exit_code = 7
        merged = collect.reconcile_executions(RegistrySnapshot(records=[reg]),
                                              [ExecutionEvidence(native_rec)], {}, 500.0)[0]
        self.assertEqual(merged[0].record.exit_code, 0)

    def test_output_available_false_is_filled_from_native_true(self):
        reg = record("x", state="finished", outcome="success")
        reg.output_available = False
        native_rec = record("x", state="finished", outcome="success")
        native_rec.output_available = True
        merged = collect.reconcile_executions(RegistrySnapshot(records=[reg]),
                                              [ExecutionEvidence(native_rec)], {}, 500.0)[0]
        self.assertTrue(merged[0].record.output_available)

    def test_nonzero_registry_timestamp_is_authoritative_over_native(self):
        reg = record("x", state="finished", outcome="success", activity=200.0)
        native_rec = record("x", state="finished", outcome="success", activity=1700000000.0)
        merged = collect.reconcile_executions(RegistrySnapshot(records=[reg]),
                                              [ExecutionEvidence(native_rec)], {}, 500.0)[0]
        self.assertEqual(merged[0].record.activity, 200.0)


class ExecutionStageBootIdTests(unittest.TestCase):
    def test_boot_id_is_read_once_per_stages_pass(self):
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        procs = {9: (1, 0, 0, 5, 0), 10: (9, 0, 0, 5, 0), 11: (9, 0, 0, 5, 0), 12: (9, 0, 0, 5, 0)}
        g._co_proc_table = lambda: procs
        items = [ExecutionEvidence(record(f"w{n}", role="worker", parent_id="lead", root_id="lead",
                                          linkage="registered", pid=n, pid_start=5, state="running"))
                 for n in (10, 11, 12)]
        items.append(ExecutionEvidence(record("lead", role="lead", pid=9, pid_start=5, state="running")))
        with mock.patch.object(collect, "_co_read_text", return_value="boot\n") as read:
            g._co_execution_stages(items, 500.0, True)
        self.assertEqual(read.call_count, 1)


class RelativeCwdScopeTests(unittest.TestCase):
    def test_registry_record_with_relative_cwd_is_kept_under_roots(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            relative = record("rel", role="lead", cwd="projects/app")
            elsewhere = record("out", role="lead", cwd=str(Path(outside) / "proj"))
            g = collect.GlobalCollector([root], registry_root=str(Path(root) / "registry"), quota=False)
            self.addCleanup(g.close)
            g._co_proc_table = dict
            snapshot = RegistrySnapshot(records=[relative, elsewhere])
            with mock.patch.object(collect, "read_registry", return_value=snapshot), \
                    mock.patch.object(collect, "read_native_executions", return_value=[]), \
                    mock.patch.dict(collect._co_native_cache, clear=True):
                items = g._co_executions(500.0)
            self.assertEqual({item.record.execution_id for item in items}, {"rel"})


class ExecutionAgeOutTests(unittest.TestCase):
    NOW = 10_000_000.0
    DAY = 86400

    def stages(self, items, all_history=False, procs=None):
        g = collect.GlobalCollector([], quota=False)
        self.addCleanup(g.close)
        g._co_proc_table = lambda: procs or {}
        with mock.patch.object(collect, "_co_read_text", return_value="boot\n"):
            return g._co_execution_stages(items, self.NOW, all_history)

    def test_closed_execution_three_days_old_is_dropped_by_the_ops_window(self):
        """Discovery keeps it (a loose 14-day cost bound); the 24 h decision runs after work state is known."""
        old = record("old", role="lead", activity=self.NOW - 3 * self.DAY, started=self.NOW - 3 * self.DAY)
        stages = self.stages([ExecutionEvidence(old)])
        self.assertEqual([stage.execution_id for stage in stages], ["old"])
        collect._co_ops_windows(stages, self.NOW)
        self.assertEqual(stages, [])

    def test_closed_execution_two_hours_old_is_kept(self):
        recent = record("recent", role="lead", activity=self.NOW - 2 * 3600, started=self.NOW - 2 * 3600)
        result = self.stages([ExecutionEvidence(recent)])
        self.assertEqual([stage.execution_id for stage in result], ["recent"])
        self.assertEqual(result[0].state, "idle")

    def test_closed_execution_with_old_activity_but_recent_end_is_kept(self):
        ended = record("ended", role="lead", activity=self.NOW - 3 * self.DAY, started=self.NOW - 3 * self.DAY)
        ended.ended = self.NOW - 3600
        result = self.stages([ExecutionEvidence(ended)])
        self.assertEqual([stage.execution_id for stage in result], ["ended"])
        self.assertEqual(result[0].state, "idle")

    def test_closed_execution_with_old_activity_and_old_end_is_dropped_by_the_ops_window(self):
        old = record("old", role="lead", activity=self.NOW - 3 * self.DAY, started=self.NOW - 3 * self.DAY)
        old.ended = self.NOW - 2 * self.DAY
        stages = self.stages([ExecutionEvidence(old)])
        self.assertEqual([stage.execution_id for stage in stages], ["old"])
        collect._co_ops_windows(stages, self.NOW)
        self.assertEqual(stages, [])

    def test_all_history_keeps_three_day_old_closed_execution(self):
        old = record("old", role="lead", activity=self.NOW - 3 * self.DAY, started=self.NOW - 3 * self.DAY)
        result = self.stages([ExecutionEvidence(old)], all_history=True)
        collect._co_ops_windows(result, self.NOW, True)
        self.assertEqual([stage.execution_id for stage in result], ["old"])

    def test_live_execution_with_exact_pid_identity_is_kept_when_old(self):
        live = record("live", role="lead", pid=9, pid_start=5,
                      activity=self.NOW - 3 * self.DAY, started=self.NOW - 3 * self.DAY)
        result = self.stages([ExecutionEvidence(live)], procs={9: (1, 0, 0, 5, 0)})
        self.assertEqual([stage.execution_id for stage in result], ["live"])
        self.assertEqual(result[0].state, "idle")


if __name__ == "__main__":
    unittest.main()
