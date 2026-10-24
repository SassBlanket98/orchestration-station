from __future__ import annotations

import unittest

from collect import _co_dedupe_sessions
from model import DONE, RUNNING, Snapshot, Stage, Worker


def row(path: str, sid: str, workers: list[Worker], *, lead: int | None, state: str, dir_: str = "") -> Stage:
    return Stage(path=path, label=path, snap=Snapshot(workers=workers), live=sum(w.state == RUNNING for w in workers),
                 lead_pid=lead, session_id=sid, state=state, dir=dir_ or path)


class SessionMerge(unittest.TestCase):
    def test_the_live_lead_row_keeps_the_session_and_takes_workers_launched_elsewhere(self) -> None:
        lead = row("/agent-work/.stage", "s1", [], lead=7, state="idle")
        other = row("/quota/.stage/review#s1", "s1", [Worker(name="review-muse", state=RUNNING), Worker(name="old", state=DONE)],
                    lead=None, state="closed", dir_="/quota/.stage/review")
        out = _co_dedupe_sessions([other, lead])
        self.assertEqual([s.path for s in out], ["/agent-work/.stage"])
        merged = out[0]
        self.assertEqual({w.name: w.home for w in merged.snap.workers}, {"review-muse": "/quota/.stage/review", "old": "/quota/.stage/review"})
        self.assertEqual((merged.live, merged.state), (1, "active"))

    def test_two_live_leads_sharing_a_session_id_stay_two_rows(self) -> None:
        first = row("/agent-work/.stage", "s9", [], lead=7, state="active")
        second = row("/agent-work/.stage#8", "s9", [], lead=8, state="active", dir_="")
        out = _co_dedupe_sessions([first, second])
        self.assertEqual(sorted(s.lead_pid for s in out), [7, 8])

    def test_a_worker_seen_from_two_stage_dirs_counts_once(self) -> None:
        lead = row("/p/.stage", "s3", [Worker(name="w", state=RUNNING)], lead=7, state="active")
        other = row("/p/.stage/review#s3", "s3", [Worker(name="w", state=RUNNING)], lead=None, state="closed")
        merged = _co_dedupe_sessions([lead, other])[0]
        self.assertEqual(([w.name for w in merged.snap.workers], merged.live), (["w"], 1))

    def test_without_a_live_lead_the_row_with_workers_still_wins_alone(self) -> None:
        a = row("/a/.stage", "s2", [], lead=None, state="closed")
        b = row("/b/.stage", "s2", [Worker(name="w", state=DONE)], lead=None, state="closed")
        out = _co_dedupe_sessions([a, b])
        self.assertEqual([s.path for s in out], ["/b/.stage"])
        self.assertEqual(out[0].snap.workers[0].home, "")


if __name__ == "__main__":
    unittest.main()
