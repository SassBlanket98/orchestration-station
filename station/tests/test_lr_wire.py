from __future__ import annotations

import unittest

import app
from model import (
    DONE,
    NOREPORT,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import (
    _rd_g_doing_lines,
    _rd_g_group_info,
    _rd_g_rows,
    render_global,
    render_global_plain,
)
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0


def station(path: str, label: str, workers: list[Worker], last: float = NOW - 30,
            lead: LeadInfo | None = None, **snap_kw) -> Stage:
    return Stage(path, label, Snapshot(now=NOW, workers=workers, **snap_kw),
                 sum(w.state == RUNNING for w in workers), last, lead)


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


class NoreportNotifier(unittest.TestCase):
    def test_noreport_is_low_urgency_finished_without_report(self) -> None:
        prev = Snapshot(now=NOW, workers=[Worker("b", RUNNING, start=NOW - 100)])
        new = Snapshot(now=NOW + 1, workers=[Worker("b", NOREPORT, start=NOW - 100, end=NOW)])
        evs = app._ap_detect_events(prev, new)
        self.assertEqual([(e.kind, e.summary, e.urgency) for e in evs],
                         [("noreport", "· b finished without a report", "low")])

    def test_done_is_not_a_problem_event(self) -> None:
        prev = Snapshot(now=NOW, workers=[Worker("a", RUNNING, start=NOW - 100)])
        new = Snapshot(now=NOW + 1, workers=[Worker("a", DONE, start=NOW - 100, end=NOW)])
        self.assertNotIn("critical", [e.urgency for e in app._ap_detect_events(prev, new)])


class UnattachedRuns(unittest.TestCase):
    def setUp(self) -> None:
        alpha = station("/a", "alpha", [], lead=LeadInfo("opus", "build"))
        alpha.state = "active"                                  # a live session row; idle projects start shut
        orphan = station("/o", "orphan", [Worker("w1", RUNNING, start=NOW - 60)], last=NOW - 5)
        orphan.unattached = True
        self.gs = GlobalSnapshot(now=NOW, stages=[alpha, orphan], unattached=1)

    def test_hidden_by_default_with_footer(self) -> None:
        frame = plain(render_global(self.gs, UIState(), 100, 40, NOW))
        text = "\n".join(frame)
        self.assertNotIn("orphan", text)
        self.assertIn("alpha", text)
        self.assertIn("1 unattached runs (A to show)", text)

    def test_hidden_from_rows(self) -> None:
        self.assertEqual([r.stage.label for r in _rd_g_rows(self.gs, UIState())], ["alpha"])

    def test_capital_a_shows_them_and_a_stays_history(self) -> None:
        ui = UIState()
        self.assertEqual(app._ap_global_key(ui, "A", self.gs, 40), "")
        self.assertTrue(ui.gunattached)
        text = "\n".join(plain(render_global(self.gs, ui, 100, 40, NOW)))
        self.assertIn("orphan", text)
        self.assertIn("1 unattached runs (A to hide)", text)
        app._ap_global_key(ui, "A", self.gs, 40)
        self.assertFalse(ui.gunattached)
        shown = UIState()
        self.assertEqual(app._ap_global_key(shown, "a", self.gs, 40), "toggle_all")
        self.assertFalse(shown.gunattached)   # `a` is history only

    def test_hiding_with_the_cursor_on_an_orphan_keeps_selection_in_range(self) -> None:
        ui = UIState(gunattached=True)
        ui.gsel = len(app.global_rows(self.gs, ui)) - 1
        app._ap_global_key(ui, "A", self.gs, 40)
        self.assertLess(ui.gsel, len(app.global_rows(self.gs, ui)))

    def test_plain_keeps_them_labelled(self) -> None:
        out = render_global_plain(self.gs, 100)
        self.assertIn("orphan", out)
        self.assertIn("unattached", out)


class DoingPane(unittest.TestCase):
    def test_doing_note_shown_when_no_actions(self) -> None:
        stage = Stage("/a", "alpha", Snapshot(now=NOW), 0, NOW - 1, LeadInfo("opus", "build"))
        stage.lead_kind = "claude"
        stage.doing_note = "no session file"
        text = "\n".join(plain(_rd_g_doing_lines(stage, 100, NOW)))
        self.assertIn("no session file", text)
        self.assertNotIn("nothing in its transcript yet", text)

    def test_goal_and_last_action_with_age(self) -> None:
        stage = Stage("/a", "alpha", Snapshot(now=NOW), 0, NOW - 1, LeadInfo("opus", "build"))
        stage.lead_kind = "claude"
        stage.goal = "tighten gpu rows"
        stage.doing = [(NOW - 120, "Edit render.py"), (NOW - 30, "Run tests")]
        lines = plain(_rd_g_doing_lines(stage, 100, NOW))
        self.assertIn("tighten gpu rows", lines[0])
        self.assertIn("Edit render.py", lines[1])
        self.assertIn("Run tests", lines[2])
        self.assertIn("30s ago", lines[2])


class GroupHeadingCount(unittest.TestCase):
    def test_heading_counts_all_twenty_rows(self) -> None:
        stages = []
        for i in range(20):
            s = station(f"/s{i}", f"s{i}", [], lead=LeadInfo("opus", "build"))
            s.state, s.lead_state, s.tag = "idle", "live", "CC·W"
            stages.append(s)
        gs = GlobalSnapshot(now=NOW, stages=stages)
        ui = UIState()
        rows = _rd_g_rows(gs, ui)
        self.assertEqual(len(rows), 20)
        self.assertEqual(len(_rd_g_group_info(ui, rows)), 20)
        text = "\n".join(plain(render_global(gs, ui, 100, 60, NOW)))
        self.assertNotIn("more idle", text)                     # every idle project gets a heading, no cap
        self.assertIn("s19", text)


class ContextColumn(unittest.TestCase):
    def test_ctx_tokens_intact_on_each_session_at_100_columns(self) -> None:
        stages = []
        for i, label in enumerate(["alpha", "a-much-longer-project-name", "b"]):
            s = station(f"/c{i}", label, [Worker("w", RUNNING, start=NOW - 60)], last=NOW - 5,
                        lead=LeadInfo("opus", "build"))
            s.state, s.lead_state, s.lead_ctx, s.lead_ctx_window = "active", "live", 46_000, 100_000
            stages.append(s)
        gs = GlobalSnapshot(now=NOW, stages=stages)
        lines = [ln for ln in plain(render_global(gs, UIState(), 100, 40, NOW)) if "46k" in ln]
        self.assertEqual(len(lines), 3)
        starts = [_wd_width(ln[:ln.index("46k")]) for ln in lines]
        self.assertTrue(all(start > 0 for start in starts))
        self.assertTrue(all(len({_wd_width(ln[:ln.index("46k")])}) == 1 for ln in lines))


if __name__ == "__main__":
    unittest.main()
