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


def write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream)


class Issue26Matrix(unittest.TestCase):
    def make_matrix(self, shared_lead: bool) -> list[Stage]:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        top = Stage(path="/project/.stage", dir="/project/.stage", label="top", session_id="T",
                    lead_state="live", lead_pid=10,
                    snap=Snapshot(workers=[Worker(name=f"manager-{i}", state=RUNNING, native_id=f"M{i}")
                                           for i in range(1, 5)]))
        stages = [top]
        for i in range(1, 5):
            stage_dir = os.path.join(root, f"cabinet-{i}", ".stage")
            os.makedirs(stage_dir, exist_ok=True)
            with open(os.path.join(stage_dir, "leader.pid"), "w", encoding="utf-8") as stream:
                stream.write(str(20 + i))
            workers = []
            for j in range(1, 5):
                name = f"w{j}"
                owner = "T" if shared_lead else f"M{i}"
                write(os.path.join(stage_dir, name + ".session"), {
                    "schema": 2, "lead_session": owner,
                    "parent_session": "" if shared_lead else "T",
                    "root_session": "" if shared_lead else "T", "depth": 0 if shared_lead else 1,
                    "lead_harness": "claude", "lead_pid": 20 + i,
                })
                with open(os.path.join(stage_dir, name + ".pid"), "w", encoding="utf-8") as stream:
                    stream.write(str(4100000 + i * 10 + j))
                with open(os.path.join(stage_dir, name + ".model"), "w", encoding="utf-8") as stream:
                    stream.write("opencode/deepseek")
            collector = collect.Collector(stage_dir, quota=False)
            self.addCleanup(collector.close)

            def make_worker(origin: str, name: str, *_args: object) -> Worker:
                return Worker(name=name, state=RUNNING, start=NOW + int(name[1:]),
                              home=origin, harness="opencode")

            with mock.patch.object(collector, "_co_one_worker", side_effect=make_worker):
                workers, _ = collector._co_workers(stage_dir, NOW, {}, {})
            for worker in workers:
                worker.session = owner
            row = Stage(path=f"/project/.stage/cabinet-{i}", dir=stage_dir, label=f"cabinet-{i}",
                        session_id=f"M{i}" if not shared_lead else f"group-{i}",
                        lead_state="live" if not shared_lead else "gone", lead_pid=20 + i if not shared_lead else None,
                        lead_kind="claude" if not shared_lead else "", tag="CC·P" if not shared_lead else "",
                        snap=Snapshot(workers=workers, groups=collect._co_groups(workers, [])))
            stages.append(row)
        return stages

    def test_issue6_fixture_matrix(self) -> None:
        stages = collect._co_apply_manager_lineage(self.make_matrix(shared_lead=False))
        collect._co_level_tags(stages)
        top = next(stage for stage in stages if stage.session_id == "T")
        managers = [stage for stage in stages if stage.session_id in {f"M{i}" for i in range(1, 5)}]
        self.assertEqual(top.level_tag, "LEAD")
        self.assertEqual([(stage.parent, stage.root_session, stage.launch_depth, stage.level_tag) for stage in managers],
                         [("T", "T", 1, "SPAWN")] * 4)
        groups = [group for stage in managers for group in stage.snap.groups]
        worker_names = [worker.name for stage in managers for worker in stage.snap.workers]
        self.assertEqual(len(groups), 4)
        self.assertEqual(sorted(len(group.workers) for group in groups), [4, 4, 4, 4])
        self.assertEqual(len(worker_names), 16)
        self.assertEqual(len({(stage.dir, worker.name) for stage in managers for worker in stage.snap.workers}), 16)
        self.assertFalse(any(worker.name.startswith("manager-") for stage in stages for worker in stage.snap.workers))
        self.assertFalse(any(worker.name == "leader" for stage in stages for worker in stage.snap.workers))
        self.assertTrue(all(stage.tag == "CC·P" for stage in managers))
        self.assertTrue(all(worker.harness == "opencode" for stage in managers for worker in stage.snap.workers))

    def test_issue6_shared_lead_layout_still_four_groups(self) -> None:
        stages = collect._co_apply_manager_lineage(self.make_matrix(shared_lead=True))
        groups = [group for stage in stages[1:] for group in stage.snap.groups]
        self.assertEqual(len(groups), 4)
        self.assertEqual(sorted(len(group.workers) for group in groups), [4, 4, 4, 4])
        top = next(stage for stage in stages if stage.session_id == "T")
        self.assertEqual(len(top.snap.workers), 4)
        self.assertTrue(all(len(stage.snap.workers) == 4 for stage in stages[1:]))
        self.assertEqual(sum(len(stage.snap.workers) for stage in stages), 20)
        self.assertEqual(len({(worker.home, worker.name) for stage in stages[1:] for worker in stage.snap.workers}), 16)
        self.assertEqual(len({(worker.home, worker.name) for stage in stages for worker in stage.snap.workers}), 20)

    def test_existing_manager_bucket_keeps_native_identity(self) -> None:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        worker_dir = os.path.join(root, "manager", ".stage")
        os.makedirs(worker_dir)
        write(os.path.join(worker_dir, "child.session"), {
            "schema": 2, "lead_session": "M", "parent_session": "T", "root_session": "T", "depth": 2,
        })
        child = Worker("child", RUNNING, home=worker_dir, session="M")
        manager_bucket = Stage(path="/project/.stage/manager", dir=worker_dir, label="manager", session_id="M",
                               lead_state="gone", snap=Snapshot(workers=[child]))
        native_manager = Worker("manager", RUNNING, native_id="M", pid=1234, harness="codex", model="gpt-6-luna",
                                account="personal", start=NOW - 15)
        top = Stage(path="/project/.stage", dir="/project/.stage", label="top", session_id="T",
                    snap=Snapshot(workers=[native_manager]))

        stages = collect._co_apply_manager_lineage([top, manager_bucket])

        self.assertEqual(top.snap.workers, [])
        self.assertEqual(manager_bucket.lead_pid, 1234)
        self.assertEqual((manager_bucket.lead_state, manager_bucket.lead_kind, manager_bucket.lead_model,
                          manager_bucket.harness, manager_bucket.tag, manager_bucket.native_id),
                         ("live", "codex", "gpt-6-luna", "codex", "Codex", "M"))
        self.assertEqual(manager_bucket.activity, NOW - 15)
        self.assertEqual([worker.name for worker in manager_bucket.snap.workers], ["child"])
        self.assertEqual(len(stages), 2)

    def test_existing_live_manager_bucket_keeps_identity_and_activity(self) -> None:
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        worker_dir = os.path.join(root, "manager", ".stage")
        os.makedirs(worker_dir)
        write(os.path.join(worker_dir, "child.session"), {
            "schema": 2, "lead_session": "M", "parent_session": "T", "root_session": "T", "depth": 2,
        })
        child = Worker("child", RUNNING, home=worker_dir, session="M")
        manager_bucket = Stage(path="/project/.stage/manager", dir=worker_dir, label="manager", session_id="M",
                               lead_state="live", lead_pid=20, lead_since=NOW - 60, lead_kind="claude",
                               tag="CC·P", snap=Snapshot(workers=[child]), activity=NOW - 10)
        native_manager = Worker("manager", "DONE", native_id="M", harness="codex", model="gpt-6-luna",
                                account="personal", start=NOW - 120)
        top = Stage(path="/project/.stage", dir="/project/.stage", label="top", session_id="T",
                    snap=Snapshot(workers=[native_manager]))

        stages = collect._co_apply_manager_lineage([top, manager_bucket])

        self.assertEqual(manager_bucket.lead_state, "live")
        self.assertEqual(manager_bucket.lead_pid, 20)
        self.assertEqual(manager_bucket.activity, NOW - 10)
        self.assertEqual(manager_bucket.lead_kind, "claude")
        self.assertEqual(manager_bucket.tag, "CC·P")
        self.assertEqual(top.snap.workers, [])
        self.assertEqual([worker.name for worker in manager_bucket.snap.workers], ["child"])
        self.assertEqual(len(stages), 2)


if __name__ == "__main__":
    unittest.main()
