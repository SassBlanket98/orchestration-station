"""s5-insights: the now-line, ETA, the header attention count and the review outcome."""
from __future__ import annotations

import unittest

import app
from model import (
    DONE,
    FAILED,
    NOREPORT,
    RUNNING,
    STALLED,
    Finding,
    GlobalSnapshot,
    LogState,
    Review,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
)
from render import (
    _rd_attention_count,
    _rd_attention_first,
    _rd_card,
    _rd_card_height,
    _rd_eta,
    _rd_eta_label,
    _rd_now_row,
    _rd_now_words,
    _rd_review_bars,
    render_frame,
    render_global,
)
from widgets import _wd_c, _wd_C_AMBER, _wd_C_GREEN, _wd_C_RED, _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0
SGR = _wd_SGR_RE


def plain(s: str) -> str:
    return SGR.sub("", s)


def live(name: str, steps: list[Step] | None = None, **kw) -> Worker:
    log = LogState(steps=steps or [])
    return Worker(name, kw.pop("state", RUNNING), log=log, **kw)


def fixture() -> Snapshot:
    """1 hot live worker, 1 empty-log live worker, 1 failed, 4 done (luna high: 200-420 s), 2 reviews, one with a P1."""
    hot = Worker("build-cards", RUNNING, title="Build the cards panel", goal="redesign the overview",
                 model="luna high", family="codex", start=NOW - 264, cpu=84.0, rss=512 * 2 ** 20, nproc=3,
                 activity=[0, 1, 2, 3] * 15, quiet_s=2.0,
                 log=LogState(harness="codex", tokens_in=210_000, tokens_out=18_000, cost=0.021, errors=1,
                              tool_counts={"read": 12, "edit": 7, "shell": 5},
                              steps=[Step(NOW - 30, "tool", "read", "src/render.py"),
                                     Step(NOW - 20, "text", "", "Reading the card layout first."),
                                     Step(NOW - 12, "tool", "edit", "src/widgets.py"),
                                     Step(NOW - 2, "tool", "bash", "pnpm test -- tests/test_render.py")]))
    empty = Worker("docs-sweep", RUNNING, model="claude-sonnet", family="claude", start=NOW - 40, cpu=3.0,
                   activity=[0] * 60, quiet_s=40.0, log=LogState(harness="claude"))
    failed = Worker("api-migrate", FAILED, model="luna high", family="codex", start=NOW - 900, end=NOW - 600,
                    activity=[0] * 60,
                    log=LogState(errors=1, steps=[Step(NOW - 600, "error", "", "migration 0042 failed")]))
    done = [Worker(name, DONE, model="luna high", family="codex", start=NOW - 3000 - dur, end=NOW - 3000,
                   activity=[0] * 60, summary=[f"{name} finished"], has_report=True,
                   log=LogState(tokens_in=50_000, tokens_out=5_000, cost=0.01))
            for name, dur in (("lint-fix", 200), ("docs-index", 240), ("schema-check", 300), ("copy-pass", 420))]
    reviews = [
        Review("review-final-plan", "luna high", "codex", NOW - 900, NOW - 300, False,
               [Finding(1, "reserve not independent"), Finding(2, "stale cache key"), Finding(2, "missing test")]),
        Review("review-plan", "luna high", "codex", NOW - 1500, NOW - 1300, False, []),
    ]
    return Snapshot(title="sample-project", now=NOW, session_start=NOW - 4000,
                    workers=[hot, empty, failed] + done, reviews=reviews, tokens_total=900_000, cost_total=0.12)


class NowLine(unittest.TestCase):
    def words(self, step: Step | None, **kw) -> str:
        steps = [step] if step else []
        return _rd_now_words(live("w", steps, **kw), 80)

    def test_tool_steps_say_verb_and_target(self) -> None:
        self.assertEqual(self.words(Step(NOW, "tool", "read", "src/render.py")), "reading src/render.py")
        self.assertEqual(self.words(Step(NOW, "tool", "edit", "src/picker/gates.ts")), "editing src/picker/gates.ts")
        self.assertEqual(self.words(Step(NOW, "tool", "bash", "pnpm test")), "running pnpm test")
        self.assertEqual(self.words(Step(NOW, "tool", "grep", "now")), "searching now")
        self.assertEqual(self.words(Step(NOW, "tool", "todowrite", "")), "updating its to-do list")

    def test_text_and_error_steps(self) -> None:
        self.assertEqual(self.words(Step(NOW, "text", "", "Let me think about it.")), "thinking")
        self.assertEqual(self.words(Step(NOW, "error", "", "migration failed")), "error: migration failed")

    def test_empty_log_says_output_arrives_later(self) -> None:
        self.assertEqual(_rd_now_words(live("w"), 80), "working (output arrives when it finishes)")
        wk = live("w")
        wk.log.harness = "claude"
        wk.log.offset = 12   # bytes read but no step yet: working, not the buffered-output note
        self.assertEqual(_rd_now_words(wk, 80), "working")

    def test_long_path_keeps_its_file_name_and_fits(self) -> None:
        text = "/home/user/Projects/some-very-long-project-name/src/components/deep/render.py"
        words = _rd_now_words(live("w", [Step(NOW, "tool", "edit", text)]), 30)
        self.assertTrue(words.startswith("editing …"), words)
        self.assertTrue(words.endswith("render.py"), words)
        self.assertLessEqual(_wd_width(words), 30)

    def test_now_words_unwrap_shell_and_tidy_paths(self) -> None:
        import os

        from render import _rd_now_words
        cmd = live("w", [Step(NOW, "tool", "shell", '/usr/bin/bash -lc "pnpm test src/api"')])
        self.assertEqual(_rd_now_words(cmd, 80), "running pnpm test src/api")
        home = os.path.expanduser("~")
        read = live("w", [Step(NOW, "tool", "read", home + "/Projects/demo/src/state/sessions.ts")])
        self.assertEqual(_rd_now_words(read, 80), "reading ~/Projects/demo/src/state/sessions.ts")
        self.assertEqual(_rd_now_words(read, 30), "reading …/state/sessions.ts")
        cut = live("w", [Step(NOW, "tool", "shell", '/usr/bin/bash -lc "adb shell uiautomator dump')])
        self.assertEqual(_rd_now_words(cut, 80), "running adb shell uiautomator dump")
        bare = live("w", [Step(NOW, "tool", "webfetch", "webfetch")])
        self.assertEqual(_rd_now_words(bare, 80), "fetching a web page")
        grep = live("w", [Step(NOW, "tool", "grep", home + "/Projects/x")])
        self.assertEqual(_rd_now_words(grep, 80), "searching ~/Projects/x")

    def test_output_age_appears_once_on_the_card(self) -> None:
        wk = live("w", [Step(NOW, "tool", "edit", "src/picker/gates.ts")], quiet_s=2.0)
        self.assertEqual(plain(_rd_now_row(wk, 80)), "now  editing src/picker/gates.ts")
        card = " ".join(plain(x) for x in _rd_card(fixture().workers[0], 1, 80, NOW, NOW, True, None))
        self.assertEqual(card.count("last out"), 1, card)

    def test_live_card_has_the_now_line_except_at_the_smallest_level(self) -> None:
        wk = fixture().workers[0]
        for lvl in range(1, 6):
            card = [plain(x) for x in _rd_card(wk, lvl, 60, NOW, NOW, True, None)]
            self.assertTrue(card[1].lstrip("▏▊▍▎ ").startswith("now  "), (lvl, card[1]))
            self.assertEqual(len(card), _rd_card_height(wk, lvl), lvl)
        smallest = [plain(x) for x in _rd_card(wk, 6, 60, NOW, NOW, True, None)]
        self.assertFalse(any("now  " in x for x in smallest), smallest)


class Eta(unittest.TestCase):
    def samples(self, start: float, model: str = "m", durations=(200, 240, 300, 420)) -> Snapshot:
        done = [Worker(f"d{i}", DONE, model=model, start=NOW - 5000, end=NOW - 5000 + d, activity=[0] * 60)
                for i, d in enumerate(durations)]
        return Snapshot(now=NOW, workers=done + [live("w", model=model, start=start, state=RUNNING)])

    def test_remaining_is_eta_minus_elapsed(self) -> None:
        snap = self.samples(start=NOW - 100)     # median 270 s, elapsed 100 s
        wk = snap.workers[-1]
        self.assertEqual(plain(_rd_eta_label(snap, wk, NOW)), "eta 3m")
        snap = self.samples(start=NOW - 264)     # 6 s left
        self.assertEqual(plain(_rd_eta_label(snap, snap.workers[-1], NOW)), "eta 6s")

    def test_past_the_eta_is_over_by_in_amber(self) -> None:
        snap = self.samples(start=NOW - 330)     # 60 s over
        label = _rd_eta_label(snap, snap.workers[-1], NOW)
        self.assertEqual(plain(label), "over by 1m")
        self.assertEqual(label, _wd_c("over by 1m", _wd_C_AMBER))

    def test_no_estimate_shows_nothing(self) -> None:
        few = self.samples(start=NOW - 100, durations=(200, 240))       # fewer than 3 finished
        self.assertIsNone(_rd_eta(few, few.workers[-1]))
        self.assertEqual(_rd_eta_label(few, few.workers[-1], NOW), "")
        snap = self.samples(start=0.0)                                  # no start time: no honest elapsed
        self.assertEqual(_rd_eta_label(snap, snap.workers[-1], NOW), "")
        done = Worker("d", DONE, model="m", start=NOW - 100, end=NOW)
        self.assertEqual(_rd_eta_label(Snapshot(now=NOW, workers=[done]), done, NOW), "")

    def test_same_model_wins_when_it_has_three_samples(self) -> None:
        snap = self.samples(start=NOW - 100)
        other = Worker("o", DONE, model="x", start=NOW - 9000, end=NOW - 8000, activity=[0] * 60)
        snap.workers.insert(0, other)
        self.assertEqual(_rd_eta(snap, snap.workers[-1]), 270)

    def test_matches_the_notifier_rule_in_app(self) -> None:
        cases = [fixture(), self.samples(start=NOW - 100), self.samples(start=NOW - 100, durations=(5, 9))]
        for snap in cases:
            for wk in snap.workers:
                self.assertEqual(_rd_eta(snap, wk), app._ap_eta(snap, wk), wk.name)

    def test_overview_has_no_eta_column_and_the_helper_still_computes_it(self) -> None:
        snap = fixture()
        gs = GlobalSnapshot(now=NOW, stages=[Stage("/s/.stage", "sample-project", snap, 2, NOW)])
        text = "\n".join(plain(x) for x in render_global(gs, UIState(), 100, 34, NOW))
        self.assertNotIn("eta ", text)       # the console's metric columns carry no ETA now
        self.assertIn("eta 4m", plain(_rd_eta_label(snap, snap.workers[1], NOW)))
        self.assertNotRegex(text, r"~\d+%")


class Attention(unittest.TestCase):
    def test_count_is_failed_stalled_plus_p1_reviews_and_no_report_is_neutral(self) -> None:
        snap = fixture()
        self.assertEqual(_rd_attention_count(snap), 2)     # api-migrate failed, review-final-plan has a P1
        snap.workers.append(Worker("n", NOREPORT, start=NOW - 9, end=NOW))     # finished, no report: not a problem
        self.assertEqual(_rd_attention_count(snap), 2)
        snap.workers.append(Worker("s", STALLED, start=NOW - 9, quiet_s=400.0))
        snap.reviews.append(Review("r2", "m", "codex", NOW, NOW, False, [Finding(1, "x"), Finding(3, "y")]))
        self.assertEqual(_rd_attention_count(snap), 4)

    def test_header_shows_the_count_and_hides_it_at_zero(self) -> None:
        text = plain(render_frame(fixture(), UIState(), 100, 34, NOW)[0])
        self.assertIn("⚠ 2", text)
        calm = Snapshot(now=NOW, workers=[Worker("d", DONE, start=NOW - 9, end=NOW)])
        self.assertNotIn("⚠", plain(render_frame(calm, UIState(), 100, 34, NOW)[0]))

    def test_header_count_is_a_key_hit_on_the_count(self) -> None:
        ui = UIState()
        frame = render_frame(fixture(), ui, 100, 34, NOW)
        col = _wd_width(plain(frame[0]).split("⚠")[0])
        hits = [h for h in ui.hits if h[4] == "key" and h[5] == "!"]
        self.assertEqual(hits, [(1, 1, col + 1, col + 3, "key", "!")])   # the hit map is 1-based once published

    def test_bang_selects_the_first_attention_worker_then_the_p1_review(self) -> None:
        snap = fixture()
        self.assertEqual(_rd_attention_first(snap), ("worker", 2))
        ui = UIState(sel=0, expanded={"build-cards"})
        app._ap_handle_key(ui, "!", snap, 34)
        self.assertEqual((ui.sel, ui.panel, ui.expanded), (2, "", {"build-cards"}))   # "!" moves the cursor only
        snap.workers = [w for w in snap.workers if w.state not in (FAILED, NOREPORT, STALLED)]
        self.assertEqual(_rd_attention_first(snap), ("reviews", -1))
        ui = UIState()
        app._ap_handle_key(ui, "!", snap, 34)
        self.assertEqual(ui.panel, "reviews")

    def test_bang_does_nothing_with_nothing_to_show(self) -> None:
        snap = Snapshot(now=NOW, workers=[Worker("d", DONE, start=NOW - 9, end=NOW)])
        ui = UIState(sel=0)
        app._ap_handle_key(ui, "!", snap, 34)
        self.assertEqual((ui.sel, ui.panel), (0, ""))


class ReviewOutcome(unittest.TestCase):
    def review(self, findings: list[Finding], running: bool = False) -> Review:
        return Review("r", "m", "codex", NOW - 60, NOW, running, findings)

    def test_clean_is_green(self) -> None:
        self.assertEqual(_rd_review_bars(self.review([])), _wd_c("clean", _wd_C_GREEN))

    def test_counts_read_p1_p2_p3_with_p1_red_and_p2_amber(self) -> None:
        bars = _rd_review_bars(self.review([Finding(1, "a"), Finding(2, "b"), Finding(2, "c"), Finding(3, "d")]))
        self.assertEqual(plain(bars), "P1 1  P2 2  P3 1")
        self.assertIn(_wd_c("P1 1", _wd_C_RED), bars)
        self.assertIn(_wd_c("P2 2", _wd_C_AMBER), bars)

    def test_running_review_without_findings_shows_no_outcome_yet(self) -> None:
        self.assertEqual(_rd_review_bars(self.review([], running=True)), "")
        self.assertEqual(plain(_rd_review_bars(self.review([Finding(1, "a")], running=True))), "P1 1  P2 0  P3 0")


class Frames(unittest.TestCase):
    def test_frames_fit_their_width_and_show_the_new_parts(self) -> None:
        for cols, rows in ((100, 34), (57, 26)):
            frame = [plain(x) for x in render_frame(fixture(), UIState(expanded={"build-cards"}), cols, rows, NOW)]
            self.assertEqual(len(frame), rows)
            self.assertTrue(all(_wd_width(line) <= cols for line in frame), cols)
            text = "\n".join(frame)
            self.assertIn("now  ", text)
            self.assertIn("eta 6s", text)
            self.assertIn("⚠ 2", text)
            self.assertIn("P1 1", text)


if __name__ == "__main__":
    unittest.main()
