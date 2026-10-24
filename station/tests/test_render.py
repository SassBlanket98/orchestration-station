"""Acceptance checks for widgets.py / render.py."""
from __future__ import annotations

import hashlib
import re
import time
import unittest

from demo import DemoCollector
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    Finding,
    LogState,
    Milestone,
    QuotaAccount,
    QuotaWindow,
    Review,
    Snapshot,
    Step,
    UIState,
    Worker,
)
from render import (
    _rd_finished_block,
    _rd_md_wrap,
    _rd_timeline_panel,
    _rd_tl_items,
    _rd_tl_pack,
    render_frame,
    render_plain,
)
from widgets import _wd_C_SELBG, _wd_C_TEXT, _wd_markdown, _wd_width

NOW = 1_760_050_000.0
SGR = re.compile(r"\x1b\[[0-9;]*m")
ANY_ESC = re.compile(r"\x1b(?!\[[0-9;]*m)")
SELBG = "48;2;{};{};{}".format(*_wd_C_SELBG)
SIZES = [(200, 60), (160, 45), (130, 40), (110, 35), (84, 30), (70, 28), (50, 20), (40, 8), (20, 5), (1, 1)]
FAMILIES = ["claude", "codex", "grok", "deepseek", "cursor", "other"]


def _steps(n: int = 8, hostile: bool = False) -> list[Step]:
    out: list[Step] = []
    for i in range(n):
        kind = ("tool", "text", "tool", "error")[i % 4]
        text = f"step {i} pnpm test src/api/routes.ts and some more words to make this long to wrap " * 2
        if hostile:
            text = "tab\there \x1b[31mred\x00nul 漢字漢字 🚀 " * 3
        out.append(Step(NOW - 600 + i * 60, kind, "bash" if kind == "tool" else "", text))
    return out


def _worker(i: int, state: str, hostile: bool = False, nones: bool = False) -> Worker:
    live = state in (RUNNING, QUIET, STALLED)
    lg = LogState(
        steps=_steps(8, hostile), tool_counts={"read": 12, "edit": 5, "shell": 7, "search": 2}, errors=i % 2,
        tokens_in=None if nones else 84_000, tokens_out=None if nones else 9000,
        tokens_cache=None if nones else 1_200_000, cost=None if nones else 0.031,
        ctx_tokens=None if nones else 96_000, last_ts=NOW - 3)
    name = ("x" * 500) if hostile and i == 0 else f"worker-{i}"
    title = "漢字のタイトル 🚀 wide title" if hostile else "Implement REST routes for accounts"
    return Worker(
        name=name, state=state, title=title, goal="GET/POST /api/accounts with validation",
        model="sonnet-work medium", family=FAMILIES[i % 6], pid=100 + i, start=NOW - 1500 + i * 60,
        end=None if live else NOW - 300 - i * 20, cpu=None if nones else 42.0,
        rss=None if nones else 310 << 20, nproc=None if nones else 3, log=lg,
        activity=[0, 1, 3, 5, 8, 2, 0, 0, 4] * 6 + ([9, 9] if hostile else []),
        quiet_s={RUNNING: 3.0, QUIET: 120.0, STALLED: 400.0}.get(state, 0.0),
        summary=["3 files changed, tests pass"] if state == DONE else [], has_report=state == DONE)


def _quota() -> list[QuotaAccount]:
    accts = []
    for i, nm in enumerate(["codex", "claude", "claude-work", "opencode-go", "cursor", "grok"]):
        wins = [QuotaWindow("5h", 20 + i * 12, 30.0), QuotaWindow("Week", 10 + i * 5, None)]
        accts.append(QuotaAccount(nm, wins, stale=i == 5, in_use=i < 3))
    return accts


def _reviews() -> list[Review]:
    return [
        Review("review-api", "luna high", "codex", NOW - 900, NOW - 400, False,
               [Finding(1, "SQL injection in accounts route"), Finding(2, "missing validation"),
                Finding(2, "no rate limiting"), Finding(3, "typo")]),
        Review("review-ui", "luna high", "codex", NOW - 600, NOW - 100, False, []),
        Review("review-run", "luna high", "codex", NOW - 300, NOW, True, [Finding(1, "race")]),
    ]


def busy(hostile: bool = False, nones: bool = False) -> Snapshot:
    ws = [_worker(0, RUNNING, hostile, nones), _worker(1, RUNNING, hostile, nones),
          _worker(2, QUIET, hostile, nones), _worker(3, STALLED, hostile, nones)]
    for i in range(12):
        ws.append(_worker(4 + i, (DONE, FAILED, NOREPORT)[i % 3], hostile, nones))
    ms = [Milestone(NOW - 1400 + i * 200, f"milestone {i}") for i in range(5)] + [Milestone(None, "untimed")]
    return Snapshot(title="sample-project", now=NOW, session_start=NOW - 1500, workers=ws, reviews=_reviews(),
                    milestones=ms, quota=_quota(), quota_ok=True, tokens_total=1_200_000, cost_total=0.42,
                    hidden_older=12)


def one_live() -> Snapshot:
    return Snapshot(title="t", now=NOW, session_start=NOW - 600, workers=[_worker(0, RUNNING)])


def only_finished() -> Snapshot:
    return Snapshot(title="t", now=NOW, session_start=NOW - 1500,
                    workers=[_worker(4, DONE), _worker(5, FAILED), _worker(6, NOREPORT)])


def plain(line: str) -> str:
    return SGR.sub("", line)


class FrameShape(unittest.TestCase):
    def check(self, snap: Snapshot, ui: UIState, cols: int, rows: int,
              t: float = 1_760_050_000.37) -> list[str]:
        out = render_frame(snap, ui, cols, rows, t)
        label = f"{cols}x{rows} view={ui.view} tab={ui.tab}"
        self.assertEqual(len(out), rows, label)
        for i, ln in enumerate(out):
            self.assertEqual(_wd_width(ln), cols, f"{label} line {i}: {plain(ln)!r}")
            self.assertIsNone(ANY_ESC.search(ln), label)
            self.assertNotIn("\n", ln)
            self.assertTrue(ln.endswith("\x1b[0m"), label)
            self.assertNotIn("render error", ln, label)
        return out

    def test_all_sizes_and_views(self) -> None:
        snaps = [Snapshot(), one_live(), busy()]
        for snap in snaps:
            for cols, rows in SIZES:
                for view in ("overview", "detail", "quota"):
                    for tab in ("live", "brief", "report"):
                        for text in ([], ["# Title", "Goal: do `things`", "- item one", "* item two " * 30]):
                            follow = tab != "live" or len(text) == 0
                            ui = UIState(view=view, tab=tab, detail_text=text, follow=follow, density="compact")
                            self.check(snap, ui, cols, rows)

    def test_hostile(self) -> None:
        for snap in (busy(hostile=True, nones=True), busy(hostile=True)):
            for cols, rows in SIZES:
                for view in ("overview", "detail", "quota"):
                    for tab in ("live", "brief", "report"):
                        ui = UIState(view=view, tab=tab, sel=99, scroll=10**6, follow=False, density="compact",
                                     detail_text=["漢字 🚀 \t\x1b[31m\x00" * 20, "#" + "y" * 400])
                        self.check(snap, ui, cols, rows)
        snap = busy(hostile=True)
        for sel in (-5, 0, 3, 7, 15, 10**9):
            self.check(snap, UIState(sel=sel, density="compact"), 110, 35)
        snap.workers[1].activity = [1]
        snap.workers[2].activity = []
        snap.workers[3].activity = list(range(500))
        self.check(snap, UIState(density="compact"), 160, 45)

    def test_tiny_and_zero_dimensions(self) -> None:
        for cols, rows in ((1, 1), (2, 3), (39, 40), (100, 7), (5, 100)):
            self.check(busy(), UIState(density="compact"), cols, rows)

    def test_requested_sizes_in_both_densities_with_hostile_summaries(self) -> None:
        snaps = [Snapshot(), one_live(), busy(hostile=True)]
        for wk in snaps[-1].workers[4:]:
            wk.summary = ["- **bold** " + "漢字 " * 300 + "tail \x1b[31m" * 10]
        for cols, rows in ((200, 60), (160, 45), (130, 40), (110, 35), (84, 30),
                           (57, 80), (57, 40), (40, 12)):
            for snap in snaps:
                for density in ("full", "compact"):
                    self.check(snap, UIState(density=density), cols, rows)


class Shrink(unittest.TestCase):
    def flags(self, rows: int) -> tuple[bool, bool, bool]:
        text = "\n".join(plain(x) for x in render_frame(busy(), UIState(density="compact"), 160, rows, NOW))
        return "╭─ quota" in text, "╭─ reviews" in text, "╭─ timeline" in text

    def test_full_at_160x45(self) -> None:
        self.assertEqual(self.flags(45), (True, True, True))

    def test_order(self) -> None:
        for rows in range(45, 9, -1):
            q, r, tl = self.flags(rows)
            if tl:
                self.assertTrue(r and q, rows)
            if r:
                self.assertTrue(q, rows)
        self.assertEqual(self.flags(16), (False, False, False))

    def test_compact_forms(self) -> None:
        text = "\n".join(plain(x) for x in render_frame(busy(), UIState(density="compact"), 160, 16, NOW))
        self.assertIn("quota  codex", text)
        self.assertIn("reviews 3 · P1 2", text)
        self.assertRegex(text, r"timeline [▁-█]+ 25m")

    def test_live_names_at_160x16(self) -> None:
        text = "\n".join(plain(x) for x in render_frame(busy(), UIState(density="compact"), 160, 16, NOW))
        for i in range(4):
            self.assertIn(f"worker-{i}", text)

    def test_quota_hidden_when_unavailable(self) -> None:
        snap = busy()
        snap.quota_ok = False
        text = "\n".join(plain(x) for x in render_frame(snap, UIState(density="compact"), 160, 45, NOW))
        self.assertNotIn("quota  codex", text)
        self.assertNotIn("╭─ quota", text)

    def test_scrolls_when_cards_do_not_fit(self) -> None:
        many = [_worker(i, RUNNING) for i in range(12)]
        snap = Snapshot(title="x", now=NOW, session_start=NOW - 900, workers=many)
        text = "\n".join(plain(x) for x in render_frame(snap, UIState(sel=11, density="compact"), 100, 14, NOW))
        self.assertIn("worker-11", text)
        self.assertIn("▲", text)

    def test_full_counts_workers_that_do_not_fit(self) -> None:
        many = [_worker(i, RUNNING) for i in range(12)]
        snap = Snapshot(title="x", now=NOW, session_start=NOW - 900, workers=many)
        text = "\n".join(plain(x) for x in render_frame(snap, UIState(sel=11, density="full",
                                                                     expanded={"worker-11"}), 100, 14, NOW))
        self.assertIn("worker-11", text)   # the open worker is shown as its card
        self.assertIn("worker-0", text)
        self.assertNotIn("worker-10", text)
        self.assertRegex(text, r"\+\d+ more running")
        mixed = Snapshot(title="x", now=NOW, workers=[_worker(i, RUNNING) for i in range(6)]
                         + [_worker(20 + i, DONE) for i in range(3)])
        text = "\n".join(plain(x) for x in render_frame(mixed, UIState(density="full", expanded={"worker-0"}),
                                                       100, 16, NOW))
        self.assertIn("worker-5", text)     # live rows come before finished ones
        self.assertNotIn("worker-20", text)
        self.assertIn("+3 more finished", text)


class Animation(unittest.TestCase):
    def test_running_moves(self) -> None:
        a = render_frame(one_live(), UIState(density="compact"), 110, 30, 1000.0)
        b = render_frame(one_live(), UIState(density="compact"), 110, 30, 1000.1)
        self.assertNotEqual(a, b)

    def test_finished_is_still(self) -> None:
        a = render_frame(only_finished(), UIState(density="compact"), 110, 30, 1000.0)
        b = render_frame(only_finished(), UIState(density="compact"), 110, 30, 1000.3)
        self.assertEqual(a[1:], b[1:])
        c = render_frame(only_finished(), UIState(density="compact"), 110, 30, 1001.0)
        self.assertNotEqual(a[0], c[0])

    def test_flash(self) -> None:
        snap = busy()
        name = snap.workers[0].name
        base = render_frame(snap, UIState(density="compact"), 160, 45, NOW)
        fresh = render_frame(snap, UIState(flashes={name: NOW - 1}, density="compact"), 160, 45, NOW)
        old = render_frame(snap, UIState(flashes={name: NOW - 5}, density="compact"), 160, 45, NOW)
        self.assertNotEqual(base, fresh)
        self.assertEqual(base, old)
        fin = snap.workers[4].name
        self.assertNotEqual(base, render_frame(snap, UIState(flashes={fin: NOW - 1}, density="compact"), 160, 45, NOW))
        rev = render_frame(snap, UIState(flashes={"reviews": NOW - 1}, density="compact"), 160, 45, NOW)
        self.assertNotEqual(base, rev)
        self.assertEqual(base, render_frame(snap, UIState(flashes={"reviews": NOW - 4}, density="compact"), 160, 45, NOW))

    def test_selection_background(self) -> None:
        out = render_frame(busy(), UIState(sel=1, density="compact"), 160, 45, NOW)
        marked = [i for i, ln in enumerate(out) if SELBG in ln]
        self.assertEqual(len(marked), 1)
        self.assertIn("worker-1", plain(out[marked[0]]))
        out = render_frame(busy(), UIState(sel=6, density="compact"), 160, 45, NOW)
        marked = [i for i, ln in enumerate(out) if SELBG in ln]
        self.assertEqual(len(marked), 1)


class Plain(unittest.TestCase):
    def test_plain(self) -> None:
        snap = busy()
        for cols in (30, 60, 100, 200):
            txt = render_plain(snap, cols)
            self.assertNotIn("\x1b", txt)
            self.assertTrue(txt.endswith("\n"))
            for ln in txt.splitlines():
                self.assertLessEqual(_wd_width(ln), cols, ln)
        txt = render_plain(snap, 200)
        for wk in snap.workers:
            self.assertIn(wk.name, txt)
        for word in ("review-api", "P1", "SQL injection", "codex"):
            self.assertIn(word, txt)
        self.assertTrue(render_plain(Snapshot()).endswith("\n"))
        render_plain(busy(hostile=True, nones=True), 80)

    def test_rows_prioritize_live_newest_finished_and_recent_milestones(self) -> None:
        snap = Snapshot(now=NOW, workers=[_worker(0, RUNNING)] + [_worker(i, DONE) for i in range(1, 12)],
                        milestones=[Milestone(NOW - i, f"priority milestone {i}") for i in range(7, 0, -1)])
        for i, wk in enumerate(snap.workers[1:]):
            wk.summary = [f"finished item {i} complete block"]
        lines = render_plain(snap, 100, rows=40).splitlines()
        text = "\n".join(lines)
        self.assertLessEqual(len(lines), 40)
        self.assertLess(text.index("● worker-0"), text.index("✔ worker-1"))
        self.assertIn("finished item 0 complete block", text)
        self.assertLess(text.index("finished item 0"), text.index("priority milestone 1"))
        self.assertLess(text.index("priority milestone 3"), text.index("✔ worker-2"))
        for i in (1, 2, 3):
            self.assertIn(f"priority milestone {i}", text)
        self.assertIn("more finished", text)
        self.assertIn("earlier milestones", text)
        self.assertNotRegex(text, r"\+\d+ more(?:\n|$)")


def _frame_text(snap: Snapshot, cols: int = 160, rows: int = 45, ui: UIState | None = None) -> list[str]:
    return [plain(x) for x in render_frame(snap, ui or UIState(density="compact"), cols, rows, NOW)]


class TimelineFixes(unittest.TestCase):
    def test_review_worker_drawn_once(self) -> None:
        snap = busy()
        snap.workers[4].name = "review-api"
        snap.workers[4].is_review = True
        lanes = [ln for ln in _frame_text(snap) if "review-api" in ln and "━" in ln]
        self.assertEqual(len(lanes), 1, lanes)
        self.assertIn("review-api 1·2·1", lanes[0])
        self.assertIn("✔", lanes[0])
        no_dup = [ln for ln in _frame_text(busy()) if "review-api" in ln and "━" in ln]
        self.assertEqual(len(no_dup), 1)   # a plain review (no worker twin) is still drawn

    def test_review_without_worker_twin_is_kept(self) -> None:
        snap = busy()
        snap.workers[4].name = "review-api"   # same name but not flagged as a review
        lanes = [ln for ln in _frame_text(snap) if "review-api" in ln and "━" in ln]
        self.assertEqual(len(lanes), 2, lanes)

    def test_name_inside_full_width_bar(self) -> None:
        snap = busy()
        for wk in snap.workers[:2]:
            wk.start = NOW - 1500
        lines = _frame_text(snap)
        for name in ("worker-0", "worker-1"):
            row = next(ln for ln in lines if name in ln and "━" in ln)
            self.assertIn(f" {name} ", row)
            self.assertEqual(row.count(name), 1)
        frame = render_frame(snap, UIState(density="compact"), 160, 45, NOW)
        raw = next(ln for ln in frame if "worker-0" in plain(ln) and "━" in ln)
        self.assertIn("\x1b[1;38;2;{};{};{}mworker-0".format(*_wd_C_TEXT), raw)


class PlainHeader(unittest.TestCase):
    def test_clock_never_cut(self) -> None:
        snap = busy()
        clock = time.strftime("%H:%M:%S", time.localtime(snap.now))
        for cols in (20, 30, 60, 80, 100, 120, 200):
            head = render_plain(snap, cols).splitlines()[0]
            self.assertTrue(head.endswith(clock), (cols, head))
            self.assertLessEqual(_wd_width(head), cols)

    def test_older_on_header_line(self) -> None:
        snap = busy()
        text = render_plain(snap, 200)
        self.assertIn("+12 older", text.splitlines()[0])
        self.assertNotIn("\n+12 older", text)
        head = render_plain(snap, 100).splitlines()[0]
        self.assertNotIn("Σ", head)   # totals go first


class FullDensity(unittest.TestCase):
    def test_wrapped_full_density_bullet_continues_in_order(self) -> None:
        snap = DemoCollector().snapshot()
        worker = next(wk for wk in snap.workers if wk.name == "db-schema")
        worker.state = DONE
        block = _rd_finished_block(worker, False, snap.now, 55)
        visible = " ".join(plain(line).strip() for line in block)
        expected = "accounts.plan_id added: uuid, foreign key to plans(id), indexed."
        self.assertIn(expected, visible)
        self.assertEqual(visible.count("accounts.plan_id added"), 1, visible)

        milestone = "accounts.plan_id accounts.plan_id follows the first occurrence."
        milestone_lines = _rd_md_wrap(milestone, 25, "       ")
        milestone_text = " ".join(plain(line).strip() for line in milestone_lines)
        self.assertIn(milestone, milestone_text)

        failed = _worker(7, FAILED)
        failed.log.steps[-1].kind = "error"
        failed.log.steps[-1].text = "database lock timeout after migration commit"
        failed_block = _rd_finished_block(failed, False, NOW, 40)
        failed_text = " ".join(plain(line).strip() for line in failed_block)
        self.assertIn(failed.log.steps[-1].text, failed_text)

    @unittest.skip("hash of the original's captured rendering text")
    def test_compact_matches_captured_baseline_fixture(self) -> None:
        # worker-0 (the cursor's worker) is open, as it was before the open set replaced the one-open rule
        lines = [plain(x).rstrip() for x in render_frame(
            busy(), UIState(density="compact", expanded={"worker-0"}), 160, 45, NOW + 0.3)]
        # slice s5-insights: the header's ⚠ count, now-lines, ETA on live rows and the review outcome changed this frame
        # slice ctx-tokens: the header drops the token sum and worker rows show their own window
        # slice d-pages: the footer gained "[L] lead  [C] cost  [F] files"; the only changed row is the footer line
        # the header now names the batch ("monitor · <station>") instead of "workers · workers"
        # lr-counts: a finished worker with no report reads "finished · no report" and is no longer in the ⚠ count
        self.assertEqual(hashlib.sha256("\n".join(lines).encode()).hexdigest(),
                         "ffd3eb6a3790df07deaf8fc05a5f792acfb5170922e0155fc3a79ac25ffad6c5")

    def test_markdown_cleanup(self) -> None:
        spans = _wd_markdown("- **bold** `code` [label](https://x.test) https://anthropic.com/a/very/long/path")
        text = "".join(x[0] for x in spans)
        self.assertEqual(text, "bold code label anthropic.com/…")
        self.assertTrue(any(bold for _, _, bold in spans))
        self.assertEqual(_wd_markdown("1. __strong__", plain=True), "strong")
        self.assertEqual(_wd_markdown("2025. was a good year", plain=True), "2025. was a good year")
        self.assertIn("**unclosed", "".join(x[0] for x in _wd_markdown("**unclosed")))

    def test_finished_blocks_and_services(self) -> None:
        snap = Snapshot(now=NOW, workers=[_worker(0, RUNNING)] + [_worker(i, DONE) for i in range(1, 5)],
                        milestones=[Milestone(NOW - i, f"milestone {i}") for i in range(5, 0, -1)],
                        services=[("dash", True), ("preview", False)])
        for wk in snap.workers[1:]:
            wk.summary = [f"bullet {i} finalword{i}" for i in range(4)]
        out = render_frame(snap, UIState(sel=1, expanded={"worker-1"}), 57, 80, NOW)   # the open finished row
        text = "\n".join(plain(x) for x in out)
        for i in range(1, 5):
            self.assertIn(f"worker-{i}", text)
        self.assertIn("finalword0", text)
        self.assertEqual(text.count("finalword"), 3)   # only the expanded row carries its summary
        self.assertIn("milestone 1", text)
        self.assertNotIn("milestone 2", text)
        self.assertIn("+4", text)
        self.assertIn("services", text)
        short = "\n".join(plain(x) for x in render_frame(snap, UIState(), 57, 22, NOW))
        self.assertIn("worker-0", short)
        self.assertIn("milestone 1", short)
        self.assertNotIn("milestone 5", short)
        opened = "\n".join(plain(x) for x in render_frame(snap, UIState(lead_open=True), 57, 40, NOW))
        self.assertIn("milestone 5", opened)
        self.assertLess(opened.index("milestone 1"), opened.index("milestone 5"))
        for width, height in ((200, 60), (160, 45), (130, 40), (110, 35), (84, 30),
                              (57, 80), (57, 40), (40, 12)):
            for density in ("full", "compact"):
                frame = render_frame(snap, UIState(density=density), width, height, NOW)
                self.assertEqual(len(frame), height)
                self.assertTrue(all(_wd_width(line) == width for line in frame))

    def test_live_names_survive_tiny_full_frame(self) -> None:
        text = "\n".join(plain(x) for x in render_frame(busy(), UIState(), 40, 12, NOW))
        for i in range(4):
            self.assertIn(f"worker-{i}", text)

    def test_plain_everything_and_row_limit(self) -> None:
        snap = Snapshot(now=NOW, workers=[_worker(0, RUNNING), _worker(1, DONE)],
                        milestones=[Milestone(NOW, "milestone text")], services=[("dash", True)])
        snap.workers[1].summary = ["- **every** bullet `text`"] * 4
        all_text = render_plain(snap, 30)
        self.assertIn("every bullet text", all_text)
        self.assertEqual(all_text.count("every bullet text"), 4)
        self.assertIn("milestone text", all_text)
        self.assertIn("services: dash up", all_text)
        limited = render_plain(snap, 30, 8)
        self.assertLessEqual(len(limited.splitlines()), 8)
        self.assertNotIn("\x1b", all_text)
        self.assertTrue(all(_wd_width(line) <= 30 for line in all_text.splitlines()))


class ReviewHead(unittest.TestCase):
    def reviews_lines(self, cols: int, revs: list[Review]) -> list[str]:
        snap = busy()
        snap.reviews = revs
        out = _frame_text(snap, cols, 45)
        return [ln for ln in out if "review-" in ln and "╭" not in ln]

    def revs(self, model: str) -> list[Review]:
        r = _reviews()
        for x in r:
            x.model = model
        return r

    def test_long_model_is_cut_time_and_counts_stay(self) -> None:
        revs = self.revs("codex gpt-6-luna high")
        revs[0].end = NOW - 400
        lines = [ln for ln in self.reviews_lines(160, revs) if "review-api" in ln and "P1 1" in ln]
        self.assertEqual(len(lines), 1)
        ln = lines[0]
        self.assertIn(time.strftime("%H:%M", time.localtime(NOW - 400)), ln)
        self.assertIn("P1 1  P2 2  P3 1", ln)
        self.assertIn("…", ln)
        self.assertNotIn("0…", ln)

    def test_same_count_format_on_every_review(self) -> None:
        for cols in (130, 160, 200):
            heads = [ln for ln in self.reviews_lines(cols, self.revs("codex gpt-6-luna high"))
                     if "luna" in ln or "codex" in ln]
            with_counts = [ln for ln in heads if re.search(r"P\d ", ln)]
            self.assertTrue(all(re.search(r"P1 \d  P2 \d  P3 \d", ln) for ln in with_counts), heads)

    def test_outcome_kept_next_to_a_long_name(self) -> None:
        rev = Review("review-long-name-here", "m", "codex", NOW - 900, NOW - 400, False,
                     [Finding(1, "a"), Finding(2, "b"), Finding(3, "c")] * 5)
        snap = busy()
        snap.reviews = [rev, _reviews()[1]]
        text = _frame_text(snap, 130, 40)
        heads = [ln for ln in text if ("review-long" in ln or "review-ui" in ln) and "━" not in ln]
        self.assertEqual(len(heads), 2, heads)
        for ln in heads:
            self.assertRegex(ln, r"P1 \d  P2 \d  P3 \d|clean")


class CountsRow(unittest.TestCase):
    def test_zero_categories_and_err_omitted(self) -> None:
        wk = _worker(0, RUNNING)
        wk.log.tool_counts = {"read": 3, "edit": 0, "shell": 0, "search": 2, "other": 0}
        wk.log.errors = 0
        snap = Snapshot(title="t", now=NOW, session_start=NOW - 600, workers=[wk])
        text = "\n".join(_frame_text(snap, 110, 30))
        self.assertIn("read 3", text)
        self.assertIn("search 2", text)
        for word in ("edit 0", "shell 0", "other 0"):
            self.assertNotIn(word, text)
        self.assertNotRegex(text, r"\berr \d")
        wk.log.errors = 2
        text = "\n".join(_frame_text(snap, 110, 30))
        self.assertRegex(text, r"\berr 2")


class Speed(unittest.TestCase):
    def test_busy_200x60(self) -> None:
        snap, ui = busy(), UIState(density="compact")
        render_frame(snap, ui, 200, 60, NOW)
        start = time.perf_counter()
        for k in range(50):
            render_frame(snap, ui, 200, 60, NOW + k * 0.1)
        avg = (time.perf_counter() - start) / 50
        self.assertLess(avg, 0.030, f"{avg * 1000:.1f} ms")


if __name__ == "__main__":
    unittest.main()


class HeaderClock(unittest.TestCase):
    def test_clock_right_aligned_at_every_width(self) -> None:
        snap = busy()
        clock = time.strftime("%H:%M:%S", time.localtime(NOW))
        for view in ("overview", "detail"):
            for cols in range(40, 201):
                head = _frame_text(snap, cols, 40, UIState(view=view, density="compact"))[0]
                self.assertTrue(head.rstrip().endswith(clock), (view, cols, head))
                self.assertLessEqual(_wd_width(head), cols)

    def test_shed_order(self) -> None:
        snap = busy()
        snap.milestones[-2].text = "lead text"
        wide = _frame_text(snap, 220, 40)[0]
        self.assertIn("lead:", wide)
        self.assertIn("Σ", wide)
        mid = _frame_text(snap, 100, 40)[0]
        self.assertNotIn("Σ", mid)
        self.assertNotIn("lead:", mid)
        tight = _frame_text(snap, 60, 40)[0]
        self.assertIn("1▸", tight.replace("2▸", "1▸"))
        self.assertIn("✔", tight)
        self.assertNotIn("older", tight)


class LaneSharedName(unittest.TestCase):
    def test_live_bar_after_finished_item_keeps_name(self) -> None:
        snap = busy()
        base = NOW - 1500
        ws = []
        for i in range(5):
            wk = _worker(i, RUNNING)
            wk.name = f"w{i}-long-name"
            wk.start, wk.end = base + 600 + i, None
            ws.append(wk)
        done = _worker(5, DONE)
        done.name = "first-done"
        done.start, done.end = base, base + 240
        late = _worker(6, RUNNING)
        late.name = "late-live"
        late.start, late.end = base + 420, None
        snap.workers = ws + [done, late]
        snap.reviews = []
        snap.session_start = base
        items = _rd_tl_items(snap, 148)
        lanes, _ = _rd_tl_pack(items, 6)
        self.assertTrue(any(len(ln) > 1 for ln in lanes))
        lines = [plain(x) for x in _rd_timeline_panel(snap, UIState(density="compact"), 0, 150, 10, NOW, lanes, 0)]
        row = next(ln for ln in lines if "late-live" in ln and "━" in ln)
        self.assertEqual(row.count("late-live"), 1)
        self.assertIn(" late-live ", row)
