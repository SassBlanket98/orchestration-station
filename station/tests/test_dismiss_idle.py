from __future__ import annotations

import unittest

from collect import _co_empty_idle_leads, _co_filter_dismissed_stages
from model import DONE, RUNNING, Group, Snapshot, Stage, Worker


def worker(state: str = DONE) -> Worker:
    return Worker(name="worker", state=state, start=100.0, end=200.0 if state == DONE else None)


def stage(path: str = "execution:abc", *, lead_state: str = "gone", live: int = 0,
          activity: float = 100.0, last_activity: float = 100.0) -> Stage:
    return Stage(path=path, label="example", live=live, lead_state=lead_state,
                 activity=activity, last_activity=last_activity)


class DismissedRows(unittest.TestCase):
    def test_old_dismissed_execution_is_dropped_and_counted(self) -> None:
        row = stage()
        rows, count = _co_filter_dismissed_stages([row], {row.path: 100.0})
        self.assertEqual(rows, [])
        self.assertEqual(count, 1)

    def test_activity_after_dismissal_revives_execution_row(self) -> None:
        row = stage(activity=101.0)
        rows, count = _co_filter_dismissed_stages([row], {row.path: 100.0})
        self.assertEqual(rows, [row])
        self.assertEqual(count, 0)

    def test_live_lead_is_not_dismissed(self) -> None:
        row = stage(lead_state="live")
        rows, count = _co_filter_dismissed_stages([row], {row.path: 100.0})
        self.assertEqual(rows, [row])
        self.assertEqual(count, 0)


class EmptyIdleLeads(unittest.TestCase):
    def idle_lead(self, *, state: str = "idle", live: int = 0) -> Stage:
        wk = worker(RUNNING if live else DONE)
        return Stage(path="/project/.stage", label="project", lead_state="live", state=state,
                 live=live, snap=Snapshot(workers=[wk], groups=[Group("group", [wk.name], 100.0, 1, 1, live)]))

    def test_idle_live_lead_keeps_finished_workers_and_is_not_solo(self) -> None:
        row = self.idle_lead()
        _co_empty_idle_leads([row], all_history=False)
        self.assertEqual([wk.name for wk in row.snap.workers], ["worker"])
        self.assertEqual([group.label for group in row.snap.groups], ["group"])
        self.assertFalse(row.solo)
        self.assertEqual(row.live, 0)

    def test_active_lead_keeps_workers(self) -> None:
        row = self.idle_lead(state="active")
        workers = list(row.snap.workers)
        _co_empty_idle_leads([row], all_history=False)
        self.assertEqual(row.snap.workers, workers)

    def test_all_history_keeps_workers(self) -> None:
        row = self.idle_lead()
        workers = list(row.snap.workers)
        _co_empty_idle_leads([row], all_history=True)
        self.assertEqual(row.snap.workers, workers)

    def test_idle_lead_with_live_worker_keeps_it(self) -> None:
        row = self.idle_lead(live=1)
        workers = list(row.snap.workers)
        _co_empty_idle_leads([row], all_history=False)
        self.assertEqual(row.snap.workers, workers)


if __name__ == "__main__":
    unittest.main()
