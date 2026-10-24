from __future__ import annotations

import unittest

import collect
from model import Stage, Turn, TurnTool, UIState, Vitals
from openclawview import (
    OV_KEYS,
    _ov_amount,
    _ov_call_line,
    _ov_key,
    _ov_opens,
    _ov_page,
    _ov_time,
    _ov_turn_content,
    _ov_turns,
)
from widgets import _wd_c, _wd_C_FAINT, _wd_C_GREEN, _wd_C_RED, _wd_SGR_RE, _wd_width


def _plain(line: str) -> str:
    return _wd_SGR_RE.sub("", line)


def _turn(i: int, **kwargs: object) -> Turn:
    values = {"run_id": f"run-{i}", "start": 1_700_000_000 + i * 60,
              "ask": f"Question {i}", "model": "claude-sonnet", "provider": "anthropic",
              "tokens_in": 1200, "tokens_out": 340, "latency": 2.5, "tools": {"exec": 1}}
    values.update(kwargs)
    return Turn(**values)


class BotViewTests(unittest.TestCase):
    def stage(self, turns: list[Turn] | None = None, vitals: Vitals | None = None) -> Stage:
        return Stage(path="/stage#telegram", label="Bot", session_name="telegram", display_name="telegram",
                     tag="OClaw", lead_kind="openclaw", state="active", turns=turns or [], vitals=vitals)

    def test_opens_only_openclaw_stage_markers(self) -> None:
        self.assertTrue(_ov_opens(self.stage()))
        self.assertTrue(_ov_opens(Stage(path="p", label="x", lead_kind="openclaw")))
        self.assertFalse(_ov_opens(Stage(path="p", label="x", tag="CDX", turns=[_turn(1)])))

    def test_page_is_exact_size_with_empty_turns_and_unknown_vitals(self) -> None:
        page = _ov_page(self.stage(), UIState(), 80, 12, 1_700_000_600)
        self.assertEqual(len(page), 12)
        self.assertTrue(all(_wd_width(line) == 80 for line in page))
        self.assertIn("Bot · live", " ".join(page))
        self.assertIn("no turn data", " ".join(page))
        self.assertIn("—", " ".join(page))

    def test_header_uses_the_configured_agent_label(self) -> None:
        with unittest.mock.patch.object(collect, "OPENCLAW_AGENT", "zephyr"):
            page = _ov_page(self.stage(), UIState(), 100, 12, 1_700_000_600)
        header = _plain(page[0])
        self.assertIn("◆ Zephyr ›", header)
        self.assertIn("Zephyr · live", header)

    def test_page_widths_with_turns_at_all_supported_sizes(self) -> None:
        stage = self.stage([_turn(1), _turn(2)], Vitals(model="sonnet", provider="anthropic", ctx_used=100,
                                                        ctx_budget=1000, compactions=2, cost=0.0,
                                                        runtime=12.0, status="running"))
        for cols in (80, 110, 160):
            with self.subTest(cols=cols):
                page = _ov_page(stage, UIState(), cols, 30, 1_700_000_600)
                self.assertEqual(len(page), 30)
                self.assertTrue(all(_wd_width(line) == cols for line in page))
                joined = " ".join(page)
                self.assertIn("$0.00", joined)

    def test_page_is_exact_size_at_every_width_60_to_220(self) -> None:
        turns = [_turn(i, ask="a much longer question that has to be cut somewhere near the end",
                       tools={"exec": 3, "read": 2, "sessions": 1}) for i in range(25)]
        stage = self.stage(turns, Vitals(model="ornith-1.5-9b", provider="llamacpp", ctx_used=38900,
                                         ctx_budget=105000, compactions=2, cost=None, runtime=1131.0,
                                         status="running"))
        for cols in range(60, 221):
            with self.subTest(cols=cols):
                for rows in (12, 40):
                    page = _ov_page(stage, UIState(sel=3), cols, rows, 1_700_002_000)
                    self.assertEqual(len(page), rows)
                    self.assertTrue(all(_wd_width(line) == cols for line in page))

    def test_turn_table_header_and_rows_share_every_column_at_60_to_200(self) -> None:
        turn = _turn(7, start=1_700_003_900, ask="Q7", model="m", tokens_in=1200, tokens_out=40,
                     cost=0.5, latency=2.5, tools={"exec": 1})
        stage = self.stage([turn])
        for cols in range(60, 201):
            with self.subTest(cols=cols):
                page = [_plain(line) for line in _ov_page(stage, UIState(), cols, 20, 1_700_004_000)]
                header = next(line for line in page if "TIME" in line and "TOOLS" in line)
                row = next(line for line in page if line.startswith("▸"))
                self.assertEqual(header.index("TIME"), row.index(_ov_time(turn.start)))
                self.assertEqual(row[header.index("ASK"):header.index("ASK") + 2], "Q7")
                if cols >= 110:
                    columns = (("MODEL", 14, "m"), ("IN", 7, "1.2k"), ("OUT", 7, "40"),
                               ("COST", 8, "$0.50"), ("LAT", 7, "2.5s"))
                else:
                    columns = (("TOK", 8, "1.2k"), ("COST", 9, "$0.50"), ("LAT", 7, "2.5s"))
                for title, width, value in columns:
                    start = header.index(title) + len(title) - width
                    self.assertEqual(header[start:start + width].strip(), title)
                    self.assertEqual(row[start:start + width].strip(), value)
                self.assertTrue(row[header.index("TOOLS"):].startswith("exec×1"))

    def test_tokens_are_compact_not_comma_grouped(self) -> None:
        self.assertEqual(_ov_amount(999), "999")
        self.assertEqual(_ov_amount(20300), "20k")
        self.assertEqual(_ov_amount(1_200_000), "1.2M")
        self.assertEqual(_ov_amount(None), "—")
        page = " ".join(_plain(line) for line in _ov_page(
            self.stage([_turn(1, tokens_in=20300, tokens_out=40),
                        _turn(2, tokens_in=1_200_000, tokens_out=0)]),
            UIState(), 100, 20, 1_700_000_600))
        self.assertIn("20k", page)
        self.assertIn("1.2M", page)
        self.assertNotIn(",", page)

    def test_turns_are_listed_newest_first_whatever_the_input_order(self) -> None:
        middle = _turn(2, start=1_700_000_120, ask="middle turn")
        oldest = _turn(1, start=1_700_000_060, ask="oldest turn")
        newest = _turn(3, start=1_700_000_180, ask="newest turn")
        stage = self.stage([middle, oldest, newest])
        self.assertEqual([turn.run_id for turn in _ov_turns(stage)], ["run-3", "run-2", "run-1"])
        plain = [_plain(line) for line in _ov_page(stage, UIState(), 100, 20, 1_700_000_600)]
        self.assertLess(next(i for i, line in enumerate(plain) if "newest turn" in line),
                        next(i for i, line in enumerate(plain) if "middle turn" in line))
        self.assertLess(next(i for i, line in enumerate(plain) if "middle turn" in line),
                        next(i for i, line in enumerate(plain) if "oldest turn" in line))
        content = _ov_turn_content(stage, UIState(), 100, 1_700_000_600)
        self.assertTrue(_plain(content[2]).startswith("▸"))
        self.assertIn("newest turn", _plain(content[2]))

    def test_tool_call_marks_are_tick_cross_and_faint_dash(self) -> None:
        calls = [TurnTool(ts=1_700_000_060, name="exec", ok=True, dur=0.5, args="echo hello"),
                 TurnTool(ts=1_700_000_061, name="exec", ok=False, dur=1.0, args="false"),
                 TurnTool(ts=1_700_000_062, name="exec", ok=None, args="sleep 1")]
        joined = " ".join(_ov_call_line(call, 100) for call in calls)
        self.assertIn(_wd_c("✔", _wd_C_GREEN), joined)
        self.assertIn(_wd_c("✖", _wd_C_RED), joined)
        self.assertIn(_wd_c("—", _wd_C_FAINT), joined)
        self.assertNotIn("●", joined)
        turn = _turn(1, calls=calls)
        page = " ".join(_ov_page(self.stage([turn]), UIState(gturn=turn.run_id), 100, 24, 1_700_000_600))
        self.assertIn(_wd_c("✔", _wd_C_GREEN), page)

    def test_retry_count_is_singular_for_one(self) -> None:
        for attempts, expected in ((1, "0 retries"), (2, "1 retry"), (3, "2 retries")):
            with self.subTest(attempts=attempts):
                page = " ".join(_ov_page(self.stage([_turn(1, attempts=attempts)]), UIState(), 100, 20,
                                         1_700_000_600))
                self.assertIn(expected, page)

    def test_fold_line_says_older_turns(self) -> None:
        turns = [_turn(i, outcome="ok") for i in range(30)]
        page = [_plain(line) for line in _ov_page(self.stage(turns), UIState(sel=29), 80, 14, 1_700_002_000)]
        self.assertTrue(any("older turns" in line for line in page))
        self.assertFalse(any("earlier turns" in line for line in page))

    def test_unmetered_cost_is_marked_local(self) -> None:
        page = _ov_page(self.stage([_turn(1)], Vitals(model="local", cost=None)), UIState(), 110, 20, 1_700_000_600)
        self.assertIn("— local", " ".join(page))

    def test_selected_turn_opens_calls_and_attempt_details(self) -> None:
        turn = _turn(1, attempts=2, outcome="error", error="rate limited", calls=[
            TurnTool(ts=1_700_000_060, name="exec", ok=False, dur=0.5, args="echo hello")])
        stage = self.stage([turn])
        ui = UIState(sel=0, gturn=turn.run_id)
        page = _ov_page(stage, ui, 80, 24, 1_700_000_600)
        joined = " ".join(page)
        self.assertIn("rate limited", joined)
        self.assertIn("echo hello", joined)
        self.assertIn("exec", joined)
        self.assertIn("↻1", joined)
        self.assertIn("✖", joined)

    def test_navigation_clamps_and_enter_toggles_cursor_turn(self) -> None:
        stage = self.stage([_turn(i) for i in range(30)])
        ui = UIState(sel=40)
        self.assertTrue(_ov_key(ui, stage, "down", 10))
        self.assertEqual(ui.sel, 29)
        self.assertTrue(_ov_key(ui, stage, "home", 10))
        self.assertEqual(ui.sel, 0)
        self.assertTrue(_ov_key(ui, stage, "end", 10))
        self.assertEqual(ui.sel, 29)
        self.assertTrue(_ov_key(ui, stage, "enter", 10))
        self.assertEqual(ui.gturn, "run-0")
        self.assertTrue(_ov_key(ui, stage, "enter", 10))
        self.assertEqual(ui.gturn, "")
        self.assertFalse(_ov_key(ui, stage, "x", 10))

    def test_navigation_uses_newest_first_order(self) -> None:
        middle = _turn(2, start=1_700_000_120)
        stage = self.stage([_turn(1, start=1_700_000_060), middle, _turn(3, start=1_700_000_180)])
        ui = UIState(sel=0)
        self.assertTrue(_ov_key(ui, stage, "enter", 10))
        self.assertEqual(ui.gturn, "run-3")
        self.assertTrue(_ov_key(ui, stage, "down", 10))
        self.assertTrue(_ov_key(ui, stage, "enter", 10))
        self.assertEqual(ui.gturn, "run-2")

    def test_thirty_turns_scroll_with_cursor_and_keep_running_turn_visible(self) -> None:
        turns = [_turn(i, outcome="running" if i == 29 else "ok") for i in range(30)]
        stage = self.stage(turns)
        ui = UIState(sel=29)
        page = _ov_page(stage, ui, 80, 14, 1_700_002_000)
        self.assertEqual(len(page), 14)
        self.assertTrue(all(_wd_width(line) == 80 for line in page))
        self.assertTrue(any("older turns" in line for line in page))
        self.assertTrue(any("Question 0" in line for line in page))

    def test_empty_navigation_and_removed_refresh_key(self) -> None:
        ui = UIState(sel=7)
        self.assertTrue(_ov_key(ui, self.stage(), "down", 10))
        self.assertEqual(ui.sel, 0)
        self.assertFalse(any(key == "r" for key, _ in OV_KEYS))

    def test_selected_turn_stays_in_view_when_header_lines_take_rows(self) -> None:
        stage = self.stage([_turn(i) for i in range(20)])
        page = _ov_page(stage, UIState(sel=6), 80, 12, 1_700_002_000)
        self.assertIn("Question 13", " ".join(_wd_SGR_RE.sub("", line) for line in page))


if __name__ == "__main__":
    unittest.main()
