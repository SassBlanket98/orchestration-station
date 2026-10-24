"""CC strip: the tab bar and the Usage, Events, Logs and Metrics bodies."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ccstrip
from model import (
    CCEvent,
    GlobalSnapshot,
    LogState,
    QuotaAccount,
    QuotaWindow,
    Snapshot,
    Stage,
    Worker,
)
from widgets import (
    _wd_c,
    _wd_C_AMBER,
    _wd_C_BLUE,
    _wd_C_DIM,
    _wd_C_GREEN,
    _wd_C_RED,
    _wd_C_SUB,
    _wd_C_TEXT,
    _wd_pad,
    _wd_SGR_RE,
    _wd_width,
)

NOW = 1_800_000_000.0


def plain(s: str) -> str:
    return _wd_SGR_RE.sub("", s)


def stage(path: str = "/p/alpha", label: str = "alpha", transcript: str = "",
          pulses: list[tuple[float, int]] | None = None, workers: list[Worker] | None = None) -> Stage:
    return Stage(path=path, label=label, transcript_path=transcript, lead_tool_pulses=list(pulses or []),
                 snap=Snapshot(workers=list(workers or [])))


def worker(name: str = "w1", path: str = "", pulses: list[tuple[float, int]] | None = None) -> Worker:
    return Worker(name=name, state="running", log=LogState(path=path, tool_pulses=list(pulses or [])))


def snap(*stages: Stage, quota: list[QuotaAccount] | None = None, quota_ok: bool = False) -> GlobalSnapshot:
    return GlobalSnapshot(now=NOW, stages=list(stages), quota=list(quota or []), quota_ok=quota_ok)


def ev(ts: float, key: str, kind: str = "tool", text: str = "x") -> CCEvent:
    return CCEvent(ts=ts, key=key, kind=kind, text=text)


def fake_tail(path: str, n: int = 200) -> list[str]:
    """The real log_tail contract: the last `n` lines of the file, [] when unreadable."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    return lines[-n:] if n else []


class TabBarTests(unittest.TestCase):
    def test_labels_and_active_marker(self) -> None:
        bar, _spans = ccstrip.strip_tab_bar("Usage", 60)
        self.assertTrue(plain(bar).startswith(" ▸Usage  Events  Logs  Metrics "))
        self.assertEqual(_wd_width(bar), 60)

    def test_active_marker_moves_with_the_tab(self) -> None:
        bar, _ = ccstrip.strip_tab_bar("Logs", 60)
        self.assertTrue(plain(bar).startswith(" Usage  Events  ▸Logs  Metrics "))
        self.assertNotIn("▸Usage", plain(bar))

    def test_active_is_accent_bold_others_dim(self) -> None:
        bar, _ = ccstrip.strip_tab_bar("Metrics", 60)
        self.assertIn(_wd_c("▸Metrics", _wd_C_BLUE, bold=True), bar)
        self.assertIn(_wd_c("▸Events", _wd_C_BLUE, bold=True), ccstrip.strip_tab_bar("Events", 60)[0])
        self.assertIn(_wd_c("Events", _wd_C_DIM), bar)
        self.assertNotIn(_wd_c("Metrics", _wd_C_BLUE, bold=True), bar)

    def test_spans_cover_each_tab_inclusive(self) -> None:
        _bar, spans = ccstrip.strip_tab_bar("Usage", 60)
        self.assertEqual(spans, [(1, 6, "Usage"), (9, 14, "Events"), (17, 20, "Logs"), (23, 29, "Metrics")])

    def test_spans_follow_the_marker(self) -> None:
        _bar, spans = ccstrip.strip_tab_bar("Logs", 60)
        self.assertEqual(spans, [(1, 5, "Usage"), (8, 13, "Events"), (16, 20, "Logs"), (23, 29, "Metrics")])

    def test_spans_are_dropped_and_clamped_when_narrow(self) -> None:
        self.assertEqual(ccstrip.strip_tab_bar("Usage", 12)[1], [(1, 6, "Usage"), (9, 11, "Events")])
        bar, spans = ccstrip.strip_tab_bar("Events", 0)
        self.assertEqual((plain(bar), spans), ("", []))

    def test_four_tabs_fit_at_phone_width(self) -> None:
        bar, spans = ccstrip.strip_tab_bar("Usage", 46)
        self.assertEqual(_wd_width(bar), 46)
        self.assertEqual([name for _a, _b, name in spans], list(ccstrip.STRIP_TABS))


class ShapeTests(unittest.TestCase):
    def test_each_tab_is_exactly_height_by_width(self) -> None:
        gs = snap(stage(), stage("/p/beta", "beta", workers=[worker("w1")]))
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: [ev(NOW - 1, "/p/alpha|w1")]), \
                mock.patch.object(ccstrip, "log_tail", fake_tail):
            for tab in ccstrip.STRIP_TABS:
                for width in (60, 100, 160):
                    for height in (4, 6, 8):
                        lines = ccstrip.strip_lines(gs, tab, gs.stages[0], gs.stages[1].snap.workers[0],
                                                    width, height, 0, NOW)
                        self.assertEqual(len(lines), height, (tab, width, height))
                        for line in lines:
                            self.assertEqual(_wd_width(line), width, (tab, width, height, plain(line)))

    def test_line_zero_is_the_tab_bar(self) -> None:
        for tab in ccstrip.STRIP_TABS:
            lines = ccstrip.strip_lines(snap(), tab, None, None, 80, 5)
            self.assertEqual(lines[0], ccstrip.strip_tab_bar(tab, 80)[0])

    def test_degenerate_heights(self) -> None:
        self.assertEqual(ccstrip.strip_lines(snap(), "Events", None, None, 80, 0), [])
        one = ccstrip.strip_lines(snap(), "Events", None, None, 80, 1)
        self.assertEqual(one, [ccstrip.strip_tab_bar("Events", 80)[0]])

    def test_module_exposes_the_event_sources(self) -> None:
        for name in ("fleet_events", "row_events", "log_tail", "row_key"):
            self.assertTrue(callable(getattr(ccstrip, name)), name)


class EventsTests(unittest.TestCase):
    def spine(self) -> list[CCEvent]:
        return [
            ev(NOW - 30, "/p/alpha|w1", "tool", "first"),
            ev(NOW - 20, "/p/beta|", "msg", "second"),
            ev(NOW - 10, "/p/alpha|w1", "err", "third"),
        ]

    def test_newest_at_the_bottom(self) -> None:
        gs = snap(stage(), stage("/p/beta", "beta"))
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: self.spine()):
            lines = ccstrip.strip_lines(gs, "Events", None, None, 80, 4, 0, NOW)
        self.assertIn("first", plain(lines[1]))
        self.assertIn("second", plain(lines[2]))
        self.assertIn("third", plain(lines[3]))

    def test_few_events_sit_at_the_bottom_under_blank_rows(self) -> None:
        gs = snap(stage())
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: self.spine()[:1]):
            lines = ccstrip.strip_lines(gs, "Events", None, None, 80, 5, 0, NOW)
        self.assertTrue(all(not plain(l).strip() for l in lines[1:4]))
        self.assertIn("first", plain(lines[4]))

    def test_columns_are_fixed_width_and_text_truncates(self) -> None:
        gs = snap(stage())
        events = [ev(NOW - 1, "/p/alpha|w1", "state", "日本語" * 30)]
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: events):
            line = ccstrip.strip_lines(gs, "Events", None, None, 60, 2, 0, NOW)[1]
        text = plain(line)
        self.assertEqual(_wd_width(line), 60)
        self.assertRegex(text[:8], r"^\d\d:\d\d:\d\d$")
        self.assertEqual(text[26:32], "state ")
        self.assertTrue(text[34:].startswith("日本語"))
        self.assertTrue(text.rstrip().endswith("…"))

    def test_cjk_label_and_text_keep_width_at_every_size(self) -> None:
        gs = snap(stage("/p/alpha", "阿尔法阿尔法阿尔法阿尔法"))
        events = [ev(NOW - 1, "/p/alpha|", "msg", "文字" * 80)]
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: events):
            for width in (60, 100, 160):
                line = ccstrip.strip_lines(gs, "Events", None, None, width, 3, 0, NOW)[2]
                self.assertEqual(_wd_width(line), width, width)

    def test_row_labels_from_key_and_stage_label(self) -> None:
        gs = snap(stage(), stage("/p/beta", "beta"))
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: self.spine()):
            lines = ccstrip.strip_lines(gs, "Events", None, None, 80, 4, 0, NOW)
        self.assertIn("w1", plain(lines[1]))
        self.assertIn("beta", plain(lines[2]))

    def test_selected_row_is_brighter(self) -> None:
        gs = snap(stage(), stage("/p/beta", "beta"))
        wk = worker("w1")
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: self.spine()):
            lines = ccstrip.strip_lines(gs, "Events", gs.stages[0], wk, 80, 4, 0, NOW)
        self.assertIn(_wd_c(_wd_pad("w1", 14), _wd_C_TEXT, bold=True), lines[1])
        self.assertIn(_wd_c(_wd_pad("beta", 14), _wd_C_SUB), lines[2])
        self.assertNotIn(_wd_c(_wd_pad("w1", 14), _wd_C_SUB), lines[1])

    def test_err_events_are_red(self) -> None:
        gs = snap(stage(), stage("/p/beta", "beta"))
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: self.spine()):
            lines = ccstrip.strip_lines(gs, "Events", None, None, 80, 4, 0, NOW)
        self.assertIn(_wd_c("err   ", _wd_C_RED), lines[3])
        self.assertIn(_wd_c("third", _wd_C_RED), lines[3])
        self.assertIn(_wd_c("tool  ", _wd_C_DIM), lines[1])
        self.assertIn(_wd_c("second", _wd_C_DIM), lines[2])

    def test_scroll_skips_the_newest(self) -> None:
        events = [ev(NOW - i, "/p/alpha|w1", "tool", t) for i, t in ((50, "a"), (40, "b"), (30, "c"),
                                                                     (20, "d"), (10, "e"))]
        gs = snap(stage())
        with mock.patch.object(ccstrip, "fleet_events", lambda g, limit=500: events):
            top = ccstrip.strip_lines(gs, "Events", None, None, 80, 4, 0, NOW)
            back = ccstrip.strip_lines(gs, "Events", None, None, 80, 4, 2, NOW)
        self.assertEqual([plain(l).split()[-1] for l in top[1:4]], ["c", "d", "e"])
        self.assertEqual([plain(l).split()[-1] for l in back[1:4]], ["a", "b", "c"])

    def test_no_events_is_one_centred_dim_note(self) -> None:
        lines = ccstrip.strip_lines(snap(), "Events", None, None, 60, 4, 0, NOW)
        notes = [l for l in lines[1:] if plain(l).strip()]
        self.assertEqual(len(notes), 1)
        self.assertEqual(plain(notes[0]).strip(), "no events yet")
        self.assertIn(_wd_c("no events yet", _wd_C_DIM), notes[0])
        self.assertEqual(lines[2], notes[0])
        self.assertEqual(plain(notes[0]).index("no"), (60 - len("no events yet")) // 2)


class LogsTests(unittest.TestCase):
    def test_tail_shows_the_newest_at_the_bottom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "w.log"
            path.write_text("\n".join(f"L{i}" for i in range(1, 7)) + "\n")
            st, wk = stage(), worker("w1", path=str(path))
            with mock.patch.object(ccstrip, "log_tail", fake_tail):
                lines = ccstrip.strip_lines(snap(st), "Logs", st, wk, 60, 4, 0, NOW)
        self.assertEqual([plain(l).rstrip() for l in lines[1:4]], ["L4", "L5", "L6"])
        self.assertEqual(len(lines), 4)

    def test_scroll_moves_back_in_the_tail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "w.log"
            path.write_text("\n".join(f"L{i}" for i in range(1, 7)) + "\n")
            st, wk = stage(), worker("w1", path=str(path))
            with mock.patch.object(ccstrip, "log_tail", fake_tail):
                lines = ccstrip.strip_lines(snap(st), "Logs", st, wk, 60, 4, 2, NOW)
        self.assertEqual([plain(l).rstrip() for l in lines[1:4]], ["L2", "L3", "L4"])

    def test_lead_row_reads_the_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lead.jsonl"
            path.write_text("T1\nT2\n")
            st = stage(transcript=str(path))
            with mock.patch.object(ccstrip, "log_tail", fake_tail):
                lines = ccstrip.strip_lines(snap(st), "Logs", st, None, 60, 4, 0, NOW)
        self.assertIn("T2", plain(lines[2]))

    def test_worker_path_wins_over_the_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "lead.jsonl").write_text("TRANSCRIPT\n")
            (Path(td) / "w.log").write_text("WORKERLOG\n")
            st = stage(transcript=str(Path(td) / "lead.jsonl"))
            wk = worker("w1", path=str(Path(td) / "w.log"))
            with mock.patch.object(ccstrip, "log_tail", fake_tail):
                lines = ccstrip.strip_lines(snap(st), "Logs", st, wk, 60, 4, 0, NOW)
        self.assertIn("WORKERLOG", plain(lines[1]))
        self.assertNotIn("TRANSCRIPT", plain("\n".join(lines)))

    def test_no_file_is_a_centred_dim_sentence(self) -> None:
        lines = ccstrip.strip_lines(snap(), "Logs", stage(), worker("w1"), 60, 4, 0, NOW)
        self.assertEqual(plain(lines[2]).strip(), "no log file for this session")
        self.assertIn(_wd_c("no log file for this session", _wd_C_DIM), lines[2])
        self.assertTrue(all(not plain(l).strip() for l in (lines[1], lines[3])))

    def test_no_selection_asks_for_a_worker(self) -> None:
        lines = ccstrip.strip_lines(snap(), "Logs", None, None, 60, 4, 0, NOW)
        self.assertEqual(plain(lines[2]).strip(), "select a worker to see its log")
        self.assertIn(_wd_c("select a worker to see its log", _wd_C_DIM), lines[2])

    def test_empty_file_is_no_log(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "w.log"
            path.write_text("")
            with mock.patch.object(ccstrip, "log_tail", fake_tail):
                lines = ccstrip.strip_lines(snap(), "Logs", None, worker("w1", path=str(path)), 60, 4, 0, NOW)
        self.assertEqual(plain(lines[2]).strip(), "no log file for this session")


class UsageTests(unittest.TestCase):
    def test_quota_lines(self) -> None:
        quota = [
            QuotaAccount(name="claude", windows=[QuotaWindow(name="5h", used=42.0, pace=None),
                                                 QuotaWindow(name="Week", used=80.0, pace=None)]),
            QuotaAccount(name="codex", windows=[QuotaWindow(name="5h", used=95.0, pace=None)]),
        ]
        gs = snap(stage(), quota=quota, quota_ok=True)
        lines = ccstrip.strip_lines(gs, "Usage", None, None, 100, 8, 0, NOW)
        self.assertEqual(len(lines), 8)
        self.assertIn(_wd_c(_wd_pad("claude", 12), _wd_C_TEXT, bold=True), lines[1])
        self.assertIn(_wd_c(" 42%", _wd_C_GREEN), lines[1])
        self.assertIn(_wd_c(" 80%", _wd_C_AMBER), lines[1])
        self.assertIn(_wd_c(_wd_pad("codex", 12), _wd_C_TEXT, bold=True), lines[2])
        self.assertIn(_wd_c(" 95%", _wd_C_RED), lines[2])

    def test_colour_thresholds(self) -> None:
        quota = [QuotaAccount(name="a", windows=[QuotaWindow(name="5h", used=pct, pace=None)])
                 for pct in (74.0, 75.0, 89.0, 90.0)]
        gs = snap(stage(), quota=quota, quota_ok=True)
        lines = ccstrip.strip_lines(gs, "Usage", None, None, 100, 8, 0, NOW)
        colours = [(plain(l).split()[-1], l) for l in lines[1:5]]
        self.assertIn(_wd_c(" 74%", _wd_C_GREEN), colours[0][1])
        self.assertIn(_wd_c(" 75%", _wd_C_AMBER), colours[1][1])
        self.assertIn(_wd_c(" 89%", _wd_C_AMBER), colours[2][1])
        self.assertIn(_wd_c(" 90%", _wd_C_RED), colours[3][1])

    def test_quota_unavailable(self) -> None:
        lines = ccstrip.strip_lines(snap(), "Usage", None, None, 80, 4, 0, NOW)
        self.assertEqual(plain(lines[1]).rstrip(), "quota unavailable")
        self.assertIn(_wd_c("quota unavailable", _wd_C_DIM), lines[1])

    def test_no_accounts_when_ok(self) -> None:
        gs = snap(stage(), quota_ok=True)
        lines = ccstrip.strip_lines(gs, "Usage", None, None, 80, 4, 0, NOW)
        self.assertEqual(plain(lines[1]).rstrip(), "no accounts")

    def test_more_accounts_than_room_are_cut(self) -> None:
        quota = [QuotaAccount(name=f"a{i}", windows=[QuotaWindow(name="5h", used=10.0, pace=None)]) for i in range(6)]
        gs = snap(stage(), quota=quota, quota_ok=True)
        lines = ccstrip.strip_lines(gs, "Usage", None, None, 80, 4, 0, NOW)
        self.assertEqual(len(lines), 4)


class MetricsTests(unittest.TestCase):
    def test_fleet_tools_chart_axis_max_and_unit(self) -> None:
        gs = snap(stage(pulses=[(NOW - 60, 5)]), stage("/p/beta", "beta", workers=[worker("w1", pulses=[(NOW - 120, 3)])]))
        lines = ccstrip.strip_lines(gs, "Metrics", None, None, 100, 4, 0, NOW)
        self.assertTrue(plain(lines[1]).startswith("5 tools/min "))
        self.assertNotIn("idle", plain(lines[1]))
        self.assertNotIn("peak", plain(lines[1]))

    def test_no_pulses_is_a_flat_dim_baseline_and_idle(self) -> None:
        lines = ccstrip.strip_lines(snap(), "Metrics", None, None, 100, 4, 0, NOW)
        self.assertTrue(plain(lines[1]).startswith("0 tools/min "))
        self.assertIn("idle", plain(lines[1]))
        self.assertIn("▁", plain(lines[1]))
        self.assertNotIn("·", plain(lines[1]))
        self.assertIn(_wd_c("idle", _wd_C_DIM), lines[1])

    def test_chart_is_exact_width_at_each_size(self) -> None:
        for width in (60, 100, 160):
            for pulses in ([], [(NOW - 60, 5)]):
                line = ccstrip.strip_lines(snap(stage(pulses=pulses)), "Metrics", None, None, width, 4, 0, NOW)[1]
                self.assertEqual(_wd_width(line), width, (width, bool(pulses)))

    def test_metrics_body_is_only_the_tools_chart(self) -> None:
        quota = [QuotaAccount(name="claude", windows=[QuotaWindow(name="5h", used=42.0, pace=None)])]
        gs = snap(stage(), quota=quota, quota_ok=True)
        lines = ccstrip.strip_lines(gs, "Metrics", None, None, 100, 4, 0, NOW)
        self.assertTrue(plain(lines[1]).startswith("0 tools/min "))
        self.assertNotIn("claude", plain("\n".join(lines[1:])))


class EmptyTests(unittest.TestCase):
    def test_everything_empty_never_raises(self) -> None:
        gs = GlobalSnapshot()
        for tab in ccstrip.STRIP_TABS:
            for height in (4, 6, 8):
                lines = ccstrip.strip_lines(gs, tab, None, None, 60, height, 0, 0.0)
                self.assertEqual(len(lines), height)
                for line in lines:
                    self.assertEqual(_wd_width(line), 60)

    def test_every_empty_tab_is_one_note_at_each_width(self) -> None:
        for tab in ("Usage", "Events", "Logs"):
            for width in (60, 100, 160):
                lines = ccstrip.strip_lines(GlobalSnapshot(), tab, None, None, width, 6, 0, NOW)
                for line in lines:
                    self.assertEqual(_wd_width(line), width, (tab, width))
                self.assertEqual(sum(1 for l in lines[1:] if plain(l).strip()), 1, (tab, width))


if __name__ == "__main__":
    unittest.main()
