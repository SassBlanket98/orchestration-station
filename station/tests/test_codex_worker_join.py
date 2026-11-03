from __future__ import annotations

import json
import os
import tempfile
import unittest

import collect
from model import ExecutionEvidence, ExecutionRecord, Snapshot, Stage, Worker
from registry import execution_key


def _worker(name: str, *, start: float = 100.0, pid: int | None = 500,
            harness: str = "codex", state: str = "running") -> Worker:
    return Worker(name=name, state=state, start=start, pid=pid, harness=harness, family=harness)


def _native(native_id: str, *, start: float = 100.0, cwd: str = "/repo", pid: int | None = None,
            tokens_in: int | None = 17, cost: float | None = 0.25) -> Stage:
    record = ExecutionRecord(execution_key("codex", "personal", native_id), harness="codex",
                             account="personal", native_id=native_id, role="worker", cwd=cwd,
                             started=start, pid=pid, tokens_in=tokens_in, cost=cost)
    item = ExecutionEvidence(record)
    worker = collect._co_execution_worker(item, 200.0, False)
    return Stage(path=f"execution:{record.execution_id}", label="execution", snap=Snapshot(workers=[worker]),
                 execution_id=record.execution_id, parent_id=record.parent_id, root_id=record.root_id,
                 account="personal", lead_kind="codex", lead_state="gone", cwd=cwd, native_id=native_id)


def _manager(tmp: str, workers: list[Worker]) -> Stage:
    return Stage(path=tmp, label="project", snap=Snapshot(workers=workers), dir=tmp,
                 lead_state="live", session_id="manager-session", lead_kind="codex")


def _run_record(tmp: str, name: str, native_id: str, *, start: float = 100.0,
                cwd: str = "/repo") -> None:
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, f"{name}.run.json"), "w", encoding="utf-8") as stream:
        json.dump({"schema": 1, "name": name, "harness": "codex", "native_id": native_id,
                   "cwd": cwd, "started": start, "lead_session": "manager-session"}, stream)


def _unattached(cwd: str, *workers: Worker) -> Stage:
    return Stage(path=f"execution:unattached:{cwd}", label="unattached",
                 snap=Snapshot(workers=list(workers)), execution_id="unattached:bucket",
                 lead_kind="codex", lead_state="gone", cwd="")


class CodexWorkerJoin(unittest.TestCase):
    def test_real_layout_two_workers_three_native_rows_in_one_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = "/repo/.stage/wt/issues-2-6"
            _run_record(tmp, "build-s4a", "", start=1793129217.0, cwd=cwd)
            _run_record(tmp, "build-s3-fix1", "", start=1793128462.0, cwd=cwd)
            first = _worker("build-s4a", start=1793129217.0, pid=None)
            second = _worker("build-s3-fix1", start=1793128462.0, pid=None)
            manager = _manager(tmp, [first, second])
            bucket = _unattached(
                cwd,
                _native("thread-new", start=1793129218.0, cwd=cwd, pid=None).snap.workers[0],
                _native("thread-prior", start=1793128464.0, cwd=cwd, pid=None).snap.workers[0],
                _native("thread-old", start=1793127705.0, cwd=cwd, pid=None).snap.workers[0],
            )

            stages = collect._co_attach_executions([manager, bucket])

            self.assertEqual(stages, [manager, bucket])
            self.assertEqual([first.native_id, second.native_id], ["thread-new", "thread-prior"])
            self.assertEqual([worker.native_id for worker in bucket.snap.workers], ["thread-old"])
            self.assertEqual([worker.name for worker in manager.snap.workers], ["build-s4a", "build-s3-fix1"])

    def test_native_row_in_other_stage_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = "/repo/.stage/wt/issues-2-6"
            _run_record(tmp, "build-s4a", "", cwd=cwd)
            named = _worker("build-s4a", start=100.0, pid=None)
            manager = _manager(tmp, [named])
            native = _unattached(
                cwd, _native("other-stage-thread", start=103.0, cwd=cwd, pid=None).snap.workers[0])

            stages = collect._co_attach_executions([manager, native])

            self.assertEqual(stages, [manager])
            self.assertEqual(named.native_id, "other-stage-thread")
            self.assertTrue(named.execution_id)

    def test_codex_native_id_folds_into_named_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-s4a", "thread-123")
            named = _worker("build-s4a", pid=500)
            manager = _manager(tmp, [named])
            native = _native("thread-123", pid=999)

            stages = collect._co_attach_executions([manager, native])

            self.assertEqual(stages, [manager])
            self.assertEqual(manager.snap.workers, [named])
            self.assertEqual(named.native_id, "thread-123")
            self.assertTrue(named.execution_id)
            self.assertEqual(named.log.tokens_in, 17)
            self.assertEqual(named.log.cost, 0.25)

    def test_empty_codex_id_folds_wrapper_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-s4a", "")
            _run_record(tmp, "build-s4a-neighbour", "", start=101.0)
            named = _worker("build-s4a", start=100.0, pid=500, state="done")
            neighbour = _worker("build-s4a-neighbour", start=101.0, pid=600, state="done")
            manager = _manager(tmp, [named, neighbour])
            native = _native("thread-child", start=103.0, pid=503)

            stages = collect._co_attach_executions([manager, native], {})

            self.assertIn(native, stages)
            self.assertEqual(named.native_id, "")

            manager = _manager(tmp, [named, neighbour])
            procs = {500: (1, 0, 0, 0, 0), 503: (500, 0, 0, 0, 0)}
            stages = collect._co_attach_executions([manager, native], procs)

            self.assertEqual(stages, [manager])
            self.assertEqual([worker.name for worker in manager.snap.workers],
                             ["build-s4a", "build-s4a-neighbour"])
            self.assertEqual(named.native_id, "thread-child")

    def test_live_parallel_workers_resolved_by_pid_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-a", "")
            _run_record(tmp, "build-b", "")
            first = _worker("build-a", start=100.0, pid=500)
            second = _worker("build-b", start=101.0, pid=600)
            manager = _manager(tmp, [first, second])
            native_a = _native("thread-a", start=101.0, cwd="/repo", pid=503)
            native_b = _native("thread-b", start=101.5, cwd="/repo", pid=603)
            procs = {
                500: (1, 0, 0, 0, 0), 503: (500, 0, 0, 0, 0),
                600: (1, 0, 0, 0, 0), 603: (600, 0, 0, 0, 0),
            }

            stages = collect._co_attach_executions([manager, native_a, native_b], procs)

            self.assertEqual(stages, [manager])
            self.assertEqual([first.native_id, second.native_id], ["thread-a", "thread-b"])

    def test_reused_pid_does_not_join_old_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-old", "")
            old = _worker("build-old", start=100.0, pid=500, state="done")
            manager = _manager(tmp, [old])
            native = _native("thread-new", start=100000.0, cwd="/repo", pid=500)

            stages = collect._co_attach_executions([manager, native], {500: (1, 0, 0, 0, 0)})

            self.assertIn(native, stages)
            self.assertEqual(old.native_id, "")

    def test_two_natives_under_one_wrapper_both_stay_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-wrapper", "")
            wrapper = _worker("build-wrapper", start=100.0, pid=500)
            manager = _manager(tmp, [wrapper])
            native_a = _native("thread-a", start=101.0, cwd="/repo", pid=503)
            native_b = _native("thread-b", start=102.0, cwd="/repo", pid=504)
            procs = {
                500: (1, 0, 0, 0, 0),
                503: (500, 0, 0, 0, 0),
                504: (500, 0, 0, 0, 0),
            }

            stages = collect._co_attach_executions([manager, native_a, native_b], procs)

            self.assertCountEqual(stages, [manager, native_a, native_b])
            self.assertEqual(wrapper.native_id, "")
            self.assertEqual([worker.native_id for stage in (native_a, native_b)
                              for worker in stage.snap.workers], ["thread-a", "thread-b"])

    def test_pid_veto_blocks_time_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-a", "")
            _run_record(tmp, "build-b", "")
            first = _worker("build-a", start=100.0, pid=500)
            second = _worker("build-b", start=103.0, pid=600)
            manager = _manager(tmp, [first, second])
            native = _native("thread-b", start=101.0, cwd="/repo", pid=603)
            procs = {500: (1, 0, 0, 0, 0), 600: (1, 0, 0, 0, 0), 603: (600, 0, 0, 0, 0)}

            stages = collect._co_attach_executions([manager, native], procs)

            self.assertEqual(stages, [manager])
            self.assertEqual(first.native_id, "")
            self.assertEqual(second.native_id, "thread-b")

    def test_recordless_neighbour_blocks_the_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "a", "", start=100.0)
            named = _worker("a", start=100.0, pid=None)
            manual = _worker("manual", start=102.0, pid=None)
            manager = _manager(tmp, [named, manual])
            native = _native("thread-manual", start=102.0, cwd="/repo", pid=None)

            stages = collect._co_attach_executions([manager, native])

            self.assertIn(native, stages)
            self.assertEqual(named.native_id, "")

    def test_finished_unique_codex_pair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-s4a", "")
            named = _worker("build-s4a", start=100.0, pid=None, state="done")
            manager = _manager(tmp, [named])
            native = _native("finished-thread", start=104.0, pid=None)

            stages = collect._co_attach_executions([manager, native])

            self.assertEqual(stages, [manager])
            self.assertEqual([worker.name for worker in manager.snap.workers], ["build-s4a"])
            self.assertEqual(named.native_id, "finished-thread")

    def test_parallel_same_cwd_is_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-a", "")
            _run_record(tmp, "build-b", "")
            first = _worker("build-a", start=100.0, pid=None)
            second = _worker("build-b", start=100.0, pid=None)
            manager = _manager(tmp, [first, second])
            native_a = _native("thread-a", start=101.0, pid=None)
            native_b = _native("thread-b", start=101.0, pid=None)

            stages = collect._co_attach_executions([manager, native_a, native_b])

            self.assertIn(native_a, stages)
            self.assertIn(native_b, stages)
            self.assertEqual([first.native_id, second.native_id], ["", ""])

    def test_conflicting_ids_never_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _run_record(tmp, "build-s4a", "thread-record")
            named = _worker("build-s4a", pid=None)
            manager = _manager(tmp, [named])
            native = _native("thread-other", pid=None)

            stages = collect._co_attach_executions([manager, native])

            self.assertIn(native, stages)
            self.assertEqual(named.native_id, "")


if __name__ == "__main__":
    unittest.main()
