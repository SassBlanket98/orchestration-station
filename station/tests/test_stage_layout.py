from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import collect
from model import DONE, RUNNING, Milestone, Worker

NOW = 1_800_000_000.0


class StageLayout(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stage = self.tmp.name

    def write(self, name: str, suffix: str, text: str = "") -> str:
        path = os.path.join(self.stage, name + suffix)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
        return path

    def discover(self) -> list[Worker]:
        collector = collect.Collector(self.stage, quota=False)
        self.addCleanup(collector.close)
        worker = Worker("accepted", RUNNING, home=self.stage)
        with mock.patch.object(collector, "_co_one_worker", return_value=worker):
            workers, _services = collector._co_workers(self.stage, NOW, {}, {})
        return workers

    def test_leader_pid_and_log_are_not_workers(self) -> None:
        self.write("leader", ".pid", "4194304")
        self.write("leader", ".log", "bookkeeping output")
        self.assertEqual(collect._co_stage_pid_names(self.stage), {})
        self.assertEqual(self.discover(), [])

    def test_each_supported_companion_accepts_worker(self) -> None:
        for suffix in (".model", ".session", ".run.json"):
            with self.subTest(suffix=suffix):
                self.write("w", ".pid", "4194304")
                self.write("w", suffix, "companion")
                self.assertEqual(collect._co_stage_pid_names(self.stage), {4194304: "w"})
                self.assertEqual([worker.name for worker in self.discover()], ["accepted"])
                os.unlink(os.path.join(self.stage, "w.pid"))
                os.unlink(os.path.join(self.stage, "w" + suffix))
        os.makedirs(os.path.join(self.stage, "briefs"), exist_ok=True)
        self.write("w", ".pid", "4194304")
        with open(os.path.join(self.stage, "briefs", "w.md"), "w", encoding="utf-8") as stream:
            stream.write("brief")
        self.assertEqual(collect._co_stage_pid_names(self.stage), {4194304: "w"})
        self.assertEqual([worker.name for worker in self.discover()], ["accepted"])

    def test_legacy_pid_log_requires_companion(self) -> None:
        self.write("legacy", ".pid", "4194304")
        self.write("legacy", ".log", "old worker log")
        self.assertEqual(collect._co_stage_pid_names(self.stage), {})
        self.assertEqual(self.discover(), [])

    def test_live_session_pid_is_worker_not_service(self) -> None:
        self.write("w", ".pid", "4194304")
        self.write("w", ".session", "session")
        worker = Worker("w", RUNNING, home=self.stage)
        collector = collect.Collector(self.stage, quota=False)
        self.addCleanup(collector.close)
        procs = {4194304: (1, 0, 0, 0, 0)}
        with mock.patch.object(collect, "_co_proc_is_agent", return_value=False), \
                mock.patch.object(collector, "_co_one_worker", return_value=worker):
            workers, services = collector._co_workers(self.stage, NOW, procs, {})
        self.assertEqual(workers, [worker])
        self.assertEqual(services, [])
        self.assertEqual(collect._co_stage_pid_names(self.stage), {4194304: "w"})

    def test_live_non_agent_pid_with_only_a_report_dir_is_not_a_service(self) -> None:
        # a report dir has always exempted a live pid from the service check; the worker gate does not count it
        os.makedirs(os.path.join(self.stage, "w"))
        procs = {os.getpid(): (1, 1, 1, 1, 1)}
        self.assertFalse(collect._co_is_service(self.stage, "w", os.getpid(), procs, {}))

    def test_suffixed_brief_validates_pid(self) -> None:
        os.makedirs(os.path.join(self.stage, "briefs"), exist_ok=True)
        self.write("build", ".pid", "4194304")
        with open(os.path.join(self.stage, "briefs", "build-review.md"), "w", encoding="utf-8") as stream:
            stream.write("brief")
        # a brief named <worker>-<suffix>.md belongs to <worker> (established convention, e.g. w2 / w2-alpha.md)
        self.assertEqual(collect._co_stage_pid_names(self.stage), {4194304: "build"})

    def test_shared_lead_four_stages_four_workers(self) -> None:
        workers: list[Worker] = []
        for cabinet in range(1, 5):
            stage = os.path.join(self.stage, f"boom-{cabinet}")
            os.makedirs(stage)
            with open(os.path.join(stage, "leader.pid"), "w", encoding="utf-8") as stream:
                stream.write("4194304")
            for index in range(1, 5):
                name = f"w{index}"
                with open(os.path.join(stage, name + ".pid"), "w", encoding="utf-8") as stream:
                    stream.write(str(4100000 + cabinet * 10 + index))
                with open(os.path.join(stage, name + ".model"), "w", encoding="utf-8") as stream:
                    stream.write("model")
            collector = collect.Collector(stage, quota=False)
            self.addCleanup(collector.close)

            def make_worker(origin: str, name: str, *_args: object) -> Worker:
                return Worker(name, DONE, start=NOW + int(name[1:]), home=origin, session="one-lead-session")

            with mock.patch.object(collector, "_co_one_worker", side_effect=make_worker):
                found, _services = collector._co_workers(stage, NOW, {}, {})
            workers.extend(found)

        milestones = [Milestone(NOW, "dispatch: launched w1 w2 w3 w4")]
        groups = collect._co_groups(workers, milestones)

        self.assertEqual(len(groups), 4)
        self.assertEqual(sorted(len(group.workers) for group in groups), [4, 4, 4, 4])
        self.assertEqual(len(workers), 16)
        keys = [key for group in groups for key in group.worker_keys]
        self.assertEqual(len(keys), 16)
        self.assertEqual(len(set(keys)), 16)
        self.assertEqual(sum(group.total for group in groups), 16)
        self.assertNotIn("leader", [name for group in groups for name in group.workers])
        self.assertEqual({group.stage for group in groups}, {f"boom-{i}" for i in range(1, 5)})

    def test_single_stage_keeps_dispatch_groups(self) -> None:
        workers = [
            Worker("build-a", DONE, start=NOW, home=self.stage),
            Worker("build-b", DONE, start=NOW + 1, home=self.stage),
            Worker("review-x", DONE, start=NOW + 120, home=self.stage),
        ]
        groups = collect._co_groups(workers, [
            Milestone(NOW, "dispatch: launched build-a build-b"),
            Milestone(NOW + 120, "dispatch: launched review-x"),
        ])
        self.assertEqual([(group.label, group.workers) for group in groups], [
            ("review-x", ["review-x"]),
            ("build", ["build-a", "build-b"]),
        ])

    def test_current_is_selected_across_all_origins(self) -> None:
        stage_a, stage_b = os.path.join(self.stage, "a"), os.path.join(self.stage, "b")
        live = Worker("live", RUNNING, start=NOW, home=stage_a)
        finished = Worker("finished", DONE, start=NOW + 100, end=NOW + 110, home=stage_b)
        groups = collect._co_groups([live, finished], [])
        self.assertEqual([group.workers for group in groups if group.current], [["live"]])
        self.assertTrue(next(group for group in groups if group.workers == ["finished"]).finished)
        self.assertEqual(next(group for group in groups if group.workers == ["finished"]).finished_at, NOW + 110)

        older = Worker("older", DONE, start=NOW, end=NOW + 10, home=stage_a)
        newest = Worker("newest", DONE, start=NOW + 100, end=NOW + 120, home=stage_b)
        groups = collect._co_groups([older, newest], [])
        self.assertEqual([group.workers for group in groups if group.current], [["newest"]])
        newest_group = next(group for group in groups if group.workers == ["newest"])
        self.assertTrue(newest_group.finished)
        self.assertEqual(newest_group.finished_at, NOW + 120)

    def test_empty_home_joins_only_unambiguous_origin(self) -> None:
        stage_a, stage_b = os.path.join(self.stage, "a"), os.path.join(self.stage, "b")
        base = Worker("base", DONE, start=NOW, home=stage_a)
        unlocated = Worker("unlocated", DONE, start=NOW + 10, home="")
        groups = collect._co_groups([base, unlocated], [])
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0].workers), {"base", "unlocated"})

        other = Worker("other", DONE, start=NOW + 20, home=stage_b)
        groups = collect._co_groups([base, other, unlocated], [])
        self.assertEqual(len(groups), 3)
        self.assertEqual(next(group for group in groups if "unlocated" in group.workers).workers, ["unlocated"])


if __name__ == "__main__":
    unittest.main()
