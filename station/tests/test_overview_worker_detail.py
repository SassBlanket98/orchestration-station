from __future__ import annotations

import unittest

from model import (
    FAILED,
    NOREPORT,
    RUNNING,
    GlobalSnapshot,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def frame(workers: list[Worker], cols: int = 100, opened: tuple[str, ...] = ()) -> list[str]:
    snap = Snapshot(now=NOW, workers=workers)
    stage = Stage('/sample/.stage', 'detail-probe', snap, last_activity=NOW)
    ui = UIState(gopen={('/sample/.stage', 'worker', name) for name in opened})
    return [_wd_SGR_RE.sub('', row) for row in render_global(
        GlobalSnapshot(now=NOW, stages=[stage]), ui, cols, 40, NOW)]


class OverviewWorkerDetail(unittest.TestCase):
    def test_row_shows_model_runtime_and_current_action_detail_adds_output_age(self) -> None:
        worker = Worker('layout', RUNNING, model='luna medium', start=NOW - 240, quiet_s=3,
                        title='A full task description that belongs only in the worker detail',
                        log=LogState(steps=[Step(NOW - 3, 'tool', 'read', 'src/app.py')]))
        text = '\n'.join(frame([worker]))
        for value in ('luna medium', '04m 00s', 'src/app.py'):
            self.assertIn(value, text)
        self.assertNotIn(worker.title, text)
        opened = '\n'.join(frame([worker], opened=('layout',)))
        for value in (worker.title, 'output 3s ago'):
            self.assertIn(value, opened)

    def test_phone_width_keeps_runtime_and_action_and_detail_keeps_the_model(self) -> None:
        worker = Worker('layout', RUNNING, model='luna medium', start=NOW - 240, quiet_s=3,
                        log=LogState(steps=[Step(NOW - 3, 'tool', 'edit', 'src/app.py')]))
        rows = frame([worker], 57)
        text = '\n'.join(rows)
        for value in ('04m 00s', 'src/app.py'):
            self.assertIn(value, text)
        self.assertTrue(all(_wd_width(row) <= 57 for row in rows))
        opened = frame([worker], 57, opened=('layout',))
        self.assertIn('model luna medium', '\n'.join(opened))
        self.assertTrue(all(_wd_width(row) <= 57 for row in opened))

    def test_attention_rows_distinguish_error_from_missing_report(self) -> None:
        workers = [Worker('launch', FAILED, model='opus', start=NOW - 60, end=NOW - 50,
                          log=LogState(steps=[Step(NOW - 50, 'error', '', 'session limit reached')])),
                   Worker('missing', NOREPORT, model='ornith local', start=NOW - 60, end=NOW - 20)]
        stage = Stage('/sample/.stage', 'detail-probe', Snapshot(now=NOW, workers=workers), last_activity=NOW)
        ui = UIState(gopen={('/sample/.stage', 'stage', ''), ('', 'more', 'finished:/sample')})
        text = '\n'.join(_wd_SGR_RE.sub('', row) for row in render_global(GlobalSnapshot(now=NOW, stages=[stage]), ui, 100, 40, NOW))
        self.assertIn('session limit reached', text)   # the failed worker's why line
        self.assertIn('○ missing', text)               # no-report is the circle mark, not a state note

    def test_large_batch_lists_every_running_worker(self) -> None:
        workers = [Worker(f'worker-{i}', RUNNING, model='luna', start=NOW - 60) for i in range(11)]
        text = '\n'.join(frame(workers))
        self.assertIn('worker-10', text)              # only finished workers fold; every running one shows
        self.assertNotIn('earlier from this session', text)


if __name__ == '__main__':
    unittest.main()
