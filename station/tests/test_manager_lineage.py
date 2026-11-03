from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import collect
from model import RUNNING, Snapshot, Stage, Worker

NOW = 1_800_000_000.0


def write_session(stage: str, name: str, *, lead: str, parent: str = "", root: str = "", depth: int = 0,
                  launched_at: float = NOW) -> None:
    os.makedirs(stage, exist_ok=True)
    with open(os.path.join(stage, name + ".session"), "w", encoding="utf-8") as stream:
        json.dump({"schema": 2, "lead_session": lead, "parent_session": parent,
                   "root_session": root, "depth": depth, "launched_at": launched_at}, stream)


class ManagerLineage(unittest.TestCase):
    def _pid_manager_fixture(self, *, managers: int = 1, workers: int = 1,
                             ancestor_pids: list[int] | None = None, launched_at: float = NOW,
                             manager_start: float = NOW - 30) -> tuple[Stage, list[Stage]]:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        parent_workers = [Worker(name=f"mgr{i}", state=RUNNING, pid=100 + i,
                                 model=f"haiku-{i}", harness="claude", account="personal", start=manager_start)
                          for i in range(1, managers + 1)]
        top = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                    snap=Snapshot(workers=parent_workers,
                                  groups=collect._co_groups(parent_workers, [])))
        top.live = len(parent_workers)
        sources = []
        for i in range(1, managers + 1):
            source_dir = os.path.join(root, f"mgr{i}", ".stage")
            os.makedirs(source_dir, exist_ok=True)
            source_workers = []
            for j in range(1, workers + 1):
                name = f"w{j}"
                pids = ancestor_pids if ancestor_pids is not None else [100 + i]
                write_session(source_dir, name, lead=f"M{i}", parent="top", root="top", depth=2,
                              launched_at=launched_at)
                with open(os.path.join(source_dir, name + ".session"), encoding="utf-8") as stream:
                    data = json.load(stream)
                data["ancestors"] = pids
                with open(os.path.join(source_dir, name + ".session"), "w", encoding="utf-8") as stream:
                    json.dump(data, stream)
                source_workers.append(Worker(name, RUNNING, start=NOW + j, home=source_dir,
                                             session=f"M{i}"))
            sources.append(Stage(path=f"/top/mgr{i}", dir=source_dir, label=f"mgr{i}",
                                 snap=Snapshot(workers=source_workers,
                                               groups=collect._co_groups(source_workers, []))))
        return top, sources

    def _multi_stage_pid_manager_fixture(self, *, managers: int = 1, workers: int = 1
                                         ) -> tuple[Stage, Stage, list[Stage]]:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        root_stage = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                           snap=Snapshot())
        manager_rows = [Worker(name=f"mgr{i}", state=RUNNING, pid=100 + i, model=f"haiku-{i}",
                               harness="claude", account="personal", start=NOW - 30)
                        for i in range(1, managers + 1)]
        launch_stage = Stage(path="/top/launch", dir="/top/.stage/launch", label="top launch",
                             session_id="top", lead_state="live",
                             snap=Snapshot(workers=manager_rows,
                                           groups=collect._co_groups(manager_rows, [])))
        launch_stage.live = len(manager_rows)
        sources = []
        for i in range(1, managers + 1):
            source_dir = os.path.join(root, f"mgr{i}", ".stage")
            source_workers = []
            for j in range(1, workers + 1):
                name = f"w{j}"
                write_session(source_dir, name, lead=f"M{i}", parent="top", root="top", depth=2,
                              launched_at=NOW)
                with open(os.path.join(source_dir, name + ".session"), encoding="utf-8") as stream:
                    data = json.load(stream)
                data["ancestors"] = [100 + i]
                with open(os.path.join(source_dir, name + ".session"), "w", encoding="utf-8") as stream:
                    json.dump(data, stream)
                source_workers.append(Worker(name, RUNNING, start=NOW + j, home=source_dir,
                                             session=f"M{i}"))
            sources.append(Stage(path=f"/top/mgr{i}", dir=source_dir, label=f"mgr{i}",
                                 snap=Snapshot(workers=source_workers,
                                               groups=collect._co_groups(source_workers, []))))
        return root_stage, launch_stage, sources

    def test_pid_file_manager_rows_fold_into_promoted_stages(self) -> None:
        top, sources = self._pid_manager_fixture(managers=4, workers=4)
        out = collect._co_apply_manager_lineage([top, *sources])
        promoted = [stage for stage in out if stage.session_id and stage.session_id.startswith("M")]
        self.assertEqual([worker.name for worker in top.snap.workers], [])
        self.assertEqual(top.snap.groups, [])
        self.assertEqual(top.live, 0)
        self.assertEqual(len(promoted), 4)
        self.assertEqual([len(stage.snap.workers) for stage in promoted], [4] * 4)
        self.assertEqual([stage.lead_pid for stage in promoted], [101, 102, 103, 104])
        self.assertEqual([stage.lead_model for stage in promoted], ["haiku-1", "haiku-2", "haiku-3", "haiku-4"])
        self.assertTrue(all(stage.lead_state == "live" for stage in promoted))

    def test_pid_file_manager_row_folds_from_second_stage_with_same_parent_session(self) -> None:
        root_stage, launch_stage, sources = self._multi_stage_pid_manager_fixture()
        out = collect._co_apply_manager_lineage([root_stage, launch_stage, *sources])
        promoted = next(stage for stage in out if stage.session_id == "M1")
        self.assertEqual(root_stage.session_id, launch_stage.session_id)
        self.assertEqual(launch_stage.snap.workers, [])
        self.assertEqual(launch_stage.live, 0)
        self.assertEqual(launch_stage.snap.groups, [])
        self.assertEqual((promoted.lead_pid, promoted.lead_model, promoted.lead_since), (101, "haiku-1", NOW - 30))
        self.assertEqual([worker.name for worker in promoted.snap.workers], ["w1"])

    def test_four_pid_managers_fold_from_same_session_launch_stage(self) -> None:
        root_stage, launch_stage, sources = self._multi_stage_pid_manager_fixture(managers=4, workers=4)
        out = collect._co_apply_manager_lineage([root_stage, launch_stage, *sources])
        promoted = [stage for stage in out if stage.session_id and stage.session_id.startswith("M")]
        self.assertEqual(launch_stage.snap.workers, [])
        self.assertEqual(launch_stage.live, 0)
        self.assertEqual(launch_stage.snap.groups, [])
        self.assertEqual(len(promoted), 4)
        self.assertEqual([len(stage.snap.workers) for stage in promoted], [4] * 4)
        self.assertEqual([stage.lead_pid for stage in promoted], [101, 102, 103, 104])
        self.assertEqual([stage.lead_model for stage in promoted], ["haiku-1", "haiku-2", "haiku-3", "haiku-4"])

    def test_pid_reuse_after_child_launch_stays_under_parent(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=1000.0, manager_start=2000.0)
        top.snap.workers[0].pid = 101
        manager_row = top.snap.workers[0]
        with mock.patch.object(collect, "_co_worker_lineage",
                               return_value=("M1", "top", "top", 2, (101,), 1000.0)):
            out = collect._co_apply_manager_lineage([top, *sources])
        self.assertIn(manager_row, top.snap.workers)
        self.assertEqual(top.live, 1)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_old_finished_pid_row_is_not_folded_after_pid_reuse(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=5000.0, manager_start=1000.0)
        manager_row = top.snap.workers[0]
        manager_row.state = "done"
        manager_row.end = 1100.0
        top.live = 0
        with mock.patch.object(collect, "_co_worker_lineage",
                               return_value=("M1", "top", "top", 2, (101,), 5000.0)):
            out = collect._co_apply_manager_lineage([top, *sources])
        self.assertIn(manager_row, top.snap.workers)
        self.assertEqual(top.live, 0)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_finished_pid_manager_row_folds_when_end_follows_child_launch(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=5000.0, manager_start=4900.0)
        manager_row = top.snap.workers[0]
        manager_row.state = "done"
        manager_row.end = 5100.0
        top.live = 0
        with mock.patch.object(collect, "_co_worker_lineage",
                               return_value=("M1", "top", "top", 2, (101,), 5000.0)):
            out = collect._co_apply_manager_lineage([top, *sources])
        self.assertNotIn(manager_row, top.snap.workers)
        self.assertEqual(top.live, 0)
        self.assertTrue(any(stage.session_id == "M1" for stage in out))

    def test_finished_pid_row_with_unknown_end_is_not_folded(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=5000.0, manager_start=4900.0)
        manager_row = top.snap.workers[0]
        manager_row.state = "done"
        manager_row.end = None
        top.live = 0
        with mock.patch.object(collect, "_co_worker_lineage",
                               return_value=("M1", "top", "top", 2, (101,), 5000.0)):
            out = collect._co_apply_manager_lineage([top, *sources])
        self.assertIn(manager_row, top.snap.workers)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_live_pid_row_keeps_start_only_fold_rule(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=5000.0, manager_start=1000.0)
        manager_row = top.snap.workers[0]
        with mock.patch.object(collect, "_co_worker_lineage",
                               return_value=("M1", "top", "top", 2, (101,), 5000.0)):
            out = collect._co_apply_manager_lineage([top, *sources])
        self.assertNotIn(manager_row, top.snap.workers)
        self.assertTrue(any(stage.session_id == "M1" for stage in out))

    def test_pid_started_before_child_launch_is_folded(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=NOW, manager_start=NOW - 30)
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual(top.snap.workers, [])
        self.assertTrue(any(stage.session_id == "M1" for stage in out))

    def test_unknown_manager_start_does_not_fold_pid_row(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=NOW, manager_start=0.0)
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual(len(top.snap.workers), 1)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_unknown_child_launch_time_does_not_fold_pid_row(self) -> None:
        top, sources = self._pid_manager_fixture(launched_at=0.0, manager_start=NOW - 30)
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual(len(top.snap.workers), 1)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_live_pid_row_does_not_replace_existing_live_lead_identity(self) -> None:
        top, sources = self._pid_manager_fixture()
        existing = Stage(path="/top/mgr1", dir=sources[0].dir, label="known manager", session_id="M1",
                         lead_state="live", lead_pid=777, lead_since=NOW - 100, lead_kind="claude",
                         lead_model="claude-haiku-5-5", tag="CC·P",
                         snap=Snapshot())
        out = collect._co_apply_manager_lineage([top, existing, *sources])
        manager = next(stage for stage in out if stage.session_id == "M1")
        self.assertEqual((manager.lead_state, manager.lead_pid, manager.lead_model, manager.tag),
                         ("live", 777, "claude-haiku-5-5", "CC·P"))
        self.assertEqual([worker.name for worker in top.snap.workers], [])

    def test_pid_row_fills_gaps_on_existing_manager_stage(self) -> None:
        top, sources = self._pid_manager_fixture()
        existing = Stage(path="/top/mgr1", dir=sources[0].dir, label="", session_id="M1",
                         lead_state="gone", snap=Snapshot())
        out = collect._co_apply_manager_lineage([top, existing, *sources])
        manager = next(stage for stage in out if stage.session_id == "M1")
        self.assertEqual((manager.lead_state, manager.lead_pid, manager.lead_model, manager.lead_since,
                          manager.lead_kind, manager.tag),
                         ("live", 101, "haiku-1", NOW - 30, "claude", "CC·P"))

    def test_unmatched_pid_row_stays_under_parent(self) -> None:
        top, sources = self._pid_manager_fixture(ancestor_pids=[999])
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual(len(top.snap.workers), 1)
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_ambiguous_ancestor_pid_stays_under_parent(self) -> None:
        top, sources = self._pid_manager_fixture(managers=2, ancestor_pids=[101, 102])
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual([worker.name for worker in top.snap.workers], ["mgr1", "mgr2"])
        self.assertFalse(any(stage.session_id and stage.session_id.startswith("M") for stage in out))

    def test_reported_real_worker_is_not_folded_as_pid_manager(self) -> None:
        top, sources = self._pid_manager_fixture()
        report = os.path.join(sources[0].dir, "mgr1", "REPORT.md")
        os.makedirs(os.path.dirname(report), exist_ok=True)
        with open(report, "w", encoding="utf-8") as stream:
            stream.write("## Summary\nReal worker output\n")
        top.snap.workers[0].has_report = True
        out = collect._co_apply_manager_lineage([top, *sources])
        self.assertEqual([worker.name for worker in top.snap.workers], ["mgr1"])
        self.assertFalse(any(stage.session_id == "M1" for stage in out))

    def test_manager_without_status_env_is_spawn_depth_one(self) -> None:
        lead = collect._co_Lead(20, "cursor", "live", "/project", NOW - 5)
        procs = {20: (10, 0, 0, 200, 0), 10: (1, 0, 0, 100, 0)}
        envs = {20: {"CLAUDE_CODE_SESSION_ID": "manager"},
                10: {"CLAUDE_CODE_SESSION_ID": "top", "STATUS_ROOT_SESSION": "top", "STATUS_DEPTH": "0"}}
        with mock.patch.object(collect, "_pr_scan", return_value=procs), \
                mock.patch.object(collect, "_co_environ", side_effect=lambda pid: envs.get(pid, {})):
            meta = collect._co_lead_session("/project/.stage", lead, "manager")
        self.assertEqual((meta["parent"], meta["root_session"], meta["launch_depth"]), ("top", "top", 1))

    def test_explicit_status_env_wins_over_ancestors(self) -> None:
        lead = collect._co_Lead(20, "cursor", "live", "/project", NOW - 5)
        procs = {20: (10, 0, 0, 200, 0), 10: (1, 0, 0, 100, 0)}
        envs = {20: {"CLAUDE_CODE_SESSION_ID": "manager", "STATUS_PARENT_SESSION": "explicit",
                     "STATUS_ROOT_SESSION": "root-explicit", "STATUS_DEPTH": "3"},
                10: {"CLAUDE_CODE_SESSION_ID": "ancestor"}}
        with mock.patch.object(collect, "_pr_scan", return_value=procs), \
                mock.patch.object(collect, "_co_environ", side_effect=lambda pid: envs.get(pid, {})):
            meta = collect._co_lead_session("/project/.stage", lead, "manager")
        self.assertEqual((meta["parent"], meta["root_session"], meta["launch_depth"]),
                         ("explicit", "root-explicit", 3))

    def test_recorded_manager_lineage_survives_detachment(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            worker_dir = os.path.join(root, "cabinet", ".stage")
            write_session(worker_dir, "worker", lead="manager", parent="top", root="top", depth=1)
            parent = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                           snap=Snapshot(workers=[Worker(name="manager", state=RUNNING, native_id="manager",
                                                         harness="claude", pid=20)]))
            source = Stage(path="/source", dir=worker_dir, label="source",
                           snap=Snapshot(workers=[Worker(name="worker", state=RUNNING, session="manager", home=worker_dir)]))
            out = collect._co_apply_manager_lineage([parent, source])
        row = next(stage for stage in out if stage.session_id == "manager")
        self.assertEqual((row.parent, row.root_session, row.launch_depth), ("top", "top", 1))
        self.assertEqual([worker.name for worker in row.snap.workers], ["worker"])

    def test_self_parent_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source_dir = os.path.join(root, "source", ".stage")
            write_session(source_dir, "worker", lead="manager", parent="manager", root="top", depth=1)
            native = Worker(name="manager", state=RUNNING, native_id="manager", harness="claude", pid=20)
            top = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                        snap=Snapshot(workers=[native]))
            source_worker = Worker(name="worker", state=RUNNING, session="manager", home=source_dir)
            source = Stage(path="/source", dir=source_dir, label="source",
                           snap=Snapshot(workers=[source_worker]))
            out = collect._co_apply_manager_lineage([top, source])
        self.assertFalse(any(stage.session_id == "manager" for stage in out))
        self.assertIn(native, top.snap.workers)
        self.assertIn(source_worker, source.snap.workers)
        self.assertEqual((top.parent, top.launch_depth), ("", 0))

    def test_unknown_parent_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source_dir = os.path.join(root, "source", ".stage")
            write_session(source_dir, "worker", lead="manager", parent="ghost", root="top", depth=1)
            native = Worker(name="manager", state=RUNNING, native_id="manager", harness="claude", pid=20)
            top = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                        snap=Snapshot(workers=[native]))
            source_worker = Worker(name="worker", state=RUNNING, session="manager", home=source_dir)
            source = Stage(path="/source", dir=source_dir, label="source",
                           snap=Snapshot(workers=[source_worker]))
            out = collect._co_apply_manager_lineage([top, source])
        self.assertFalse(any(stage.session_id == "manager" for stage in out))
        self.assertIn(native, top.snap.workers)
        self.assertIn(source_worker, source.snap.workers)
        self.assertEqual((top.parent, top.launch_depth), ("", 0))

    def test_process_table_is_reused_for_many_leads(self) -> None:
        procs = {pid: (1, 0, 0, pid * 10, 0) for pid in range(20, 25)}
        envs = {pid: {"CLAUDE_CODE_SESSION_ID": f"session-{pid}"} for pid in procs}
        collector = collect.GlobalCollector([], quota=False)
        try:
            with mock.patch.object(collect, "_co_scan_procs", return_value=procs) as scan, \
                    mock.patch.object(collect, "_pr_scan", side_effect=AssertionError("per-lead /proc scan")) as raw_scan, \
                    mock.patch.object(collect, "_co_environ", side_effect=lambda pid: envs.get(pid, {})):
                for pid in procs:
                    lead = collect._co_Lead(pid, "claude", "live", "/project", NOW - 5)
                    collect._co_lead_session("/project/.stage", lead, procs=collector._co_proc_table())
            scan.assert_called_once_with()
            raw_scan.assert_not_called()
        finally:
            collector.close()

    def test_lead_without_session_does_not_infer_ancestor_parent(self) -> None:
        lead = collect._co_Lead(20, "claude", "live", "/project", NOW - 5)
        procs = {20: (10, 0, 0, 200, 0), 10: (1, 0, 0, 100, 0)}
        envs = {20: {}, 10: {"CLAUDE_CODE_SESSION_ID": "shell-session"}}
        with mock.patch.object(collect, "_pr_scan", return_value=procs), \
                mock.patch.object(collect, "_co_environ", side_effect=lambda pid: envs.get(pid, {})):
            meta = collect._co_lead_session("/project/.stage", lead)
        self.assertEqual((meta["parent"], meta["root_session"], meta["launch_depth"]), ("", "", 0))

    def test_missing_ancestry_stays_unknown(self) -> None:
        lead = collect._co_Lead(20, "cursor", "live", "/project", NOW - 5)
        with mock.patch.object(collect, "_pr_scan", return_value={20: (999, 0, 0, 200, 0)}), \
                mock.patch.object(collect, "_co_environ", return_value={"CLAUDE_CODE_SESSION_ID": "manager"}):
            meta = collect._co_lead_session("/project/.stage", lead, "manager")
        self.assertEqual((meta["parent"], meta["root_session"], meta["launch_depth"]), ("", "", 0))

    def test_manager_is_not_also_a_worker(self) -> None:
        parent = Stage(path="/top", dir="/top/.stage", label="top", session_id="top", lead_state="live",
                       snap=Snapshot(workers=[Worker(name="manager", state=RUNNING, native_id="manager")] ))
        manager = Stage(path="/manager", dir="/manager/.stage", label="manager", session_id="manager",
                        lead_state="live", lead_pid=20)
        out = collect._co_apply_manager_lineage([parent, manager])
        self.assertEqual([worker.native_id for stage in out for worker in stage.snap.workers], [])


if __name__ == "__main__":
    unittest.main()
