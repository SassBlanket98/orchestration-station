from __future__ import annotations

import unittest

import collect
from model import DONE, RUNNING, Snapshot, Stage, Worker

NOW = 1_800_000_000.0


def worker(name: str, session: str, *, state: str = RUNNING, pid: int | None = None, start: float = NOW) -> Worker:
    return Worker(name=name, state=state, pid=pid, start=start,
                  end=None if state == RUNNING else start + 60, session=session)


def row(path: str, *, session_id: str = "", lead_state: str = "gone", workers: list[Worker] | None = None,
        solo: bool = False, execution_id: str = "") -> Stage:
    snap = Snapshot(workers=list(workers or []))
    return Stage(path=path, label=path.rsplit("/", 1)[-1], snap=snap, session_id=session_id,
                 lead_state=lead_state, lead_pid=1 if lead_state == "live" else None, solo=solo,
                 execution_id=execution_id, live=sum(w.state == RUNNING for w in snap.workers),
                 last_activity=NOW - 1000)


class AdoptBySession(unittest.TestCase):
    LEAD = "sess-lead"

    def test_workers_in_a_nested_stage_move_under_their_live_lead(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live", solo=True)
        nested = row("/proj/.stage/visibility-build", session_id="sess-other", workers=[
            worker("a", self.LEAD, pid=10), worker("b", self.LEAD, state=DONE, pid=11, start=NOW - 500),
        ])
        out = collect._co_adopt_by_session([lead, nested])
        self.assertEqual([s.path for s in out], ["/proj"])
        self.assertEqual({w.name for w in lead.snap.workers}, {"a", "b"})
        self.assertEqual(nested.snap.workers, [])
        self.assertEqual(lead.snap.workers[0].home, "/proj/.stage/visibility-build")

    def test_a_worker_with_another_session_stays_put(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live")
        nested = row("/proj/.stage/visibility-build", session_id="sess-other", workers=[
            worker("a", self.LEAD, pid=10), worker("c", "sess-third", pid=12),
        ])
        out = collect._co_adopt_by_session([lead, nested])
        self.assertEqual({w.name for w in lead.snap.workers}, {"a"})
        self.assertEqual([w.name for w in nested.snap.workers], ["c"])
        self.assertEqual([s.path for s in out], ["/proj", "/proj/.stage/visibility-build"])

    def test_a_worker_with_an_empty_session_never_moves(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live")
        nested = row("/proj/.stage/visibility-build", session_id="sess-other", workers=[worker("d", "", pid=13)])
        out = collect._co_adopt_by_session([lead, nested])
        self.assertEqual(lead.snap.workers, [])
        self.assertEqual([w.name for w in nested.snap.workers], ["d"])
        self.assertEqual(len(out), 2)

    def test_the_lead_loses_solo_and_its_live_count_grows(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live", solo=True,
                   workers=[worker("own", self.LEAD, state=DONE, pid=5, start=NOW - 900)])
        nested = row("/proj/.stage/visibility-build", session_id="sess-other", workers=[
            worker("a", self.LEAD, pid=10, start=NOW - 10), worker("b", self.LEAD, state=DONE, pid=11, start=NOW - 100),
        ])
        collect._co_adopt_by_session([lead, nested])
        self.assertFalse(lead.solo)
        self.assertEqual(lead.live, 1)
        self.assertEqual(len(lead.snap.workers), 3)
        self.assertEqual(lead.snap.workers[0].name, "a")          # live first
        self.assertEqual(lead.last_activity, NOW - 10)             # newest moved worker start
        self.assertTrue(lead.snap.groups)
        self.assertEqual(sum(len(g.workers) for g in lead.snap.groups), 3)

    def test_a_lead_that_is_not_live_receives_nothing(self) -> None:
        gone = row("/proj", session_id=self.LEAD, lead_state="gone", solo=False)
        nested = row("/proj/.stage/visibility-build", session_id="sess-other",
                     workers=[worker("a", self.LEAD, pid=10)])
        out = collect._co_adopt_by_session([gone, nested])
        self.assertEqual(gone.snap.workers, [])
        self.assertEqual([w.name for w in nested.snap.workers], ["a"])
        self.assertEqual(len(out), 2)

    def test_a_duplicate_name_and_pid_is_not_moved_twice(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live", workers=[worker("a", self.LEAD, pid=10)])
        nested = row("/proj/.stage/visibility-build", session_id="sess-other", workers=[worker("a", self.LEAD, pid=10)])
        collect._co_adopt_by_session([lead, nested])
        self.assertEqual(len(lead.snap.workers), 1)
        self.assertEqual([w.name for w in nested.snap.workers], ["a"])

    def test_execution_rows_are_untouched(self) -> None:
        lead = row("/proj", session_id=self.LEAD, lead_state="live")
        exe = row("exec:1", session_id="sess-other", execution_id="ex-1", workers=[worker("x", self.LEAD, pid=20)])
        collect._co_adopt_by_session([lead, exe])
        self.assertEqual(lead.snap.workers, [])
        self.assertEqual([w.name for w in exe.snap.workers], ["x"])


if __name__ == "__main__":
    unittest.main()
