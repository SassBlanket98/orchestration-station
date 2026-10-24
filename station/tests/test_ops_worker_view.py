"""The worker page (R4): layout, metrics, TOOLS/MIN, step table, scrolling and its keys."""
from __future__ import annotations

import unittest

import app
from model import (
    DONE,
    FAILED,
    RUNNING,
    GlobalSnapshot,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import global_rows, render_global
from widgets import _wd_c, _wd_C_FAINT, _wd_SGR_RE, _wd_width
from workerview import _wv_entry_count, _wv_key, _wv_page

NOW = 1_800_000_000.0


def step(i: int, outcome: str = "ok", dur: float | None = 0.5, kind: str = "tool", tool: str = "bash",
         text: str = "") -> Step:
    return Step(NOW - 600 + i * 10, kind, tool, text or f"cmd {i}", dur, outcome)


def worker(state: str = RUNNING, steps: list[Step] | None = None, **kw: object) -> Worker:
    fields: dict[str, object] = {
        "goal": "map the global TUI", "model": "deepseek-v4.1-flash", "effort": "high",
        "account": "opencode-go", "pid": 4242, "job": "build", "start": NOW - 760,
        "log": LogState(harness="opencode", steps=steps if steps is not None else [step(0)],
                        tool_counts={"bash": 84}, tokens_in=340000, tokens_out=48000,
                        ctx_tokens=82000, ctx_window=200000, last_ts=NOW - 5,
                        tool_pulses=[(NOW - 30 * i, 1) for i in range(20)]),
    }
    if state != RUNNING:
        fields["end"] = NOW - 5
    fields.update(kw)
    return Worker("i2-tui", state, **fields)  # type: ignore[arg-type]


def station(workers: list[Worker], path: str = "/p/a", label: str = "sample-project") -> Stage:
    st = Stage(path, label, Snapshot(now=NOW, workers=list(workers)), sum(x.state == RUNNING for x in workers),
               NOW - 10)
    st.lead_state, st.session_name = "live", "monitor ops console"
    return st


def gs_of(*stages: Stage) -> GlobalSnapshot:
    return GlobalSnapshot(now=NOW, stages=list(stages))


def plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def page(wk: Worker, cols: int = 120, rows: int = 24, ui: UIState | None = None,
         st: Stage | None = None) -> list[str]:
    return _wv_page(st or station([wk]), wk, ui or UIState(), cols, rows, NOW)


def text(wk: Worker, cols: int = 120, rows: int = 24, ui: UIState | None = None) -> str:
    return "\n".join(plain(line) for line in page(wk, cols, rows, ui))


class Layout(unittest.TestCase):
    def test_page_is_exactly_rows_by_cols_at_80_120_160(self) -> None:
        for cols in (80, 120, 160):
            for rows in (8, 24, 40):
                with self.subTest(cols=cols, rows=rows):
                    lines = page(worker(), cols, rows)
                    self.assertEqual(len(lines), rows)
                    self.assertTrue(all(_wd_width(line) == cols for line in lines))

    def test_page_is_exact_for_a_short_page_and_a_single_column(self) -> None:
        for cols, rows in ((1, 3), (20, 1), (80, 2)):
            with self.subTest(cols=cols, rows=rows):
                lines = page(worker(), cols, rows)
                self.assertEqual(len(lines), rows)
                self.assertTrue(all(_wd_width(line) == cols for line in lines))

    def test_zero_rows_draw_nothing(self) -> None:
        self.assertEqual(page(worker(), 80, 0), [])

    def test_crumb_identity_and_metrics(self) -> None:
        shown = text(worker())
        crumb = shown.splitlines()[0]
        self.assertIn("sample-project", crumb)
        self.assertIn("monitor ops console", crumb)
        self.assertIn("i2-tui", crumb)
        self.assertIn("WORKER", crumb)
        self.assertIn("running", crumb)
        for part in ("MODEL DeepSeek V4.1 Flash · hi", "ROUTE opencode-go", "MODE ", "PID 4242", "JOB build",
                     "ELAPSED 12m 40s", "TOOLS 84", "TOKENS 388k", "CTX 41%", "COST — sub", "FLAGS none",
                     "GOAL map the global TUI"):
            self.assertIn(part, shown)

    def test_unknown_values_are_faint_dashes_never_zero(self) -> None:
        wk = worker(model="", effort="", account="", pid=None, job="", goal="", log=LogState())
        lines = page(wk)
        shown = "\n".join(plain(line) for line in lines)
        for label in ("MODEL", "ROUTE", "MODE", "PID", "JOB", "TOOLS", "TOKENS", "CTX", "GOAL"):
            with self.subTest(label=label):
                self.assertIn(label + " —", shown)
        self.assertIn("COST — sub", shown)
        self.assertIn(_wd_c("—", _wd_C_FAINT), "\n".join(lines))
        self.assertNotIn("$0.00", shown)

    def test_metered_cost_shows_money_and_a_local_one_reads_local(self) -> None:
        self.assertIn("COST $1.23", text(worker(log=LogState(steps=[step(0)], cost=1.234))))
        self.assertIn("COST — local", text(worker(account="ollama")))
        self.assertIn("COST $0.000", text(worker(log=LogState(steps=[step(0)], cost=0.0))))

    def test_flags_are_listed(self) -> None:
        shown = text(worker(flags=frozenset({"no log", "stdin hang"})))
        self.assertIn("FLAGS no log, stdin hang", shown)

    def test_finished_workers_state_and_mark(self) -> None:
        for state, label in ((DONE, "done"), (FAILED, "failed")):
            with self.subTest(state=state):
                crumb = text(worker(state), 160).splitlines()[0]
                self.assertIn(label, crumb)
        self.assertIn("✔", text(worker(DONE), 160).splitlines()[0])
        self.assertIn("✖", text(worker(FAILED, failure="boom"), 160).splitlines()[0])

    def test_tools_per_minute_spark_follows_width(self) -> None:
        for cols, cells in ((40, 20), (80, 60), (120, 60), (160, 60)):
            with self.subTest(cols=cols):
                line = next(x for x in page(worker(), cols) if plain(x).startswith("TOOLS/MIN"))
                shown = plain(line).rstrip()
                self.assertTrue(shown.endswith("30m"), shown)
                self.assertEqual(len(shown) - len("TOOLS/MIN ") - len("  30m"), cells)

    def test_step_header_and_steps_run_oldest_to_newest(self) -> None:
        steps = [step(i) for i in range(6)]
        shown = text(worker(steps=steps), 120, 30)
        header = next(line for line in shown.splitlines() if "TIME" in line and "STEP" in line)
        self.assertIn("TOOL", header)
        self.assertIn("DUR", header)
        at = [shown.index(f"cmd {i}") for i in range(6)]
        self.assertEqual(at, sorted(at))

    def test_failed_step_gets_a_why_line_right_below_it(self) -> None:
        wk = worker(FAILED, failure="exit 1 · ModuleNotFoundError: No module named 'collect'",
                    steps=[step(0), step(1, "failed", 65.0), step(2)])
        shown = text(wk, 120, 30)
        lines = shown.splitlines()
        row = next(i for i, line in enumerate(lines) if "cmd 1" in line)
        self.assertIn("why exit 1 · ModuleNotFoundError", lines[row + 1])
        self.assertNotIn("why", lines[row])

    def test_failed_step_without_a_reason_gets_a_faint_dash(self) -> None:
        wk = worker(steps=[step(0, "failed")])
        lines = page(wk)
        row = next(i for i, line in enumerate(lines) if "cmd 0" in plain(line))
        self.assertIn("why ", lines[row + 1])
        self.assertIn(_wd_c("—", _wd_C_FAINT), lines[row + 1])

    def test_no_steps_says_so(self) -> None:
        self.assertIn("no steps recorded", text(worker(steps=[])))

    def test_newest_step_is_at_the_bottom_of_the_table(self) -> None:
        shown = text(worker(steps=[step(i) for i in range(3)]), 120, 30)
        lines = [line for line in shown.splitlines() if "cmd " in line]
        self.assertTrue(lines[-1].strip().endswith("cmd 2"), lines[-1])


class Scrolling(unittest.TestCase):
    def visible(self, wk: Worker, ui: UIState, rows: int = 20) -> list[int]:
        shown = "\n".join(plain(line) for line in page(wk, 120, rows, ui))
        return [i for i in range(40) if f"cmd {i}" in shown]

    def test_scroll_zero_shows_the_newest_and_home_the_oldest(self) -> None:
        wk, ui = worker(steps=[step(i) for i in range(40)]), UIState()
        near = self.visible(wk, ui)
        self.assertIn(39, near)
        self.assertNotIn(0, near)
        _wv_key(ui, station([wk]), wk, "home", 20)
        far = self.visible(wk, ui)
        self.assertIn(0, far)
        self.assertNotIn(39, far)
        self.assertEqual(near, [i for i in range(40) if i in near])

    def test_scroll_clamps_at_both_ends(self) -> None:
        wk, ui = worker(steps=[step(i) for i in range(40)]), UIState()
        _wv_key(ui, station([wk]), wk, "home", 20)
        at_top = self.visible(wk, ui)
        for _ in range(20):
            _wv_key(ui, station([wk]), wk, "up", 20)
            page(wk, 120, 20, ui)
        self.assertEqual(self.visible(wk, ui), at_top)
        _wv_key(ui, station([wk]), wk, "end", 20)
        for _ in range(20):
            _wv_key(ui, station([wk]), wk, "down", 20)
            page(wk, 120, 20, ui)
        self.assertEqual(ui.scroll, 0)
        self.assertIn(39, self.visible(wk, ui))

    def test_page_clamps_a_stale_offset(self) -> None:
        wk, ui = worker(steps=[step(i) for i in range(5)]), UIState(scroll=9999)
        page(wk, 120, 30, ui)
        self.assertLessEqual(ui.scroll, _wv_entry_count(wk))
        self.assertIn(0, [i for i in range(5) if f"cmd {i}" in text(wk, 120, 30, ui)])

    def test_key_scroll_steps(self) -> None:
        wk = worker(steps=[step(i) for i in range(40)])
        st = station([wk])
        ui = UIState()
        self.assertEqual(_wv_key(ui, st, wk, "up", 20), "")
        self.assertEqual(ui.scroll, 1)
        self.assertEqual(_wv_key(ui, st, wk, "down", 20), "")
        self.assertEqual(ui.scroll, 0)
        _wv_key(ui, st, wk, "down", 20)
        self.assertEqual(ui.scroll, 0)                      # never below the newest
        _wv_key(ui, st, wk, "pgup", 20)
        self.assertEqual(ui.scroll, 12)                     # rows - 8
        _wv_key(ui, st, wk, "pgdn", 20)
        self.assertEqual(ui.scroll, 0)
        _wv_key(ui, st, wk, "k", 20)
        _wv_key(ui, st, wk, "j", 20)
        self.assertEqual(ui.scroll, 0)
        _wv_key(ui, st, wk, "home", 20)
        self.assertEqual(ui.scroll, _wv_entry_count(wk))
        _wv_key(ui, st, wk, "end", 20)
        self.assertEqual(ui.scroll, 0)


class Actions(unittest.TestCase):
    def test_key_returns_brief_report_and_files(self) -> None:
        wk = worker()
        st = station([wk])
        ui = UIState()
        self.assertEqual(_wv_key(ui, st, wk, "b", 20), "brief")
        self.assertEqual(_wv_key(ui, st, wk, "r", 20), "report")
        self.assertEqual(_wv_key(ui, st, wk, "f", 20), "files")
        self.assertEqual(_wv_key(ui, st, wk, "z", 20), "")
        self.assertEqual(ui.scroll, 0)

    def test_brief_and_report_open_the_station_detail_on_that_worker(self) -> None:
        wk, other = worker(), Worker("other", DONE, start=NOW - 20, end=NOW - 5)
        st = station([other, wk])
        gs = gs_of(st)
        for action, tab in (("brief", "brief"), ("report", "report")):
            with self.subTest(action=action):
                ui = UIState(gpage="wk", gstage=st.path, gworker=wk.name)
                self.assertEqual(app._ap_wk_action(ui, gs, st, wk, action), "load_detail")
                self.assertEqual((ui.gpage, ui.gstage, ui.gworker), ("stage", st.path, ""))
                self.assertEqual((ui.view, ui.tab), ("detail", tab))
                self.assertEqual(ui.sel, 1)

    def test_files_opens_the_station_files_page(self) -> None:
        wk = worker()
        st = station([wk])
        ui = UIState(gpage="wk", gstage=st.path, gworker=wk.name)
        self.assertEqual(app._ap_wk_action(ui, gs_of(st), st, wk, "files"), "load_page")
        self.assertEqual((ui.gpage, ui.gstage, ui.view), ("stage", st.path, "files"))

    def test_an_ignored_action_changes_nothing(self) -> None:
        wk = worker()
        st = station([wk])
        ui = UIState(gpage="wk")
        self.assertEqual(app._ap_wk_action(ui, gs_of(st), st, wk, ""), "")
        self.assertEqual(ui.gpage, "wk")


class Routing(unittest.TestCase):
    def fixture(self) -> tuple[GlobalSnapshot, Stage]:
        st = station([worker()])
        return gs_of(st), st

    def gsel_worker(self, gs: GlobalSnapshot, ui: UIState, name: str = "i2-tui") -> None:
        ui.gsel = global_rows(gs, ui).index(("/p/a", "worker", name))

    def test_enter_on_a_worker_opens_the_page_and_esc_returns(self) -> None:
        gs, _ = self.fixture()
        ui = UIState()
        self.gsel_worker(gs, ui)
        app._ap_global_key(ui, "space", gs, 30)
        self.assertEqual((ui.gpage, ui.gstage, ui.gworker), ("wk", "/p/a", "i2-tui"))
        shown = "\n".join(plain(line) for line in render_global(gs, ui, 100, 30, NOW))
        self.assertIn("GOAL", shown)
        self.assertIn("TOOLS/MIN", shown)
        app._ap_global_key(ui, "esc", gs, 30)
        self.assertEqual((ui.gpage, ui.gworker), ("", ""))
        self.assertEqual(global_rows(gs, ui)[ui.gsel], ("/p/a", "worker", "i2-tui"))

    def test_right_still_opens_the_inline_detail(self) -> None:
        gs, _ = self.fixture()
        ui = UIState()
        self.gsel_worker(gs, ui)
        app._ap_global_key(ui, "right", gs, 30)
        self.assertEqual(ui.gpage, "")
        self.assertIn(("/p/a", "worker", "i2-tui"), ui.gopen)

    def test_enter_on_another_row_kind_still_toggles(self) -> None:
        gs, _ = self.fixture()
        ui = UIState()
        ui.gsel = global_rows(gs, ui).index(("/p/a", "stage", ""))
        app._ap_global_key(ui, "enter", gs, 30)
        self.assertEqual(ui.gpage, "")
        self.assertIn("s:/p/a", ui.gshut_keys)

    def test_b_on_the_page_opens_the_brief(self) -> None:
        gs, st = self.fixture()
        ui = UIState(gpage="wk", gstage=st.path, gworker="i2-tui")
        app._ap_global_key(ui, "b", gs, 30)
        self.assertEqual((ui.gpage, ui.view, ui.tab), ("stage", "detail", "brief"))
        self.assertEqual(ui.sel, 0)


if __name__ == "__main__":
    unittest.main()
