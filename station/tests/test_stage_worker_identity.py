from __future__ import annotations

import os
import tempfile
import unittest

import app
import cckeys
import collect
import events
from model import DONE, RUNNING, Group, Snapshot, Stage, UIState, Worker
from render import _rd_g_rowdata, _rd_g_tasks
from webdata import _groups, _worker_dict


def worker(name: str, home: str, **kwargs) -> Worker:
    return Worker(name=name, state=kwargs.pop("state", RUNNING), home=home, **kwargs)


def stage(path: str, workers: list[Worker], *, session: str = "", lead: str = "gone") -> Stage:
    return Stage(path=path, label=os.path.basename(path), dir=path, session_id=session,
                 lead_state=lead, lead_pid=1 if lead == "live" else None,
                 snap=Snapshot(workers=workers), live=sum(w.state == RUNNING for w in workers))


class StageWorkerIdentity(unittest.TestCase):
    def test_same_name_different_stages_survive_adoption(self) -> None:
        lead = stage("/project", [], session="lead-session", lead="live")
        one = stage("/project/.stage/cabinet-a", [worker("w1", "", session="lead-session", pid=11)])
        two = stage("/project/.stage/cabinet-b", [worker("w1", "", session="lead-session", pid=12)])
        out = collect._co_adopt_by_session([lead, one, two])
        self.assertEqual([s.path for s in out], ["/project"])
        self.assertEqual({(w.home, w.name) for w in lead.snap.workers}, {
            ("/project/.stage/cabinet-a", "w1"), ("/project/.stage/cabinet-b", "w1")})
        self.assertEqual(len({w.key for w in lead.snap.workers}), 2)

    def test_pidless_same_name_adopted_by_lead(self) -> None:
        lead = stage("/project", [], session="lead-session", lead="live")
        one = stage("/project/.stage/cabinet-a", [worker("w1", "", session="lead-session", pid=None)])
        two = stage("/project/.stage/cabinet-b", [worker("w1", "", session="lead-session", pid=None)])
        out = collect._co_adopt_by_session([lead, one, two])
        self.assertEqual([s.path for s in out], ["/project"])
        self.assertEqual({w.home for w in lead.snap.workers}, {
            "/project/.stage/cabinet-a", "/project/.stage/cabinet-b"})
        self.assertEqual(len(lead.snap.workers), 2)

    def test_homeless_same_name_different_pids_adopted_both(self) -> None:
        lead = stage("/project", [worker("w1", "", session="lead-session", pid=11)],
                     session="lead-session", lead="live")
        other = stage("/project/.stage/cabinet-b",
                      [worker("w1", "", session="lead-session", pid=12)])
        out = collect._co_adopt_by_session([lead, other])
        self.assertEqual([s.path for s in out], ["/project"])
        self.assertEqual({(w.name, w.pid) for w in lead.snap.workers}, {("w1", 11), ("w1", 12)})

    def test_no_live_lead_merge_keeps_all_workers(self) -> None:
        first = stage("/project/.stage/a", [worker("w1", "/project/.stage/a", pid=11)], session="shared")
        second = stage("/project/.stage/b", [worker("w1", "/project/.stage/b", pid=12)], session="shared")
        out = collect._co_dedupe_sessions([first, second])
        self.assertEqual(len(out), 1)
        self.assertEqual({w.key for w in out[0].snap.workers}, {
            Worker("w1", RUNNING, home="/project/.stage/a").key,
            Worker("w1", RUNNING, home="/project/.stage/b").key,
        })

    def test_homeless_same_name_different_pids_stay_two_in_dedupe(self) -> None:
        first = stage("/project/.stage/a", [worker("w1", "", pid=11)], session="shared")
        second = stage("/project/.stage/b", [worker("w1", "", pid=12)], session="shared")
        out = collect._co_dedupe_sessions([first, second])
        self.assertEqual(len(out), 1)
        self.assertEqual({(w.name, w.pid) for w in out[0].snap.workers}, {("w1", 11), ("w1", 12)})

    def test_native_identity_survives_home_assignment_and_dedupe(self) -> None:
        first_worker = worker("Same title", "", execution_id="native-a", native_id="a")
        second_worker = worker("Same title", "", execution_id="native-b", native_id="b")
        before = {first_worker.key, second_worker.key}
        first_worker.home = second_worker.home = "/project/.stage"
        self.assertEqual(before, {"native-a", "native-b"})
        self.assertEqual({first_worker.key, second_worker.key}, before)

        first = stage("/project/.stage/a", [first_worker], session="shared")
        second = stage("/project/.stage/b", [second_worker], session="shared")
        out = collect._co_dedupe_sessions([first, second])
        self.assertEqual(len(out[0].snap.workers), 2)

    def test_pidless_same_name_survives_dedupe(self) -> None:
        first = stage("/project/.stage/a", [worker("w1", "/project/.stage/a", pid=None)], session="shared")
        second = stage("/project/.stage/b", [worker("w1", "/project/.stage/b", pid=None)], session="shared")
        out = collect._co_dedupe_sessions([first, second])
        self.assertEqual(len(out), 1)
        self.assertEqual({w.home for w in out[0].snap.workers}, {
            "/project/.stage/a", "/project/.stage/b"})
        self.assertEqual(len(out[0].snap.workers), 2)

    def test_homeless_duplicate_seen_from_two_stage_dirs_counts_once_with_pidless_workers(self) -> None:
        lead = stage("/project/.stage", [worker("w", "", pid=None)], session="shared", lead="live")
        other = stage("/project/.stage/review", [worker("w", "", pid=None)], session="shared")
        out = collect._co_dedupe_sessions([lead, other])
        self.assertEqual(([w.name for w in out[0].snap.workers], out[0].live), (["w"], 1))

    def test_parent_keys_do_not_cross_stages(self) -> None:
        parent_a = worker("w1", "/stage/a", pid=101, native_id="parent-a")
        parent_b = worker("w1", "/stage/b", pid=201, native_id="parent-b")
        child_b = worker("child", "/stage/b", pid=202, session="parent-b")
        row = stage("/lead", [child_b, parent_b, parent_a])
        collect._co_nest_workers([row], {})
        self.assertEqual(child_b.parent, "w1")
        self.assertEqual(child_b.parent_key, parent_b.key)
        self.assertNotEqual(child_b.parent_key, parent_a.key)

    def test_selection_and_events_distinguish_w1(self) -> None:
        a = worker("w1", "/stage/a", state=RUNNING, pid=11, start=1)
        b = worker("w1", "/stage/b", state=DONE, pid=12, start=1, end=2)
        snap = Snapshot(workers=[a, b])
        ui = UIState()
        app._ap_toggle_expanded(ui, a)
        app._ap_toggle_expanded(ui, b)
        self.assertEqual(ui.expanded, {a.key, b.key})
        prev = Snapshot(now=1, workers=[a, worker("w1", "/stage/b", state=RUNNING, pid=12, start=1)])
        changes = app._ap_detect_events(prev, snap)
        self.assertEqual([event.key for event in changes], [b.key])
        row = stage("/lead", [a])
        self.assertEqual(events.row_key(row, a), f"/lead|{a.key}")
        self.assertEqual(cckeys._cxk_row_key(row, a), f"/lead|{a.key}")

    def test_origin_detail_route_reads_correct_report(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            a, b = os.path.join(root, "cabinet-a"), os.path.join(root, "cabinet-b")
            os.makedirs(a)
            os.makedirs(b)
            for directory, content in ((a, "report from a"), (b, "report from b")):
                os.makedirs(os.path.join(directory, "w1"))
                with open(os.path.join(directory, "w1", "REPORT.md"), "w", encoding="utf-8") as handle:
                    handle.write(content)
            collector = collect.GlobalCollector([root], quota=False, dismissed=os.path.join(root, "dismissed.json"))
            try:
                collector._co_collectors = {a: collect.Collector(a, quota=False), b: collect.Collector(b, quota=False)}
                collector._co_last_stages = [stage(a, [worker("w1", a)]), stage(b, [worker("w1", b)])]
                self.assertEqual(collector.detail(a, "w1", "report"), ["report from a"])
                self.assertEqual(collector.detail(b, "w1", "report"), ["report from b"])
            finally:
                collector.close()
        w = worker("w1", a)
        api = _worker_dict(w)
        self.assertEqual((api["name"], api["stage"], api["key"]), ("w1", a, w.key))
        self.assertEqual(api["parent_key"], "")

    def test_legacy_unique_name_group(self) -> None:
        w = worker("unique", "/stage")
        group = Group("one", ["unique"], 1, 0, 1, 1)
        row = stage("/lead", [w])
        row.snap.groups = [group]
        tasks, _ = _rd_g_tasks(_rd_g_rowdata(row))
        self.assertEqual([[item.name for item in members] for _, members in tasks], [["unique"]])
        serialized = _groups(row.snap)[0]
        self.assertEqual(serialized["workers"], ["unique"])
        self.assertEqual(serialized["worker_keys"], [w.key])


if __name__ == "__main__":
    unittest.main()
