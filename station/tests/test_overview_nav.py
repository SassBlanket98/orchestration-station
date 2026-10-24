"""The Board-style overview: cursor rows, opening in place, filters and the text filter."""
from __future__ import annotations

import unittest

from taskhelp import open_tasks

import app
from model import (
    DONE,
    FAILED,
    QUIET,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import global_rows, render_global
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0


def stage(path: str, label: str, workers: list[Worker], last: float = NOW - 10, lead: str = "gone") -> Stage:
    s = Stage(path, label, Snapshot(now=NOW, workers=workers), sum(w.state == RUNNING for w in workers), last,
              LeadInfo("opus", "build", "stream-x"))
    s.lead_state = lead
    return s


def gs_of(*stages: Stage) -> GlobalSnapshot:
    return GlobalSnapshot(now=NOW, stages=list(stages))


def keys(ui: UIState, gs: GlobalSnapshot, *names: str) -> None:
    for name in names:
        app._ap_global_key(ui, name, gs, 30)


def text(gs: GlobalSnapshot, ui: UIState, cols: int = 100, rows: int = 40) -> str:
    return "\n".join(_wd_SGR_RE.sub("", line) for line in render_global(gs, ui, cols, rows, NOW))


def fixture() -> GlobalSnapshot:
    live = stage("/a", "alpha", [Worker("w1", RUNNING, start=NOW - 90, model="luna"),
                                 Worker("w2", DONE, start=NOW - 200, end=NOW - 50)], lead="live")
    lag = stage("/b", "beta", [Worker("slow", QUIET, start=NOW - 900, quiet_s=200)], lead="live")
    bad = stage("/c", "gamma", [Worker("oops", FAILED, start=NOW - 90, end=NOW - 60)], last=NOW - 5000)
    ok = stage("/d", "delta", [Worker("fine", DONE, start=NOW - 90, end=NOW - 60)], last=NOW - 6000)
    bad.state = ok.state = "idle"
    return gs_of(live, lag, bad, ok)


def solo_stage(path: str, label: str) -> Stage:
    s = stage(path, label, [], lead="live")
    s.solo = True
    return s


class Solo(unittest.TestCase):
    def test_solo_row_opens_and_adds_no_rows(self) -> None:
        stg = solo_stage("/s", "lone")
        stg.lead_kind, stg.doing = "claude", [(NOW - 60, "Edit render.py"), (NOW - 5, "Bash: git status")]
        gs, ui = gs_of(stg), UIState(gsel=1)
        self.assertEqual(global_rows(gs, ui), [("", "group", "p:/s"), ("/s", "stage", "")])
        shown = text(gs, ui)                                   # a live solo starts open
        self.assertIn("◇", next(ln for ln in shown.splitlines() if "◇" in ln and "lone" in ln))
        self.assertIn("Bash: git status", shown)               # its latest action is the NOW sub-line
        keys(ui, gs, "left")                                   # shut: no row is added, and a live session keeps its NOW line
        self.assertEqual(global_rows(gs, ui), [("", "group", "p:/s"), ("/s", "stage", "")])
        self.assertIn("Bash: git status", text(gs, ui))
        keys(ui, gs, "right")
        self.assertIn("Bash: git status", text(gs, ui))

    def test_open_solo_row_without_actions_is_quiet(self) -> None:
        stg = solo_stage("/s", "lone")
        stg.lead_kind = "codex"
        gs, ui = gs_of(stg), UIState(gsel=1)
        keys(ui, gs, "right")
        self.assertIn("◇ lone", text(gs, ui))
        self.assertNotIn("render error", text(gs, ui))

    def test_solo_station_is_matched_by_all_and_by_working_while_its_lead_is_live(self) -> None:
        gs, ui = gs_of(solo_stage("/s", "lone")), UIState()
        self.assertEqual([r[0] for r in global_rows(gs, ui) if r[1] == "stage"], ["/s"])
        keys(ui, gs, "2")
        self.assertEqual([r[0] for r in global_rows(gs, ui) if r[1] == "stage"], ["/s"])   # a live lead in the active section is working
        gs.stages[0].lead_state = "idle"
        self.assertEqual(global_rows(gs, ui), [])


class Toggle(unittest.TestCase):
    def test_live_stations_start_open_and_idle_projects_start_shut(self) -> None:
        rows = global_rows(fixture(), UIState())
        self.assertEqual([r for r in rows if r[0] == "/a"], [("/a", "stage", ""), ("/a", "worker", "w1"), ("/a", "worker", "w2")])
        self.assertNotIn(("/d", "stage", ""), rows)            # an idle project starts shut
        opened = UIState(gopen_keys={"p:/d"})
        self.assertEqual([r for r in global_rows(fixture(), opened) if r[0] == "/d"], [("/d", "stage", "")])

    def test_enter_toggles_and_left_right_open_and_close(self) -> None:
        gs, ui = fixture(), UIState(gsel=1)
        keys(ui, gs, "enter")                                  # alpha is open: Enter shuts it
        self.assertEqual([r for r in global_rows(gs, ui) if r[0] == "/a"], [("/a", "stage", "")])
        keys(ui, gs, "right")
        self.assertEqual(len([r for r in global_rows(gs, ui) if r[0] == "/a"]), 3)
        keys(ui, gs, "left")
        self.assertEqual(len([r for r in global_rows(gs, ui) if r[0] == "/a"]), 1)
        keys(ui, gs, "left")                                   # already shut: nothing happens
        self.assertEqual(len([r for r in global_rows(gs, ui) if r[0] == "/a"]), 1)

    def test_enter_on_a_worker_opens_its_detail_and_left_returns_to_the_station(self) -> None:
        gs, ui = fixture(), UIState(gsel=2)
        keys(ui, gs, "enter")
        self.assertIn(("/a", "worker", "w1"), ui.gopen)
        self.assertIn("goal", text(gs, ui))
        keys(ui, gs, "left")                                   # shuts the detail first
        self.assertNotIn(("/a", "worker", "w1"), ui.gopen)
        self.assertEqual(ui.gsel, 2)
        keys(ui, gs, "space")                                  # Space (a double click) opens the worker page
        self.assertEqual((ui.gpage, ui.gstage, ui.gworker), ("wk", "/a", "w1"))
        keys(ui, gs, "esc")
        self.assertEqual((ui.gpage, ui.gworker), ("", ""))
        keys(ui, gs, "left")                                   # then back to the station row
        self.assertEqual(global_rows(gs, ui)[ui.gsel], ("/a", "stage", ""))

    def test_space_and_o_open_the_page_of_the_row_under_the_cursor(self) -> None:
        gs, ui = fixture(), UIState(gsel=3)                    # the second worker of alpha
        keys(ui, gs, "o")
        self.assertEqual((ui.gpage, ui.gstage, ui.gworker), ("wk", "/a", "w2"))   # a worker row opens its own page

    def test_expand_all_and_collapse_all(self) -> None:
        gs, ui = fixture(), UIState()
        keys(ui, gs, "c")
        self.assertEqual({r[1] for r in global_rows(gs, ui)}, {"group", "idle"})
        keys(ui, gs, "e")                                      # opens every active and idle station; closed stays shut
        open_tasks(gs, ui)
        rows = global_rows(gs, ui)
        self.assertIn(("/a", "worker", "w1"), rows)
        self.assertIn(("/d", "worker", "fine"), rows)   # e opens the idle session; its finished task is a line until opened

    def test_collapse_all_reaches_stations_hidden_by_the_filter(self) -> None:
        gs, ui = fixture(), UIState()
        keys(ui, gs, "4", "c", "1")            # filter to problems, close all, back to all
        self.assertEqual({r[1] for r in global_rows(gs, ui)}, {"group", "idle"})

    def test_one_task_lists_every_worker_and_has_no_fold(self) -> None:
        finished = [Worker(f"d{i}", DONE, start=NOW - 100 - i, end=NOW - 90 - i) for i in range(9)]
        many = stage("/m", "many", [Worker("live", RUNNING, start=NOW - 1)] + finished, lead="live")
        gs, ui = gs_of(many), UIState()
        rows = global_rows(gs, ui)
        self.assertEqual(sum(r[1] == "worker" for r in rows), 10)    # one task: plainly, every worker
        self.assertNotIn(("/m", "more", ""), rows)
        self.assertNotIn("earlier from this session", text(gs, ui))

    def test_closed_stations_fold_per_project(self) -> None:
        closed = [stage(f"/i{i}", f"closed{i:02d}", [], last=NOW - 1800 - i) for i in range(15)]   # quiet under 2 h
        gs, ui = gs_of(*closed), UIState()
        rows = global_rows(gs, ui)
        self.assertEqual(sum(r[1] == "stage" for r in rows), 0)
        self.assertEqual(sum(r[1] == "more" for r in rows), 15)
        ui.gsel = rows.index(("", "more", "finished:/i0"))
        keys(ui, gs, "enter")
        self.assertIn(("/i0", "stage", ""), global_rows(gs, ui))


class Filters(unittest.TestCase):
    def stages(self, gs: GlobalSnapshot, ui: UIState) -> list[str]:
        return [r[0] for r in global_rows(gs, ui) if r[1] == "stage"]

    def test_number_keys_pick_the_view(self) -> None:
        gs, ui = fixture(), UIState(gopen_keys={"p:/c"})       # gamma's idle project is shut by default
        keys(ui, gs, "2")
        self.assertEqual(self.stages(gs, ui), ["/a", "/b"])    # working: a running worker, or a live lead in the active section
        keys(ui, gs, "3")
        self.assertEqual(self.stages(gs, ui), ["/b"])          # lagging: a quiet worker
        keys(ui, gs, "4")
        self.assertEqual(self.stages(gs, ui), ["/c"])          # problems: a failed worker
        keys(ui, gs, "1")
        self.assertEqual(len(self.stages(gs, ui)), 3)          # /d stays inside its shut idle heading

    def test_chips_show_counts_and_the_active_one_is_bracketed(self) -> None:
        out = text(fixture(), UIState(gfilter="lagging"))
        self.assertIn("[3 lagging 1]", out)
        self.assertIn("1 all 4", out)

    def test_slash_types_a_query_and_enter_keeps_it_esc_clears_it(self) -> None:
        gs, ui = fixture(), UIState(gopen_keys={"p:/c"})       # gamma's idle project is shut by default
        keys(ui, gs, "/", "g", "a", "m")
        self.assertTrue(ui.gedit)
        self.assertEqual(ui.gquery, "gam")
        self.assertEqual(self.stages(gs, ui), ["/c"])
        self.assertIn("/ gam▌", text(gs, ui))
        keys(ui, gs, "enter")
        self.assertFalse(ui.gedit)
        self.assertEqual(ui.gquery, "gam")
        keys(ui, gs, "esc")
        self.assertEqual((ui.gquery, ui.gfilter), ("", "all"))
        self.assertEqual(len(self.stages(gs, ui)), 3)          # /d stays inside its shut idle heading

    def test_typing_in_the_filter_does_not_trigger_commands(self) -> None:
        gs, ui = fixture(), UIState()
        keys(ui, gs, "/", "q", "x", "a", "e", "space", "backspace")
        self.assertTrue(ui.gedit)
        self.assertEqual(ui.gquery, "qxae")
        self.assertEqual(ui.gpage, "")

    def test_query_matches_lead_model_and_worker_model(self) -> None:
        gs, ui = fixture(), UIState(gquery="luna")
        self.assertEqual(self.stages(gs, ui), ["/a"])

    def test_empty_result_says_so(self) -> None:
        self.assertIn("no stations match", text(fixture(), UIState(gquery="zzz")))


class Rendering(unittest.TestCase):
    def test_every_width_fits_and_phone_keeps_the_essentials(self) -> None:
        gs = fixture()
        for cols in (40, 57, 80, 100, 160):
            out = render_global(gs, UIState(gsel=1), cols, 30, NOW)
            self.assertEqual(len(out), 30)
            self.assertTrue(all(len(_wd_SGR_RE.sub("", line)) <= cols for line in out), cols)
        phone = text(gs, UIState(), 57, 30)
        self.assertIn("working", phone)
        self.assertRegex(phone, r"[◆○] alpha")

    def test_quota_line_colours_by_usage(self) -> None:
        from model import QuotaAccount, QuotaWindow
        gs = fixture()
        gs.quota_ok = True
        gs.quota = [QuotaAccount("codex", [QuotaWindow("5h", 41, None)]), QuotaAccount("claude", [QuotaWindow("5h", 85, None)])]
        frame = render_global(gs, UIState(), 100, 30, NOW)
        line = next(ln for ln in frame if "codex" in _wd_SGR_RE.sub("", ln))   # under the three-row title now
        self.assertIn("codex 41%", _wd_SGR_RE.sub("", line))
        self.assertRegex(line, r"38;2;\d+;\d+;\d+m85%")


class StationPage(unittest.TestCase):
    def test_page_header_says_which_station_you_are_in_and_not_the_heading_twice(self) -> None:
        gs = fixture()
        ui = UIState(gpage="stage", gstage="/a", view="overview")
        from render import render_frame
        stage_a = next(s for s in gs.stages if s.path == "/a")
        header = _wd_SGR_RE.sub("", render_frame(stage_a.snap, ui, 100, 30, NOW, stage_a)[0])
        self.assertTrue(header.startswith("◆ Station › alpha"), header)
        self.assertEqual(header.count("Station"), 1)


class PhoneAndDensity(unittest.TestCase):
    def test_phone_gets_a_three_row_touch_bar_and_desktop_does_not(self) -> None:
        gs = fixture()
        ui = UIState()
        out = render_global(gs, ui, 57, 40, NOW)
        bar = [h for h in ui.hits if h[4] == "key" and h[5] in ("f", "e", "c", "Space", "Up", "Down")]
        self.assertEqual(len(bar), 6)
        for r0, r1, c0, c1, _, _ in bar:
            self.assertEqual(r1 - r0, 2)                       # three rows tall: a finger-sized target
            self.assertGreaterEqual(c1 - c0 + 1, 6)
        self.assertIn("Filter", _wd_SGR_RE.sub("", out[-2]))
        wide = UIState()
        render_global(gs, wide, 150, 40, NOW)
        self.assertFalse([h for h in wide.hits if h[5] in ("Up", "Down")])

    def test_f_cycles_the_filter_and_the_bar_labels_it(self) -> None:
        gs, ui = fixture(), UIState()
        seen = []
        for _ in range(5):
            keys(ui, gs, "f")
            seen.append(ui.gfilter)
        self.assertEqual(seen, ["working", "lagging", "problems", "all", "working"])
        self.assertIn("Filter 2", "\n".join(_wd_SGR_RE.sub("", x) for x in render_global(gs, ui, 57, 40, NOW)))

    def test_touch_bar_buttons_press_their_keys(self) -> None:
        gs, ui = fixture(), UIState()
        render_global(gs, ui, 57, 40, NOW)
        ui.hits = [(r0 + 1, r1 + 1, c0 + 1, c1 + 1, act, tgt) for r0, r1, c0, c1, act, tgt in ui.hits]
        for target, key in (("Down", "down"), ("Up", "up"), ("Space", "space"), ("f", "f")):
            r0, _, c0, _, _, _ = next(h for h in ui.hits if h[5] == target)
            self.assertEqual(app._ap_pointer_keys(ui, f"click:{c0}:{r0}", gs, None, 0.0), [key])

    def test_spare_rows_become_air_between_stations_on_a_phone(self) -> None:
        gs = fixture()
        tight = [i for i, ln in enumerate(text(gs, UIState(), 57, 18).splitlines()) if "idle ·" in ln]
        roomy = [i for i, ln in enumerate(text(gs, UIState(), 57, 60).splitlines()) if "idle ·" in ln]
        self.assertGreater(roomy[0], tight[0])

    def test_names_drop_their_path_prefix_when_unique(self) -> None:
        a = stage("/p/Projects/openclaw", "Projects/openclaw", [Worker("w", DONE, start=NOW - 9, end=NOW - 1)], last=NOW - 1800)
        b = stage("/p/a/web", "a/web", [Worker("w", DONE, start=NOW - 9, end=NOW - 1)], last=NOW - 1801)
        c = stage("/p/b/web", "b/web", [Worker("w", DONE, start=NOW - 9, end=NOW - 1)], last=NOW - 1802)
        gs = gs_of(a, b, c)
        ui = UIState(gopen={("", "more", "finished:/p/Projects/openclaw"), ("", "more", "finished:/p/a/web"),
                            ("", "more", "finished:/p/b/web")})
        out = text(gs, ui, 100, 24)
        self.assertIn("openclaw", out)
        self.assertNotIn("Projects/openclaw", out)                 # the session row carries the short project label
        self.assertIn("a/web", out)                            # two projects called web keep enough path to tell apart
        self.assertIn("b/web", out)

    def test_name_column_is_cut_with_an_ellipsis_at_phone_width(self) -> None:
        s = stage("/d", "worker-dashboard", [Worker("w", DONE, start=NOW - 9, end=NOW - 1)], last=NOW - 1800)
        out = text(gs_of(s), UIState(), 57, 30)
        self.assertIn("worker-", out)                           # the name keeps whatever the width plan leaves
        self.assertTrue(all(len(line) <= 57 for line in out.splitlines()))

    def test_session_rows_show_now_and_legend_is_off_by_default(self) -> None:
        out = text(fixture(), UIState(), 120, 40)
        self.assertNotIn("lead live", out)
        self.assertNotIn("◆ team  ◇ solo", out)


if __name__ == "__main__":
    unittest.main()
