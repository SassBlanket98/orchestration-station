import unittest

import widgets
from model import LogState, Stage, Step, Worker, WorkState
from opsgrid import (
    _og_badge,
    _og_Cells,
    _og_closed_line,
    _og_header,
    _og_idle_divider,
    _og_plan,
    _og_project_head,
    _og_row,
    _og_stage_cells,
    _og_worker_cells,
)
from widgets import _wd_SGR_RE, _wd_width


def plain(text):
    return _wd_SGR_RE.sub("", text)


class OpsGridTests(unittest.TestCase):
    def test_plan_thresholds(self):
        for cols, inline, spark, tools, tokens, short, model_w in (
            (80, False, False, False, False, True, 12),
            (89, False, False, False, False, True, 12),
            (90, False, False, False, True, True, 16),
            (99, False, False, False, True, True, 16),
            (100, False, False, True, True, False, 16),
            (111, False, False, True, True, False, 16),
            (112, False, True, True, True, False, 16),
            (149, False, True, True, True, False, 16),
            (150, False, True, True, True, False, 16),
            (200, False, True, True, True, False, 16),
        ):
            with self.subTest(cols=cols):
                plan = _og_plan(cols)
                self.assertEqual((False, plan.spark, plan.tools, plan.tokens, plan.short, plan.model_w),
                                 (inline, spark, tools, tokens, short, model_w))

    def test_all_lines_fit_for_wide_unicode_and_empty_values(self):
        worker = Worker(name="名" * 40, state="running", start=10, level_tag="WORKER",
                        log=LogState(steps=[Step(19, "text", "", "working on 東京")], last_ts=19))
        cells = _og_worker_cells(worker, 20)
        stage = Stage(path="/x", label="项目" * 30)
        for cols in range(80, 201, 7):
            plan = _og_plan(cols)
            lines = [_og_header(plan), *_og_row(plan, "│  ├─ ", "│  │  ", "◆", worker.name, cells, 20,
                                 why="very long failure reason " * 4),
                     _og_project_head(plan, "项目" * 30, True, True, "2 live · 1 idle", "", "$0.82"),
                     _og_idle_divider(plan, 5), _og_closed_line(plan, 31, 4),
                     _og_stage_cells_line(plan, stage)]
            for line in lines:
                self.assertEqual(_wd_width(line), cols, (cols, plain(line)))

    def test_header_and_row_metric_columns_align(self):
        for cols in (80, 89, 90, 99, 100, 111, 112, 149, 150, 200):
            plan = _og_plan(cols)
            worker = Worker("w", "done", log=LogState(harness="codex"))
            row = _og_row(plan, "├─ ", "│  ", "◆", "session", _og_worker_cells(worker, 100), 100)[0]
            head = _og_header(plan)
            self.assertEqual(_wd_width(row), cols)
            self.assertEqual(_wd_width(head), cols)
            start = cols - plan.fixed + 2
            harn_start = start + plan.tag_w + 1
            self.assertEqual(plain(head)[harn_start:harn_start + 5].strip(), "HARN")
            self.assertEqual(plain(row)[harn_start:harn_start + 5].strip(), "Codex")

    def test_unknowns_and_measured_zero(self):
        unknown = _og_worker_cells(Worker("unknown", "done"), 100)
        zero = _og_worker_cells(Worker("zero", "done", log=LogState(tokens_in=0, tokens_out=0, cost=0.0)), 100)
        self.assertIsNone(unknown.ctx_pct)
        self.assertIsNone(unknown.tokens)
        self.assertIsNone(unknown.cost)
        self.assertEqual(zero.tokens, 0)
        self.assertEqual(zero.cost, 0.0)
        line = plain(_og_row(_og_plan(120), "├─ ", "│  ", "◇", "unknown", unknown, 100)[0])
        self.assertIn("—", line)
        self.assertNotIn("$0.00", line)

    def test_context_threshold_colors(self):
        for pct, role in ((69, "SUB"), (70, "AMBER"), (89, "AMBER"), (90, "RED")):
            with self.subTest(pct=pct):
                cells = _og_stage_cells(Stage(path="/x", label="x", lead_ctx=pct * 1000, lead_ctx_window=100_000), 100)
                row = _og_row(_og_plan(120), "├─ ", "│  ", "◆", "x", cells, 100)[0]
                self.assertIn(f"{pct}k", plain(row))
                self.assertIn("38;2;{};{};{}".format(*widgets._wd_ctx_color(pct)), row)

    def test_token_and_cost_formatting(self):
        wk = Worker("w", "done", log=LogState(tokens_in=812_000, tokens_out=388_000, cost=0.71))
        line = plain(_og_row(_og_plan(150), "├─ ", "│  ", "✔", "w", _og_worker_cells(wk, 100), 100)[0])
        self.assertIn("1.2M", line)
        self.assertIn("$0.71", line)
        small = Worker("w", "done", log=LogState(cost=0.001))
        line = plain(_og_row(_og_plan(120), "├─ ", "│  ", "✔", "w", _og_worker_cells(small, 100), 100)[0])
        self.assertIn("<$0.01", line)

    def test_work_state_badges(self):
        self.assertIn("● uncommitted", plain(_og_badge(WorkState(dirty=True))))
        self.assertIn("● 2 unpushed", plain(_og_badge(WorkState(ahead=2))))
        self.assertIn("● unmerged", plain(_og_badge(WorkState(unmerged=True))))
        self.assertIn("✔ merged", plain(_og_badge(WorkState(branch="feature", dirty=False, unmerged=False))))
        self.assertEqual(_og_badge(WorkState()), "")


def _og_stage_cells_line(plan, stage):
    cells = _og_stage_cells(stage, 100)
    return _og_row(plan, "├─ ", "│  ", "◐", stage.label, cells, 100)[0]


class GridAlignmentTests(unittest.TestCase):
    """the user 2026-10-24: v moved every column, the header drifted off its data, and the phone lost session names."""
    WIDTHS = (40, 46, 57, 60, 80, 89, 90, 99, 100, 111, 112, 149, 150, 190, 240)

    @staticmethod
    def full_cells():
        return _og_Cells(tag="LEAD", harn="CC·P", model="Sonnet 5.5 · hi", ctx_pct=46, tokens=1_234_000, cost=12.5,
                         elapsed="23m 11s", tools=59, pulses=[(99.0, 3)], now="Bash: ls", now_age=2.0, live=True)

    def test_empty_phone_metrics_hide_before_the_width_trim(self):
        """At 60 columns empty ctx/cost free cells for the name column; wide widths ignore metric_rows."""
        empty = [_og_Cells(), _og_Cells()]
        bare, phone = _og_plan(60), _og_plan(60, metric_rows=empty)
        self.assertIn("ctx", dict(bare.columns()))
        self.assertNotIn("ctx", dict(phone.columns()))
        self.assertGreater(phone.name_w, bare.name_w)
        wide = _og_plan(120, metric_rows=empty)
        self.assertEqual((_og_plan(120).columns(), _og_plan(120).fixed), (wide.columns(), wide.fixed))

    def test_the_mode_never_moves_a_column(self):
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                classic, cc = _og_plan(cols, False), _og_plan(cols, True)
                self.assertEqual((classic.columns(), classic.fixed), (cc.columns(), cc.fixed))
                self.assertEqual(plain(_og_header(classic)), plain(_og_header(cc)))

    def test_every_header_cell_shares_its_edges_with_its_data(self):
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                plan = _og_plan(cols)
                head = plain(_og_header(plan))
                row = plain(_og_row(plan, "├─ ", "│  ", "◆", "session", self.full_cells(), 100)[0])
                x = plan.name_w + 2
                for key, width in plan.columns():
                    h, r = head[x:x + width], row[x:x + width]
                    if key in ("ctx", "tokens", "cost", "elapsed", "tools"):    # numbers: right edges match
                        self.assertEqual((len(h.rstrip()), len(r.rstrip())), (width, width), (key, h, r))
                    elif key == "spark":                                          # `30m` closes the sparkline's cell
                        self.assertTrue(h.endswith("30m") and len(h) == width, (key, h))
                    else:                                                         # text: left edges match
                        self.assertNotEqual(h[:1], " ", (key, h))
                        self.assertNotEqual(r[:1], " ", (key, r))
                    x += width + 1
                self.assertEqual(x - 1, cols)

    def test_the_name_column_never_starves_and_the_phone_keeps_its_model(self):
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                plan = _og_plan(cols)
                self.assertGreaterEqual(plan.name_w, 20 if cols >= 46 else 18)
                self.assertIn("elapsed", dict(plan.columns()))
                if cols >= 40:
                    self.assertIn("model", dict(plan.columns()))
                row = plain(_og_row(plan, "└─ ", "   ", "◆", "TUI ENHANCEMENT", self.full_cells(), 100)[0])
                self.assertIn("TUI ENHAN", row)

    def test_project_totals_sit_in_their_columns(self):
        for cols in (120, 150, 190):
            with self.subTest(cols=cols):
                plan = _og_plan(cols)
                totals = _og_Cells(tokens=2_000_000, cost=4.0, tools=120)
                line = plain(_og_project_head(plan, "sample-project", True, True, "1 live · 1 idle", "● uncommitted", "", False, totals))
                head = plain(_og_header(plan))
                x = plan.name_w + 2
                for key, width in plan.columns():
                    if key in ("tokens", "cost", "tools"):
                        self.assertEqual(len(line[x:x + width].rstrip()), width, (key, line[x:x + width]))
                    else:
                        self.assertEqual(line[x:x + width].strip(), "", (key, line[x:x + width]))
                    x += width + 1
                self.assertNotIn("1 live", line[plan.name_w + 2:])      # the counts end before the first metric column
                self.assertEqual(_wd_width(line), cols)
                self.assertEqual(_wd_width(head), cols)

    def test_a_narrow_heading_borrows_the_metric_zone_instead_of_shortening_the_name(self):
        plan = _og_plan(90)
        line = plain(_og_project_head(plan, "sample-project", True, True, "1 live · 1 idle", "● uncommitted", "", False,
                                      _og_Cells(tokens=1000)))
        self.assertIn("sample-project", line)
        self.assertIn("1 live · 1 idle", line)
        self.assertEqual(_wd_width(line), 90)

    def test_a_live_lead_always_has_a_now_line_and_a_quiet_worker_does_not(self):
        plan = _og_plan(120)
        lead = _og_Cells(tag="LEAD", live=True)
        self.assertEqual(len(_og_row(plan, "├─ ", "│  ", "◆", "lead", lead, 100)), 2)
        self.assertIn("no activity recorded", plain(_og_row(plan, "├─ ", "│  ", "◆", "lead", lead, 100)[1]))
        ticking = _og_Cells(tag="LEAD", live=True, pulses=[(97.0, 2)])
        self.assertIn("last tool call", plain(_og_row(plan, "├─ ", "│  ", "◆", "lead", ticking, 100)[1]))
        worker = _og_Cells(tag="WORKER", live=True)
        self.assertEqual(len(_og_row(plan, "├─ ", "│  ", "◆", "w", worker, 100)), 1)
        hidden = _og_Cells(tag="LEAD", live=True, sub=False)
        self.assertEqual(len(_og_row(plan, "├─ ", "│  ", "◆", "lead", hidden, 100)), 1)

    def test_a_long_tail_never_moves_a_metric_out_of_its_cell(self):
        """Sol review 2026-10-24: `▸ 2 spawns · ▲ 1` at 40 columns pushed MODEL four cells right."""
        from widgets import _wd_c, _wd_C_RED
        tail = _wd_c("▸ 12 spawns · ▲ 3 and then some", _wd_C_RED)
        for cols in self.WIDTHS:
            with self.subTest(cols=cols):
                plan = _og_plan(cols)
                bare = plain(_og_row(plan, "└─ ", "   ", "◆", "TUI ENHANCEMENT", self.full_cells(), 100)[0])
                tailed = _og_row(plan, "   ├─ ", "   │  ", "◆", "TUI ENHANCEMENT", self.full_cells(), 100, tail=tail)[0]
                self.assertEqual(_wd_width(tailed), cols)
                self.assertEqual(plain(tailed)[plan.name_w + 2:], bare[plan.name_w + 2:])

    def test_a_project_name_survives_a_phone_width(self):
        plan = _og_plan(46)
        line = plain(_og_project_head(plan, "sample-project (ops)", False, False, "1 idle · 1 done", "● uncommitted", "1h 23m ago"))
        self.assertIn("sample-project (ops)", line)

    def test_ultra_wide_heading_keeps_more_of_the_project_name(self):
        name = "x" * 60
        tail = ("9 live · 9 idle", "● uncommitted", "2h ago")
        wide = plain(_og_project_head(_og_plan(190), name, True, True, *tail, False))
        medium = plain(_og_project_head(_og_plan(150), name, True, True, *tail, False))
        self.assertGreater(wide.count("x"), medium.count("x"))


if __name__ == "__main__":
    unittest.main()
