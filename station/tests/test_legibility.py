"""Slice F, the look and legibility pass on the overview: labels, state colours, solo vs team, the big title,
the context column, and the footer, `e` and `o` wording and behaviour."""
from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from taskhelp import open_tasks

import app
import widgets
from model import (
    DONE,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import (
    _rd_g_short_labels,
    global_rows,
    render_global,
    render_global_plain,
)
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0
HOME = "/home/d/Projects"


def rgb(color: tuple[int, int, int]) -> str:
    return "38;2;{};{};{}".format(*color)


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


def session(path: str, label: str, workers: list[Worker], *, state: str, name: str = "", tag: str = "CC·W",
            ctx: int | None = None, solo: bool = False, last: float = NOW - 30, lead_state: str = "live",
            unlinked: bool = False, cwd_dir: str = "") -> Stage:
    st = Stage(path, label, Snapshot(now=NOW, workers=workers), sum(w.state == RUNNING for w in workers), last,
               LeadInfo("opus"))
    st.state, st.lead_state, st.solo, st.unlinked = state, lead_state, solo, unlinked
    st.session_name, st.tag, st.lead_model, st.effort, st.lead_ctx = name, tag, "sonnet-5.5", "medium", ctx
    st.dir = cwd_dir
    return st


def line_with(lines: list[str], text: str) -> str:
    return next(ln for ln in lines if text in ln and any(m in ln for m in ("◆", "◇", "◐", "○")))


def colour_of(frame: list[str], text: str, mark: str) -> str:
    """The SGR colour code in front of `mark` on the row that holds `text`."""
    line = next(ln for ln in frame if text in _wd_SGR_RE.sub("", ln) and mark in _wd_SGR_RE.sub("", ln))
    return re.search(r"\x1b\[([0-9;]*)m" + re.escape(mark), line).group(1)


class EnvPatch(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)


class SharedProjectLabels(EnvPatch):
    def test_rows_of_one_directory_share_one_label(self) -> None:
        real = session(f"{HOME}/sample-project", "Projects/sample-project", [], state="active", name="a")
        virtual = session(f"{HOME}/sample-project#unlinked", "sample-project", [], state="closed", unlinked=True,
                          lead_state="gone")
        short = _rd_g_short_labels([real, virtual])
        self.assertEqual(short, {f"{HOME}/sample-project": "sample-project"})

    def test_path_parts_stay_only_for_two_different_directories(self) -> None:
        a = session(f"{HOME}/openclaw", "Projects/openclaw", [], state="active")
        b = session("/mnt/work/openclaw", "work/openclaw", [], state="active")
        short = _rd_g_short_labels([a, b])
        self.assertEqual(short, {f"{HOME}/openclaw": "Projects/openclaw", "/mnt/work/openclaw": "work/openclaw"})

    def test_stage_dir_and_stage_folder_suffix_count_as_the_same_root(self) -> None:
        real = session(f"{HOME}/sample-project/.stage", "Projects/sample-project", [], state="active",
                       cwd_dir=f"{HOME}/sample-project")
        other = session(f"{HOME}/sample-project/.stage#lead", "sample-project", [], state="idle", solo=True)
        self.assertEqual(_rd_g_short_labels([real, other]), {f"{HOME}/sample-project": "sample-project"})

    def test_project_name_is_on_heading_and_sessions_have_own_names(self) -> None:
        root = f"{HOME}/sample-project"
        a = session(root + "#a", "sample-project", [], state="active", name="desk")
        b = session(root + "#b", "sample-project", [], state="active", name="builder")
        gs = GlobalSnapshot(now=NOW, stages=[a, b])
        text = "\n".join(plain(render_global(gs, UIState(), 120, 60, NOW)))
        self.assertEqual(text.count("sample-project"), 1)
        self.assertIn("desk", text)
        self.assertIn("builder", text)


class StateColours(EnvPatch):
    def gs(self) -> GlobalSnapshot:
        hot = session(f"{HOME}/hot", "hot", [Worker("h", RUNNING, start=NOW - 60, quiet_s=2.0)], state="active")
        still = session(f"{HOME}/still", "still", [Worker("d", DONE, start=NOW - 90, end=NOW - 80)], state="active",
                        last=NOW - 40)
        idle = session(f"{HOME}/idle", "waiting", [Worker("i", DONE, start=NOW - 90, end=NOW - 80)], state="idle",
                       last=NOW - 600)
        closed = session(f"{HOME}/shut", "shut", [Worker("c", DONE, start=NOW - 90, end=NOW - 80)], state="closed",
                         lead_state="gone", last=NOW - 3600)
        solo = session(f"{HOME}/lone", "lone", [], state="active", solo=True, name="lone-one")
        return GlobalSnapshot(now=NOW, stages=[hot, still, idle, closed, solo])

    def test_each_state_has_its_own_mark_colour(self) -> None:
        frame = render_global(self.gs(), UIState(gsel=99, gopen_keys={"p:" + HOME + "/idle"}), 120, 38, NOW)
        self.assertEqual(colour_of(frame, "still", "◆"), rgb(widgets._wd_C_GREEN))   # active, nothing running: bright green
        self.assertEqual(colour_of(frame, "waiting", "◐"), rgb(widgets._wd_C_AMBER))
        self.assertEqual(colour_of(frame, "lone-one", "◇"), rgb(widgets._wd_C_TEAL))

    def test_closed_mark_is_faint(self) -> None:
        frame = render_global(self.gs(), UIState(gsel=99, gopen={(f"{HOME}/shut", "stage", ""), ("", "more", f"finished:{HOME}/shut")}), 120, 38, NOW)
        self.assertEqual(colour_of(frame, "shut", "◇"), rgb(widgets._wd_C_FAINT))

    def test_running_active_mark_breathes_between_two_greens(self) -> None:
        gs = self.gs()
        colours = set()
        for k in range(8):
            frame = render_global(gs, UIState(gsel=99), 120, 38, NOW + k * 0.2)
            colours.add(colour_of(frame, "hot", "◆"))
        self.assertGreater(len(colours), 2)
        green = rgb(widgets._wd_C_GREEN)
        self.assertIn(green, colours)   # the brightest point of the breath is the palette green
        self.assertTrue(all(c.startswith("38;2;") for c in colours))


class SoloVersusTeam(EnvPatch):
    def gs(self) -> GlobalSnapshot:
        team = session(f"{HOME}/team", "team", [Worker("t1", RUNNING, start=NOW - 60), Worker("t2", DONE, start=NOW - 90, end=NOW - 50)],
                       state="active", name="team-session")
        solo = session(f"{HOME}/lone", "lone", [], state="active", solo=True, name="lone-one")
        return GlobalSnapshot(now=NOW, stages=[team, solo])

    def test_solo_mark_is_teal_and_row_opens_with_a_chevron(self) -> None:
        frame = render_global(self.gs(), UIState(gsel=99), 120, 38, NOW)
        self.assertEqual(colour_of(frame, "lone-one", "◇"), rgb(widgets._wd_C_TEAL))
        row = line_with(plain(frame), "lone-one")
        self.assertNotIn("live", row)                          # the live count is on the project heading now

    def test_team_row_has_chevron_and_state_without_counts(self) -> None:
        frame = plain(render_global(self.gs(), UIState(gsel=99), 120, 38, NOW))
        row = line_with(frame, "team-session")
        self.assertIn("◆", row)
        self.assertNotIn("1 fin", row)
        self.assertNotIn("0 lag", row)   # a zero count is not shown
        self.assertNotIn("solo", row)

    def test_live_count_is_on_project_heading(self) -> None:
        frame = plain(render_global(self.gs(), UIState(gsel=99), 120, 38, NOW))
        heading = next(line for line in frame if "team" in line and "1 live" in line)
        self.assertIn("1 live", heading)
        self.assertNotIn("1 live", line_with(frame, "team-session"))

    def test_legend_line_explains_team_and_solo(self) -> None:
        shown = plain(render_global(self.gs(), UIState(glegend=True), 120, 40, NOW))
        self.assertTrue(any("◆ team  ◇ solo" in ln for ln in shown))
        self.assertFalse(any("◆ team  ◇ solo" in ln for ln in plain(render_global(self.gs(), UIState(), 120, 40, NOW))))


class TitleRule(EnvPatch):
    def gs(self) -> GlobalSnapshot:
        return GlobalSnapshot(now=NOW, stages=[session(f"{HOME}/a", "alpha", [Worker("w", RUNNING, start=NOW - 9)],
                                                      state="active", name="one")])

    def test_title_has_a_thin_rule_under_it_on_a_big_enough_terminal(self) -> None:
        frame = plain(render_global(self.gs(), UIState(), 120, 50, NOW))
        self.assertIn("S T A T I O N", frame[0])
        self.assertEqual(set(frame[1].strip()), {"▔"})
        self.assertEqual(len(frame[1].rstrip()), len("S T A T I O N"))
        self.assertTrue(frame[2].lstrip().startswith(("1 live",)), frame[2])   # the stats line sits right under

    def test_no_rule_on_a_small_terminal(self) -> None:
        for cols, rows in ((59, 40), (120, 29), (50, 20)):
            frame = plain(render_global(self.gs(), UIState(), cols, rows, NOW))
            self.assertIn("◆ Station", frame[0], (cols, rows))   # short or phone: the title is one line
            self.assertNotIn("▔", "\n".join(frame), (cols, rows))
        frame = plain(render_global(self.gs(), UIState(), 120, 50, NOW))
        self.assertIn("▔", frame[1])

    def test_caret_follows_the_title(self) -> None:
        frame = render_global(self.gs(), UIState(), 120, 38, 0.0)
        self.assertIn("▌", _wd_SGR_RE.sub("", frame[0]))


class ContextColumn(EnvPatch):
    def gs(self, ctx: int | None, **kw) -> GlobalSnapshot:
        st = session(f"{HOME}/a", "alpha", [Worker("w", RUNNING, start=NOW - 9)], state="active", name="one", ctx=ctx, **kw)
        return GlobalSnapshot(now=NOW, stages=[st])

    def cell(self, frame: list[str]) -> str:
        return line_with(plain(frame), "one")

    def test_ctx_sits_on_the_session_row_without_worker_counts(self) -> None:
        gs = self.gs(229_000)
        gs.stages[0].lead_ctx_window = 250_000
        row = self.cell(render_global(gs, UIState(), 120, 38, NOW))
        self.assertIn("229k", row)                               # the CTX column is the token count, shaded by fill
        self.assertNotIn("1 live", row)

    def test_ctx_is_not_repeated_on_the_milestone_line(self) -> None:
        from model import Milestone
        st = self.gs(229_000).stages[0]
        st.lead_ctx_window = 250_000
        st.snap.milestones = [Milestone(NOW - 30, "working")]
        out = "\n".join(plain(render_global(GlobalSnapshot(now=NOW, stages=[st]), UIState(gopen={(st.path, "stage", "")}), 120, 38, NOW)))
        self.assertEqual(out.count("229k"), 1)

    def test_ctx_colour_eases_from_normal_through_amber_to_red(self) -> None:
        normal, amber, red = widgets._wd_C_SUB, widgets._wd_C_AMBER, widgets._wd_C_RED
        self.assertEqual(widgets._wd_ctx_color(None), normal)
        self.assertEqual(widgets._wd_ctx_color(40), normal)
        self.assertEqual(widgets._wd_ctx_color(75), amber)
        self.assertEqual(widgets._wd_ctx_color(92), red)
        self.assertEqual(widgets._wd_ctx_color(100), red)
        self.assertNotIn(widgets._wd_ctx_color(62), (normal, amber))     # the ramp is gradual, not stepped
        self.assertNotIn(widgets._wd_ctx_color(84), (amber, red))

    def test_ctx_cell_is_painted_with_the_ramp_colour(self) -> None:
        for tokens, window, text in ((40_000, 100_000, "40k"), (75_000, 100_000, "75k"), (95_000, 100_000, "95k")):
            gs = self.gs(tokens)
            gs.stages[0].lead_ctx_window = window
            raw = next(ln for ln in render_global(gs, UIState(), 120, 38, NOW) if text in _wd_SGR_RE.sub("", ln))
            self.assertRegex(raw, rf"\x1b\[{rgb(widgets._wd_ctx_color(tokens * 100 // window))}m\s*{text}")

    def test_ctx_without_a_window_shows_tokens_in_the_normal_colour(self) -> None:
        gs = self.gs(180_000)
        raw = next(ln for ln in render_global(gs, UIState(), 120, 38, NOW) if "180k" in _wd_SGR_RE.sub("", ln))
        self.assertRegex(raw, rf"\x1b\[{rgb(widgets._wd_C_SUB)}m\s*180k")

    def test_ctx_threshold_helper_keeps_its_env_overrides(self) -> None:
        from render import _rd_g_ctx_cell
        amber, red = rgb(widgets._wd_C_AMBER), rgb(widgets._wd_C_RED)
        with patch.dict(os.environ, {"STATUS_CTX_WARN": "100", "STATUS_CTX_BAD": "200"}):
            self.assertRegex(_rd_g_ctx_cell(120_000, 9), rf"\x1b\[{amber}m")
            self.assertRegex(_rd_g_ctx_cell(230_000, 9), rf"\x1b\[{red}m")

    def test_bad_env_value_keeps_the_default(self) -> None:
        from render import _rd_g_ctx_cell
        with patch.dict(os.environ, {"STATUS_CTX_WARN": "lots"}):
            self.assertRegex(_rd_g_ctx_cell(160_000, 9), rf"\x1b\[{rgb(widgets._wd_C_AMBER)}m")

    def test_zero_or_negative_env_value_keeps_the_default(self) -> None:
        from render import _rd_g_ctx_cell
        for bad in ("0", "-50", "nan", "inf"):
            with patch.dict(os.environ, {"STATUS_CTX_WARN": bad}):
                self.assertRegex(_rd_g_ctx_cell(160_000, 9), rf"\x1b\[{rgb(widgets._wd_C_AMBER)}m", bad)

    def test_unknown_ctx_is_a_faint_dash(self) -> None:
        frame = render_global(self.gs(None), UIState(), 120, 38, NOW)
        row = next(ln for ln in frame if "one" in _wd_SGR_RE.sub("", ln))
        self.assertIn("—", _wd_SGR_RE.sub("", row))
        self.assertNotIn("0%", _wd_SGR_RE.sub("", row))

    def test_closed_row_shows_its_ctx_when_known(self) -> None:
        st = session(f"{HOME}/c", "gamma", [], state="closed", lead_state="gone", ctx=40_000, last=NOW - 3600)
        st.lead_ctx_window = 100_000
        out = "\n".join(plain(render_global(GlobalSnapshot(now=NOW, stages=[st]), UIState(gopen={("", "more", f"finished:{HOME}/c")}), 120, 38, NOW)))
        self.assertIn("40k", out)

    def test_ctx_column_survives_every_plan_width(self) -> None:
        for cols in (89, 90, 99, 100, 112, 150):
            frame = plain(render_global(self.gs(10_000), UIState(), cols, 30, NOW))
            self.assertIn("CTX", next(line for line in frame if "SESSION" in line))


class FooterAndKeys(EnvPatch):
    def gs(self) -> GlobalSnapshot:
        busy = session(f"{HOME}/busy", "busy", [Worker("x", RUNNING, start=NOW)], state="active", name="busy-one")
        idle = session(f"{HOME}/idle", "idle", [Worker("y", DONE, start=NOW - 90, end=NOW - 80)], state="idle",
                       name="idle-one", last=NOW - 600)
        closed = session(f"{HOME}/shut", "shut", [Worker("z", DONE, start=NOW - 90, end=NOW - 80)], state="closed",
                         name="shut-one", lead_state="gone", last=NOW - 3600)
        older = session(f"{HOME}/old", "old", [], state="closed", name="old-one", lead_state="gone",
                        last=NOW - 3 * 3600)
        return GlobalSnapshot(now=NOW, stages=[busy, idle, closed, older], hidden_idle=3, dismissed=1)

    def test_dismissed_rides_on_the_closed_line_and_the_old_footer_is_gone(self) -> None:
        tree = "\n".join(plain(render_global(self.gs(), UIState(), 120, 38, NOW)))
        self.assertIn("1 closed", tree)
        self.assertIn("1 dismissed", tree)
        self.assertNotIn("idle sessions hidden", tree)
        self.assertNotIn("a shows them", tree)
        self.assertIn("1 dismissed", render_global_plain(self.gs(), 120))

    def test_e_opens_active_and_idle_with_workers_and_leaves_closed_alone(self) -> None:
        gs, ui = self.gs(), UIState(gfolded={"idle", "closed"})
        app._ap_global_key(ui, "e", gs, 40)
        self.assertEqual(ui.gfolded, {"closed"})
        open_tasks(gs, ui)                                                  # idle's finished task is a line until opened
        rows = global_rows(gs, ui)
        self.assertIn((f"{HOME}/busy", "worker", "x"), rows)
        self.assertIn((f"{HOME}/idle", "worker", "y"), rows)
        self.assertNotIn((f"{HOME}/shut", "worker", "z"), rows)
        self.assertIn(("", "more", f"finished:{HOME}/shut"), rows)         # the closed heading is still there, folded
        self.assertNotIn((f"{HOME}/shut", "stage", ""), rows)

    def test_e_does_not_open_a_closed_row_even_when_it_has_workers(self) -> None:
        gs, ui = self.gs(), UIState(gfolded={"closed"})
        app._ap_global_key(ui, "e", gs, 40)
        self.assertNotIn((f"{HOME}/shut", "stage", ""), ui.gopen)
        self.assertNotIn((f"{HOME}/shut", "worker", "z"), global_rows(gs, ui))

    def test_o_on_the_idle_heading_folds_and_unfolds_it(self) -> None:
        gs = self.gs()
        ui = app._ap_launch_ui("full")
        at = global_rows(gs, ui).index(("", "idle", ""))
        ui.gsel = at
        app._ap_global_key(ui, "o", gs, 40)
        self.assertIn("idle", ui.gfolded)
        self.assertEqual(ui.gpage, "")                  # no station page
        ui.gsel = global_rows(gs, ui).index(("", "idle", ""))
        app._ap_global_key(ui, "space", gs, 40)
        self.assertNotIn("idle", ui.gfolded)

    def test_space_on_the_finished_fold_toggles_it_like_enter(self) -> None:
        gs, ui = self.gs(), UIState()
        target = ("", "more", f"finished:{HOME}/shut")
        ui.gsel = global_rows(gs, ui).index(target)
        app._ap_global_key(ui, "space", gs, 40)
        self.assertIn(target, ui.gopen)
        app._ap_global_key(ui, "o", gs, 40)
        self.assertNotIn(target, ui.gopen)

    def test_o_still_opens_a_station_page(self) -> None:
        gs = self.gs()
        ui = UIState(gsel=1)   # row 0 is the harness heading
        app._ap_global_key(ui, "o", gs, 40)
        self.assertEqual((ui.gpage, ui.gstage), ("sess", f"{HOME}/busy"))


if __name__ == "__main__":
    unittest.main()
