"""Nested workers: lineage read from <name>.session, parent linking, tree order, the terminal task list and /api/state."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from collect import _co_nest_workers, _co_worker_lineage
from model import DONE, RUNNING, Group, Snapshot, Stage, Worker, nest_workers
from render import _rd_g_rowdata, _rd_g_tasks
from webdata import _worker_dict


def wk(name: str, **kw) -> Worker:
    return Worker(name=name, state=kw.pop("state", RUNNING), start=kw.pop("start", 1.0), **kw)


def stage_of(workers: list[Worker]) -> Stage:
    return Stage(path="/p/.stage", label="p", snap=Snapshot(title="p", now=10.0, workers=workers))


class Lineage(unittest.TestCase):
    def read(self, data) -> tuple:
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "w.session"), "w") as fh:
                json.dump(data, fh)
            return _co_worker_lineage(d, "w")

    def test_schema_2_fields(self) -> None:
        self.assertEqual(self.read({"lead_session": "a", "parent_session": "r", "root_session": "r", "depth": 1}),
                         ("a", "r", "r", 2, (), 0.0))

    def test_unknown_own_id_falls_back_to_the_nearest_ancestor(self) -> None:
        self.assertEqual(self.read({"lead_session": None, "parent_session": "mid", "root_session": "r", "depth": 2})[0], "mid")

    def test_old_file_and_junk(self) -> None:
        self.assertEqual(self.read({"lead_session": "a"}), ("a", "", "", 0, (), 0.0))
        self.assertEqual(self.read([1]), ("", "", "", 0, (), 0.0))
        self.assertEqual(self.read({"depth": True, "root_session": 5}), ("", "", "", 0, (), 0.0))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_co_worker_lineage(d, "absent"), ("", "", "", 0, (), 0.0))


class Linking(unittest.TestCase):
    def test_native_id_then_process_ancestry_at_any_depth(self) -> None:
        a = wk("a", pid=100, native_id="sid-a")
        b = wk("b", pid=200, session="sid-a")                 # launched by a: its launcher id is a's own id
        c = wk("c", pid=300)                                   # no ids; a descendant process of b
        procs = {100: (1, 0, 0, 0, 0), 200: (100, 0, 0, 0, 0), 250: (200, 0, 0, 0, 0), 300: (250, 0, 0, 0, 0)}
        st = stage_of([c, a, b])
        _co_nest_workers([st], procs)
        self.assertEqual([(w.name, w.parent, w.level) for w in st.snap.workers], [("a", "", 0), ("b", "a", 1), ("c", "b", 2)])

    def test_recorded_ancestors_link_finished_workers(self) -> None:
        a = wk("a", pid=100, start=1.0, state=DONE, end=5.0)
        b = wk("b", pid=200, ancestors=(150, 100, 1), launched=3.0, state=DONE, end=6.0)
        reused = wk("r", pid=300, ancestors=(100,), launched=0.5, state=DONE, end=6.0)   # pid 100 was not a yet: not a child
        st = stage_of([b, reused, a])
        _co_nest_workers([st], {})
        self.assertEqual({w.name: w.parent for w in st.snap.workers}, {"a": "", "b": "a", "r": ""})

    def test_ancestors_and_launch_time_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "w.session"), "w") as fh:
                json.dump({"ancestors": [5, 1, "x", True, 9], "launched_at": 12.5}, fh)
            got = _co_worker_lineage(d, "w")
        self.assertEqual((got[4], got[5]), ((5, 9), 12.5))

    def test_execution_parent_id(self) -> None:
        a, b = wk("a", execution_id="x:1"), wk("b", parent_id="x:1")
        st = stage_of([b, a])
        _co_nest_workers([st], {})
        self.assertEqual([(w.name, w.level) for w in st.snap.workers], [("a", 0), ("b", 1)])

    def test_loop_is_cut_and_unrelated_workers_stay_flat(self) -> None:
        a, b, d = wk("a", pid=1000, native_id="A", session="B"), wk("b", pid=2000, native_id="B", session="A"), wk("d")
        st = stage_of([a, b, d])
        _co_nest_workers([st], {})
        self.assertEqual(sorted(w.name for w in st.snap.workers), ["a", "b", "d"])
        self.assertTrue(all(w.level <= 1 for w in st.snap.workers))
        self.assertEqual(d.level, 0)

    def test_nest_workers_keeps_a_worker_whose_parent_is_missing(self) -> None:
        out = nest_workers([wk("x", parent="gone", level=1), wk("y")])
        self.assertEqual([w.name for w in out], ["x", "y"])


class Rendering(unittest.TestCase):
    def test_task_list_puts_children_under_their_parent_across_tasks(self) -> None:
        a = wk("a")
        b = wk("b", parent="a", level=1, state=DONE, end=5.0)
        c = wk("c", parent="b", level=2)
        z = wk("z")
        st = stage_of([a, b, c, z])
        st.snap.groups = [Group("g1", ["a", "z"], 1.0, 0, 2, 2), Group("g2", ["b", "c"], 2.0, 1, 2, 1)]
        tasks, _ = _rd_g_tasks(_rd_g_rowdata(st))
        self.assertEqual([[w.name for w in m] for _, m in tasks], [["a", "b", "c", "z"]])


class Api(unittest.TestCase):
    def test_worker_dict_carries_the_tree_fields(self) -> None:
        d = _worker_dict(wk("b", parent="a", level=1, parent_session="p", root_session="r", depth=2))
        self.assertEqual((d["parent"], d["level"], d["parent_session"], d["root_session"], d["depth"]), ("a", 1, "p", "r", 2))


if __name__ == "__main__":
    unittest.main()
