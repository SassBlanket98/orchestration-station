"""S5: one session row per id; workers and headless runs de-duplicated across routes."""
from __future__ import annotations

import unittest

import collect
from model import DONE, RUNNING, Snapshot, Stage, Worker


def row(path: str, sid: str, *, lead: int | None = None, state: str = "active", dir_: str | None = None,
        workers: list[Worker] | None = None, **fields) -> Stage:
    snap = Snapshot(workers=list(workers or []))
    stage = Stage(path=path, label=path, dir=dir_ if dir_ is not None else path.split("#", 1)[0], snap=snap,
                  live=sum(w.state == RUNNING for w in snap.workers), lead_pid=lead,
                  session_id=sid, state=state, lead_state="live" if lead else "gone")
    for key, value in fields.items():
        setattr(stage, key, value)
    return stage


class SessionRowDedup(unittest.TestCase):
    def test_virtual_row_metadata_merges_into_real_stage(self) -> None:
        virtual = row("/p/.stage#42", "sess-1", dir_="", app=True, app_id="bridge", parent="launcher",
                      session_name="from virtual")
        real = row("/p/.stage", "sess-1", dir_="/p/.stage", lead=7, workers=[Worker("w", RUNNING)])
        (merged,) = collect._co_dedupe_sessions([virtual, real])
        self.assertEqual(merged.path, "/p/.stage")
        self.assertEqual((merged.app, merged.app_id, merged.parent, merged.session_name),
                         (True, "bridge", "launcher", "from virtual"))
        self.assertEqual([w.name for w in merged.snap.workers], ["w"])

    def test_split_session_row_folds_into_main_stage(self) -> None:
        main = row("/p/.stage", "sess-2", dir_="/p/.stage", lead=7)
        split = row("/p/.stage#sess-2", "sess-2", dir_="/p/.stage",
                    workers=[Worker("old", DONE)], state="closed")
        (merged,) = collect._co_dedupe_sessions([split, main])
        self.assertEqual(merged.path, "/p/.stage")
        self.assertEqual([w.name for w in merged.snap.workers], ["old"])

    def test_execution_path_loses_to_real_stage_with_same_session(self) -> None:
        station = row("/p/.stage", "sess-3", dir_="/p/.stage", lead=9)
        execution = row("execution:deadbeef", "sess-3", workers=[Worker("native", RUNNING)])
        (merged,) = collect._co_dedupe_sessions([execution, station])
        self.assertEqual(merged.path, "/p/.stage")
        self.assertEqual([w.name for w in merged.snap.workers], ["native"])


class UnattachedByProject(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = collect.GlobalCollector(["/p"], quota=False)
        self.addCleanup(self.collector.close)
        self.collector._co_proc_table = dict

    def test_headless_runs_in_one_project_share_one_visible_row(self) -> None:
        from test_step5b_attach import run

        from registry import execution_key

        first = run(cwd="/p/.stage/review")
        second = run(cwd="/p/.stage/other")
        second.record.execution_id = execution_key("codex", "personal", "run-2")
        second.record.native_id = "run-2"
        self.collector._co_link_headless([first, second])
        rows = self.collector._co_execution_stages([first, second], 200, True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].unattached)   # folded as one unattached run of the project
        self.assertEqual(len(rows[0].snap.workers), 2)

    def test_headless_runs_in_two_projects_stay_separate(self) -> None:
        from test_step5b_attach import run

        from registry import execution_key

        first = run(cwd="/a/alpha")
        second = run(cwd="/b/beta")
        second.record.execution_id = execution_key("codex", "personal", "run-2")
        second.record.native_id = "run-2"
        self.collector._co_link_headless([first, second])
        rows = self.collector._co_execution_stages([first, second], 200, True)
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(r.unattached for r in rows))
        self.assertEqual(sorted(r.label for r in rows), ["alpha", "beta"])


class UnattachedInTree(unittest.TestCase):
    def test_project_scoped_headless_row_appears_without_capital_a(self) -> None:
        from test_render_global import plain

        from model import GlobalSnapshot, UIState
        from render import _rd_g_rows, render_global

        stage = Stage(path="execution:bucket", dir="/work/proj", label="proj",
                      snap=Snapshot(workers=[Worker("w", RUNNING)]), state="active", cwd="/work/proj")
        gs = GlobalSnapshot(now=1_800_000_000.0, stages=[stage])
        self.assertEqual([r.stage.label for r in _rd_g_rows(gs, UIState())], ["proj"])
        text = "\n".join(plain(render_global(gs, UIState(), 100, 40, gs.now)))
        self.assertIn("proj", text)
        self.assertNotIn("unattached runs", text)


if __name__ == "__main__":
    unittest.main()
