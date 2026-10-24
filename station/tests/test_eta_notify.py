from __future__ import annotations

import unittest

import app
from collect import _co_state
from model import DONE, QUIET, RUNNING, STALLED, LogState, Snapshot, Worker

NOW = 1_760_050_000.0


def finished(i: int, secs: float, model: str = "luna") -> Worker:
    return Worker(f"d{i}", DONE, model=model, start=NOW - 1000 - secs, end=NOW - 1000)


class EtaNotifyTests(unittest.TestCase):
    def test_empty_log_counts_as_running_not_stalled(self) -> None:
        self.assertEqual(_co_state(True, LogState(), False, False, 900, 0.0), RUNNING)
        streamed = LogState(offset=10)
        self.assertEqual(_co_state(True, streamed, False, False, 900, 0.0), STALLED)
        self.assertEqual(_co_state(True, streamed, False, False, 120, 50.0), QUIET)

    def test_eta_is_median_of_same_model_else_all(self) -> None:
        done = [finished(i, s) for i, s in enumerate((100, 200, 300))] + [finished(9, 900, "opus")]
        snap = Snapshot(now=NOW, workers=done)
        self.assertEqual(app._ap_eta(snap, Worker("w", RUNNING, model="luna")), 200)
        self.assertEqual(app._ap_eta(snap, Worker("w", RUNNING, model="haiku")), 250)
        self.assertIsNone(app._ap_eta(Snapshot(workers=done[:2]), Worker("w", RUNNING)))

    def test_overdue_fires_once_when_eta_is_crossed(self) -> None:
        done = [finished(i, 200) for i in range(3)]
        live = Worker("w", RUNNING, model="luna", start=NOW - 190)

        def snap(now: float) -> Snapshot:
            return Snapshot(now=now, workers=done + [Worker("w", RUNNING, model="luna", start=live.start)])

        crossed = app._ap_detect_events(snap(NOW), snap(NOW + 20))
        self.assertEqual([e.kind for e in crossed], ["overdue"])
        self.assertEqual(app._ap_detect_events(snap(NOW + 20), snap(NOW + 40)), [])
        self.assertEqual(app._ap_detect_events(snap(NOW - 20), snap(NOW)), [])

    def test_stall_alert_still_fires_when_an_eta_exists(self) -> None:
        done = [finished(i, 2000) for i in range(3)]
        before = Worker("w", RUNNING, model="luna", start=NOW - 400, log=LogState(offset=5))
        after = Worker("w", STALLED, model="luna", start=NOW - 400, quiet_s=320, log=LogState(offset=5))
        events = app._ap_detect_events(Snapshot(now=NOW, workers=done + [before]),
                                       Snapshot(now=NOW + 2, workers=done + [after]))
        self.assertEqual([e.kind for e in events], ["stalled"])


if __name__ == "__main__":
    unittest.main()
