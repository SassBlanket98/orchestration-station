"""Phone width: hide metric columns that are empty on every drawn row (the user Termux screenshot 2026-10-24)."""
from __future__ import annotations

import unittest

from model import GlobalSnapshot, LeadInfo, LogState, Snapshot, Stage, UIState, Worker
from opsgrid import _og_plan
from render import _rd_g_metric_cells, _rd_g_nodes, render_global
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


def snapshot(cost: float | None = None) -> GlobalSnapshot:
    """Live project with a long session name and no ctx/cost unless asked."""
    log = LogState(harness="claude")
    if cost is not None:
        log.cost = cost
    worker = Worker(
        "w1",
        "running",
        goal="work",
        start=NOW - 120,
        log=log,
    )
    name = "Board usage modal indicator rebuild"
    stage = Stage(
        "/plant/.stage/a",
        "plant",
        Snapshot(now=NOW, workers=[worker]),
        1,
        NOW - 60,
        LeadInfo("composer", "build", "s"),
    )
    stage.state, stage.lead_state = "active", "live"
    stage.session_name, stage.dir, stage.tag = name, "/plant", "CC·P"
    stage.lead_model = "composer"
    if cost is not None:
        stage.lead_cost = cost
    return GlobalSnapshot(now=NOW, stages=[stage])


class PhoneEmptyMetricColumns(unittest.TestCase):
    def test_empty_ctx_and_cost_drop_at_phone_widths(self) -> None:
        gs = snapshot()
        ui = UIState(gquips=False, glegend=False)
        cells = _rd_g_metric_cells(_rd_g_nodes(gs, ui), NOW)
        for cols in (46, 60):
            with self.subTest(cols=cols):
                bare = _og_plan(cols)
                plan = _og_plan(cols, metric_rows=cells)
                self.assertGreaterEqual(plan.name_w, bare.name_w)
                if cols == 60:
                    self.assertIn("ctx", dict(bare.columns()))
                    self.assertIn("cost", dict(bare.columns()))
                    self.assertGreater(plan.name_w, bare.name_w)
                lines = plain(render_global(gs, ui, cols, 40, NOW))
                header = next(line for line in lines if "SESSION" in line)
                self.assertNotIn("CTX", header)
                self.assertNotIn("COST", header)
                row = next(line for line in lines if "Board" in line)
                self.assertIn("Board usage", row)

    def test_one_real_cost_keeps_the_cost_column(self) -> None:
        gs = snapshot(cost=0.42)
        ui = UIState(gquips=False, glegend=False)
        cells = _rd_g_metric_cells(_rd_g_nodes(gs, ui), NOW)
        plan = _og_plan(60, metric_rows=cells)
        self.assertNotIn("ctx", dict(plan.columns()))
        self.assertIn("cost", dict(plan.columns()))
        lines = plain(render_global(gs, ui, 60, 40, NOW))
        header = next(line for line in lines if "SESSION" in line)
        self.assertNotIn("CTX", header)
        self.assertIn("COST", header)
        self.assertIn("$0.42", "\n".join(lines))

    def test_wide_width_unchanged(self) -> None:
        gs = snapshot()
        ui = UIState(gquips=False, glegend=False)
        cells = _rd_g_metric_cells(_rd_g_nodes(gs, ui), NOW)
        bare = _og_plan(120)
        with_rows = _og_plan(120, metric_rows=cells)
        self.assertEqual((with_rows.columns(), with_rows.fixed, with_rows.hidden), (bare.columns(), bare.fixed, bare.hidden))
        frame = render_global(gs, ui, 120, 40, NOW)
        self.assertTrue(all(_wd_width(line) == 120 for line in frame))
        header = next(line for line in plain(frame) if "SESSION" in line)
        self.assertIn("CTX", header)
        self.assertIn("COST", header)


if __name__ == "__main__":
    unittest.main()
