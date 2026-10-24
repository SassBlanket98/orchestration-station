"""Quips (one phrase per station, by what a worker is doing), the legend, and the phrase file loader."""
from __future__ import annotations

import os
import re
import tempfile
import unittest
from unittest.mock import patch

import render
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    GlobalSnapshot,
    LeadInfo,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import (
    _rd_g_quip,
    _rd_g_quips,
    _rd_g_rowdata,
    _rd_quips_from_markdown,
    render_global,
)
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0
DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "QUIPS.md")


def row(*workers: Worker):
    stage = Stage("/s", "proj", Snapshot(now=NOW, workers=list(workers)), 0, NOW - 5, LeadInfo("opus"))
    return _rd_g_rowdata(stage)


def reset():
    render._rd_QUIPS = None


class Quips(unittest.TestCase):
    def setUp(self) -> None:
        reset()
        self.addCleanup(reset)
        env = patch.dict(os.environ, {"STATUS_QUIPS": "/nonexistent/quips.md"})
        env.start()
        self.addCleanup(env.stop)

    def test_the_worker_who_speaks_is_picked_by_priority(self) -> None:
        run, quiet = Worker("run", RUNNING, start=NOW - 9), Worker("q", QUIET, start=NOW - 99)
        failed, done = Worker("f", FAILED, start=NOW - 9, end=NOW - 1), Worker("d", DONE, start=NOW - 9, end=NOW - 1)
        self.assertEqual(_rd_g_quip(row(done, run, quiet, failed), NOW)[:2], (failed, "failed"))
        self.assertEqual(_rd_g_quip(row(done, run, quiet), NOW)[:2], (quiet, "lagging"))
        self.assertEqual(_rd_g_quip(row(done, run), NOW)[:2], (run, "working"))
        self.assertEqual(_rd_g_quip(row(done), NOW)[:2], (done, "idle"))
        self.assertEqual(_rd_g_quip(row(Worker("n", NOREPORT, start=NOW - 9, end=NOW - 1)), NOW)[1], "idle")
        self.assertIsNone(_rd_g_quip(row(), NOW))

    def test_phrase_is_stable_then_rotates(self) -> None:
        w = Worker("w", RUNNING, start=NOW - 9)
        r = row(w)
        self.assertEqual(_rd_g_quip(r, NOW)[2], _rd_g_quip(r, NOW + 30)[2])
        seen = {_rd_g_quip(r, NOW + k * 90)[2] for k in range(6)}
        self.assertGreater(len(seen), 1)
        self.assertTrue(seen <= set(_rd_g_quips()["working"]))

    def test_markdown_file_replaces_only_the_filled_moments(self) -> None:
        text = "## working\n- alpha\n- \n-\n- beta\n\n### lagging\n<!-- none yet -->\n-\n\n## failed\n* gamma\n\n## other\n- ignored\n"
        self.assertEqual(_rd_quips_from_markdown(text), {"working": ["alpha", "beta"], "failed": ["gamma"]})
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(text)
        self.addCleanup(os.unlink, f.name)
        with patch.dict(os.environ, {"STATUS_QUIPS": f.name}):
            reset()
            pools = _rd_g_quips()
        self.assertEqual(pools["working"], ["alpha", "beta"])
        self.assertEqual(pools["failed"], ["gamma"])
        self.assertEqual(pools["lagging"], render._rd_QUIPS_DEFAULT["lagging"])   # empty bullets: placeholders stay

    def test_json_file_works_and_bad_files_fall_back(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write('{"idle": ["resting"], "working": []}')
        self.addCleanup(os.unlink, f.name)
        with patch.dict(os.environ, {"STATUS_QUIPS": f.name}):
            reset()
            self.assertEqual(_rd_g_quips()["idle"], ["resting"])
            self.assertEqual(_rd_g_quips()["working"], render._rd_QUIPS_DEFAULT["working"])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("not json")
        self.addCleanup(os.unlink, f.name)
        with patch.dict(os.environ, {"STATUS_QUIPS": f.name}):
            reset()
            self.assertEqual(_rd_g_quips(), render._rd_QUIPS_DEFAULT)

    def test_the_shipped_document_is_a_valid_empty_template(self) -> None:
        with open(DOC, encoding="utf-8") as stream:
            self.assertEqual(_rd_quips_from_markdown(stream.read()), {})   # every field empty: defaults stay


def frame(gs: GlobalSnapshot, ui: UIState, cols: int = 110, rows: int = 40) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in render_global(gs, ui, cols, rows, NOW)]


class Display(unittest.TestCase):
    def setUp(self) -> None:
        reset()
        self.addCleanup(reset)
        env = patch.dict(os.environ, {"STATUS_QUIPS": "/nonexistent/quips.md"})
        env.start()
        self.addCleanup(env.stop)
        self.gs = GlobalSnapshot(now=NOW, stages=[Stage(
            "/s", "proj", Snapshot(now=NOW, workers=[Worker("w1", RUNNING, start=NOW - 90)]), 1, NOW - 1, LeadInfo("opus", "build", "stream-x"))])

    def test_overview_prints_no_quip_line(self) -> None:
        shown = "\n".join(frame(self.gs, UIState()))
        self.assertIn("w1", shown)
        self.assertNotRegex(shown, r"w1 “[^”]+”")   # the console's rows carry no quip line

    def test_t_still_toggles_the_setting(self) -> None:
        import app
        ui = UIState()
        self.assertTrue(ui.gquips)
        app._ap_global_key(ui, "t", self.gs, 30)
        self.assertFalse(ui.gquips)
        self.assertNotRegex("\n".join(frame(self.gs, ui)), r"w1 “")

    def test_legend_names_who_is_who_and_i_hides_it(self) -> None:
        shown = "\n".join(frame(self.gs, UIState(glegend=True)))
        for word in ("session", "model", "closed", "no report", "working", "lagging", "idle", "failed"):
            self.assertIn(word, shown)
        hidden = "\n".join(frame(self.gs, UIState()))   # off by default: the overview stays quiet
        self.assertNotIn("solo — lead running", hidden)

    def test_column_rule_has_headings_and_everything_fits(self) -> None:
        rule = next(line for line in frame(self.gs, UIState(glegend=True)) if line.startswith("─"))
        for word in ("session", "model"):
            self.assertIn(word, rule)
        for cols in (40, 57, 80, 130):
            out = render_global(self.gs, UIState(), cols, 40, NOW)
            self.assertTrue(all(len(_wd_SGR_RE.sub("", line)) <= cols for line in out), cols)

    def test_keys_toggle_quips_and_legend(self) -> None:
        import app
        ui = UIState()
        app._ap_global_key(ui, "t", self.gs, 30)
        app._ap_global_key(ui, "i", self.gs, 30)
        self.assertEqual((ui.gquips, ui.glegend), (False, True))
        app._ap_global_key(ui, "t", self.gs, 30)
        self.assertTrue(ui.gquips)

    def test_phone_rows_fit_without_quip_preview(self) -> None:
        out = frame(self.gs, UIState(glegend=False), 57, 30)
        self.assertFalse(any(re.search(r"“[^”]+”", line) for line in out))
        self.assertTrue(all(len(line) <= 57 for line in out))


if __name__ == "__main__":
    unittest.main()
