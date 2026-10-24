from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from taskhelp import open_tasks

import widgets
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    Finding,
    GlobalSnapshot,
    LeadInfo,
    LogState,
    Milestone,
    QuotaAccount,
    QuotaCredit,
    QuotaExtra,
    QuotaWindow,
    Review,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import (
    _rd_extra_text,
    _rd_quota_winner,
    _rd_ttr,
    global_rows,
    render_global,
    render_global_plain,
)
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_760_050_000.0
BLUE = "38;2;{};{};{}".format(*widgets._wd_C_BLUE)
BORDER = "38;2;{};{};{}".format(*widgets._wd_C_BORDER)
RED = "38;2;{};{};{}".format(*widgets._wd_C_RED)
AMBER = "38;2;{};{};{}".format(*widgets._wd_C_AMBER)


def sample() -> GlobalSnapshot:
    workers = [Worker("live", "running", title="Compute polish", goal="tighten gpu rows",
                      model="luna high", start=NOW - 240),
               Worker("failed", FAILED, model="opus", start=NOW - 500, end=NOW - 20),
               *[Worker(f"done{i}", DONE, start=NOW - 300, end=NOW - 10) for i in range(5)]]
    snap = Snapshot(now=NOW, workers=workers, milestones=[Milestone(NOW - 60, "starting next slice")],
                    reviews=[Review("review", "luna", "codex", NOW - 80, NOW - 20, False,
                                    [Finding(1, "P1 issue"), Finding(2, "P2 issue")])],
                    tokens_total=1_200_000, cost_total=0.84)
    alpha = Stage("/a", "alpha", snap, 1, NOW - 60, LeadInfo("Luna", "build", "picker-burn"))
    alpha.session_name, alpha.tag, alpha.lead_model, alpha.effort = "picker burn", "CC·W", "luna", "high"
    return GlobalSnapshot(now=NOW, stages=[alpha, Stage("/b", "beta", Snapshot(now=NOW), 0, NOW - 3600,
                                                        LeadInfo("opus"))], hidden_idle=2)


def station(path: str, label: str, workers: list[Worker], last: float = NOW - 30,
            lead: LeadInfo | None = None, **snap_kw) -> Stage:
    return Stage(path, label, Snapshot(now=NOW, workers=workers, **snap_kw),
                 sum(w.state == RUNNING for w in workers), last, lead)


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


def card_lines(frame: list[str], label: str) -> list[str]:
    """The lines of one card: from its top border (holding the label) to the bottom border."""
    top = next(i for i, line in enumerate(plain(frame)) if "╭" in line and label in line)
    bottom = next(i for i in range(top, len(frame)) if "╰" in plain(frame)[i])
    return plain(frame)[top:bottom + 1]


class GlobalRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.no_color = patch.dict(os.environ, {}, clear=True)
        self.no_color.start()
        self.addCleanup(self.no_color.stop)

    def test_frame_dimensions(self) -> None:
        gs = sample()
        for cols, rows in ((57, 40), (100, 30), (160, 50)):
            for ui in (UIState(), UIState(gpage="milestones", gstage="/a"),
                       UIState(gpage="reviews", gstage="/a")):
                frame = render_global(gs, ui, cols, rows, NOW)
                self.assertEqual(len(frame), rows)
                self.assertTrue(all(_wd_width(line) <= cols for line in frame))

    def test_no_line_wider_than_cols_at_each_width(self) -> None:
        gs = sample()
        gs.stages.append(station("/c", "a-very-long-station-name-for-the-width-check",
                                 [Worker("a-long-worker-name-that-must-be-cut", RUNNING, model="claude-opus-5-5",
                                         start=NOW - 90, quiet_s=120, activity=[1, 2])],
                                 NOW - 10, LeadInfo("Claude Opus 5.5", "build", "a-stream-with-a-long-name")))
        for cols in (57, 100, 160):
            for rows in (8, 20, 40):
                frame = render_global(gs, UIState(gsel=2), cols, rows, NOW)
                self.assertEqual(len(frame), rows)
                self.assertTrue(all(_wd_width(_wd_SGR_RE.sub("", line)) <= cols for line in frame), (cols, rows))

    def test_header_and_footer_shed_at_narrow_width(self) -> None:
        wide = plain(render_global(sample(), UIState(), 100, 20, NOW))
        self.assertIn("◆ Station", wide[0])   # a short screen: the title is one line
        self.assertIn("1 working", wide[1])
        self.assertIn("1 live", wide[1])
        self.assertIn("[Enter] fold", wide[-1])
        narrow = plain(render_global(sample(), UIState(), 57, 20, NOW))
        self.assertIn("1 working", narrow[1])
        self.assertNotIn("stations", narrow[1])
        self.assertIn("[?] keys  [q] quit", narrow[-1])
        tiny = plain(render_global(sample(), UIState(), 40, 20, NOW))
        self.assertIn("[?] keys  [q] quit", tiny[-1])   # the tail always stays

    def test_header_counts_only_live_active_leads_alongside_workers(self) -> None:
        active = Stage("/active", "active", Snapshot(now=NOW), lead_state="live", state="active", solo=True)
        idle = Stage("/idle", "idle", Snapshot(now=NOW), lead_state="live", state="idle")
        gone = Stage("/gone", "gone", Snapshot(now=NOW), lead_state="gone", state="idle")
        gs = GlobalSnapshot(now=NOW, stages=[active, idle, gone])
        frame = plain(render_global(gs, UIState(), 100, 30, NOW))
        self.assertIn("1 working", next(line for line in frame if "working" in line))
        self.assertNotIn("All quiet.", "\n".join(frame))
        active.snap.workers = [Worker("worker", RUNNING, start=NOW)]
        frame = plain(render_global(gs, UIState(), 100, 30, NOW))
        header = next(line for line in frame if "working" in line)
        self.assertIn("1 working", header)      # a session with a live lead and a running worker counts once

    def test_header_counts_an_active_background_lead(self) -> None:
        hosted = Stage("/hosted", "hosted", Snapshot(now=NOW), lead_state="background", state="active", solo=True)
        gs = GlobalSnapshot(now=NOW, stages=[hosted])
        frame = plain(render_global(gs, UIState(), 100, 30, NOW))
        self.assertIn("1 working", next(line for line in frame if "working" in line))
        self.assertNotIn("All quiet.", "\n".join(frame))

    def test_one_row_per_station_in_order(self) -> None:
        busy = station("/busy", "busy", [Worker("x", RUNNING, start=NOW), Worker("y", RUNNING, start=NOW)],
                       NOW - 500)
        one = station("/one", "one", [Worker("z", RUNNING, start=NOW)], NOW - 5)
        quiet = station("/quiet", "quiet", [Worker("q", DONE, start=NOW - 5, end=NOW - 1)], NOW - 1)
        gs = GlobalSnapshot(now=NOW, stages=[quiet, one, busy])
        stages = [row[0] for row in global_rows(gs, UIState()) if row[1] == "stage"]
        self.assertEqual(stages, ["/one", "/busy"])   # active, most recently active first; then closed
        self.assertEqual({row[1] for row in global_rows(gs, UIState())}, {"stage", "worker", "group", "more"})
        frame = plain(render_global(gs, UIState(), 100, 40, NOW))
        at = [next(i for i, line in enumerate(frame) if re.search(rf"[◆◇◐] {name}\b", line))
              for name in ("one", "busy")]
        self.assertEqual(at, sorted(at))

    def test_closed_station_folds_under_its_project(self) -> None:
        gs = sample()
        frame = plain(render_global(gs, UIState(), 100, 30, NOW))
        self.assertEqual(sum("finished 1" in line for line in frame), 1)
        self.assertFalse(any("beta" in line and "heading" in line for line in frame))
        self.assertNotIn(("/b", "stage", ""), global_rows(gs, UIState()))
        self.assertTrue(any("◆ picker burn" in line for line in frame))

    def test_problems_count_failed_workers_and_p1_findings(self) -> None:
        workers = [Worker(f"bad{i}", FAILED, start=NOW - 60, end=NOW - 1) for i in range(3)]
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", workers, NOW - 1,
                                                    reviews=[Review("r", findings=[Finding(1, "x")])])])
        frame = render_global(gs, UIState(), 100, 30, NOW)
        from render import _rd_g_rowdata
        self.assertEqual(_rd_g_rowdata(gs.stages[0]).problems, 4)
        self.assertIn("1 problem", "\n".join(plain(frame)))
        self.assertIn("4 problems 1", "\n".join(plain(frame)))
        nr = GlobalSnapshot(now=NOW, stages=[station("/n", "nr", [Worker("openclaw-readme", NOREPORT, start=NOW - 9,
                                                                         end=NOW - 1)], NOW - 1)])
        ui = UIState(gopen={("/n", "stage", ""), ("", "more", "finished:/n")}, gquips=False)
        open_tasks(nr, ui)
        out = render_global(nr, ui, 100, 20, NOW)
        raw = next(line for line in out if "openclaw-readme" in _wd_SGR_RE.sub("", line))
        self.assertIn(AMBER, raw)
        self.assertIn("○", _wd_SGR_RE.sub("", raw))

    def test_selected_row_has_cursor_and_background_others_do_not(self) -> None:
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [Worker("w", RUNNING, start=NOW)]),
                                             station("/b", "beta", [Worker("v", RUNNING, start=NOW)])])
        ui = UIState()
        ui.gsel = global_rows(gs, ui).index(("/b", "stage", ""))
        frame = render_global(gs, ui, 100, 30, NOW)
        marked = [line for line in frame if "\x1b[48;2" in line]
        self.assertEqual(len(marked), 1)
        self.assertIn("beta", _wd_SGR_RE.sub("", marked[0]))
        self.assertIn("\x1b[48;2", marked[0])

    def test_open_row_keeps_workers_milestone_and_reviews(self) -> None:
        frame = plain(render_global(sample(), UIState(), 100, 30, NOW))
        text = "\n".join(frame)
        alpha = next(line for line in frame if "◆ picker burn" in line)
        self.assertNotIn("1 live", alpha)
        self.assertNotIn("6 fin", alpha)
        self.assertIn("1 problem", text)   # masthead counts affected sessions
        self.assertIn("picker burn", alpha)
        self.assertIn("CC·W", alpha)
        self.assertIn("luna", alpha)
        self.assertIn("hi", alpha)
        self.assertIn("├─", text)
        self.assertIn("failed", text)
        self.assertIn("⚑ 00:45 starting next slice", text)
        self.assertIn("reviews 2 · P1 1  P2 1  P3 0", text)

    def test_finished_station_is_closed_until_opened(self) -> None:
        done = [Worker(f"d{i}", DONE, start=NOW - 90, end=NOW - 80) for i in range(3)]
        done.append(Worker("nr", NOREPORT, start=NOW - 90, end=NOW - 80))
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", done, NOW - 30, reviews=[Review("r1"), Review("r2")],
                                                    milestones=[Milestone(ts=NOW - 60, text="ok")])])
        closed = "\n".join(plain(render_global(gs, UIState(glegend=False), 100, 30, NOW)))
        self.assertIn("finished 1", closed)
        self.assertNotIn("├─", closed)
        self.assertEqual([r[1] for r in global_rows(gs, UIState())], ["more"])
        ui = UIState(gopen={("/a", "stage", ""), ("", "more", "finished:/a")})
        self.assertEqual([r[1] for r in global_rows(gs, ui)], ["more", "stage", "task"])   # its finished work is one line
        open_tasks(gs, ui)
        opened = "\n".join(plain(render_global(gs, ui, 100, 30, NOW)))
        self.assertNotIn("earlier from this session", opened)   # one task: every finished worker shows, no fold
        self.assertIn("○ nr", opened)   # the no-report mark, not a note
        self.assertIn("reviews 0", opened)
        self.assertEqual([r[1] for r in global_rows(gs, ui)], ["more", "stage", "task"] + ["worker"] * 4)
        ui.gopen.add(("/a", "more", ""))
        self.assertIn("○ nr", "\n".join(plain(render_global(gs, ui, 100, 30, NOW))))

    def test_lagging_workers_are_flagged_and_counted(self) -> None:
        workers = [Worker("slow", QUIET, start=NOW - 300, quiet_s=56, model="luna")]
        workers += [Worker(f"w{i}", RUNNING, start=NOW - 30, model="opus") for i in range(4)]
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", workers, NOW - 1)])
        frame = plain(render_global(gs, UIState(gquips=False, glegend=False), 100, 30, NOW))
        text = "\n".join(frame)
        self.assertIn("1 lagging", text)
        slow = next(line for line in frame if "slow" in line)
        self.assertIn("slow", slow)
        self.assertIn("05m 00s", slow)
        order = [next(i for i, line in enumerate(frame) if f" {n} " in line + " ") for n in ("w0", "slow")]
        self.assertEqual(order, sorted(order))   # running workers come first

    def test_open_station_keeps_lead_window_at_57_columns(self) -> None:
        live = Worker("w", RUNNING, start=NOW - 60, log=LogState(tokens_in=40_000, tokens_out=0))
        snap = Snapshot(now=NOW, workers=[live, Worker("x", DONE, start=NOW - 9, end=NOW - 8)],
                        tokens_total=46_000, cost_total=0.01, milestones=[Milestone(ts=NOW - 30, text="working")])   # the ctx is on the session row now
        gs = GlobalSnapshot(now=NOW, stages=[Stage("/a", "alpha", snap, 1, NOW - 1, lead_ctx=46_000,
                                                   lead_ctx_window=100_000)])
        for cols in (110, 57):
            self.assertIn("46k", "\n".join(plain(render_global(gs, UIState(), cols, 20, NOW))))

    def test_quota_line_and_background_only_on_the_cursor_row(self) -> None:
        gs = sample()
        gs.quota_ok = True
        gs.quota = [QuotaAccount("codex", [QuotaWindow("5h", 100.0, None)])]
        frame = render_global(gs, UIState(), 100, 30, NOW)
        self.assertIn("codex 100%", next(ln for ln in plain(frame) if "codex" in ln))   # under the three-row title
        self.assertEqual(sum("\x1b[48;2" in line for line in frame), 1)
        self.assertIn("quota offline", "\n".join(plain(render_global(sample(), UIState(), 100, 30, NOW))))

    def test_scroll_keeps_selected_card_visible(self) -> None:
        stages = []
        for i in range(8):
            st = station(f"/s{i}", f"s{i}", [], NOW - i, lead=LeadInfo("opus"))
            st.state = "idle"
            stages.append(st)
        gs = GlobalSnapshot(now=NOW, stages=stages)
        ui = UIState(gopen_keys={f"p:/s{i}" for i in range(8)})
        ui.gsel = global_rows(gs, ui).index(("/s6", "stage", ""))
        frame = plain(render_global(gs, ui, 60, 12, NOW))
        self.assertTrue(any("↑" in line and "more" in line for line in frame))
        self.assertTrue(any("s6" in line for line in frame))
        self.assertFalse(any("s0" in line for line in frame))
        top = plain(render_global(gs, UIState(gsel=0), 60, 12, NOW))
        self.assertTrue(any("↓" in line and "more" in line for line in top))
        self.assertTrue(any("s0" in line for line in top))

    def test_milestone_and_review_pages_scroll_clamp_and_footer(self) -> None:
        gs = sample()
        for page in ("milestones", "reviews"):
            frame = render_global(gs, UIState(gpage=page, gstage="/a", scroll=999), 57, 12, NOW)
            self.assertEqual(len(frame), 12)
            self.assertIn("Esc", "".join(frame))
            plain_frame = [_wd_SGR_RE.sub("", line) for line in frame]
            self.assertIn("quit", plain_frame[-1])

    def test_empty_and_plain_output(self) -> None:
        empty = render_global(GlobalSnapshot(now=NOW), UIState(), 57, 10, NOW)
        self.assertIn("no orchestrators", "".join(empty))
        text = render_global_plain(sample(), 100)
        self.assertNotRegex(text, re.compile(r"\x1b\[[0-9;]*m"))
        self.assertIn("alpha", text)
        self.assertIn("+2 more finished", text)
        self.assertNotIn("▾", text)
        short = render_global_plain(sample(), 57, rows=5)
        self.assertEqual(len(short.splitlines()), 5)
        self.assertIn("+", short.splitlines()[-1])

    def test_quiet_line_idle_age_and_quota_overflow(self) -> None:
        done = Worker("w", DONE, start=NOW - 7300, end=NOW - 7200)
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [done], last=NOW - 3600, lead=LeadInfo("opus", "build"))],
                            quota=[QuotaAccount(f"acct{i}", [QuotaWindow("5h", 50 + i, NOW + 3600)]) for i in range(12)], quota_ok=True)
        frame = "\n".join(plain(render_global(gs, UIState(), 100, 30, NOW)))
        self.assertNotIn("All quiet.", frame)
        self.assertIn("finished 1", frame)
        quota = next(line for line in frame.splitlines() if "acct" in line)
        self.assertRegex(quota, r"\+\d+\s*$")
        self.assertLessEqual(_wd_width(quota), 100)

    def test_credit_account_balance_shows_in_global_quota_line(self) -> None:
        done = Worker("w", DONE, start=NOW - 7300, end=NOW - 7200)
        credit = QuotaCredit(balance=1.84, burn_per_day=0.5, runs_out_at=NOW + 3 * 86400, low=True)
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [done], last=NOW - 7200, lead=LeadInfo("opus", "build"))],
                            quota=[QuotaAccount("opencode-zen", credit=credit)], quota_ok=True)
        frame = "\n".join(plain(render_global(gs, UIState(), 100, 30, NOW)))
        line = next(line for line in frame.splitlines() if "opencode-zen" in line)
        self.assertIn("$1.84", line)

    def test_capped_account_shows_extra_usage_in_quota_line_and_panel(self) -> None:
        done = Worker("w", DONE, start=NOW - 7300, end=NOW - 7200)
        extra = QuotaExtra(usd=7.54, method="measured", since=NOW - 86400)
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [done], last=NOW - 7200, lead=LeadInfo("opus", "build"))],
                            quota=[QuotaAccount("opencode-go", [QuotaWindow("7d", 100.0, None)], extra=extra),
                                   QuotaAccount("claude", [QuotaWindow("5h", 20.0, None)])], quota_ok=True)
        frame = "\n".join(plain(render_global(gs, UIState(), 100, 30, NOW)))
        self.assertIn("+$7.54", frame)
        self.assertEqual(frame.count("+$7.54"), 1)
        self.assertEqual(_rd_extra_text(QuotaExtra(None, "unknown", NOW)), "capped")
        self.assertEqual(_rd_extra_text(QuotaExtra(3.0, "estimated", NOW)), "capped · extra $3.00 est")

    def test_quota_winner_binding_constraint(self) -> None:
        capped = QuotaAccount("a", [QuotaWindow("5h", 12.0, None, window="five_hour"),
                                    QuotaWindow("Week", 100.0, None, window="seven_day")])
        self.assertEqual(_rd_quota_winner(capped).name, "Week")          # capped weekly beats a fresh 5h
        fresh = QuotaAccount("b", [QuotaWindow("5h", 62.0, None, window="five_hour"),
                                   QuotaWindow("Week", 30.0, None, window="seven_day")])
        self.assertEqual(_rd_quota_winner(fresh).name, "5h")             # fresh weekly lets the 5h show
        high = QuotaAccount("c", [QuotaWindow("5h", 20.0, None, window="five_hour"),
                                  QuotaWindow("Week", 55.0, None, window="seven_day")])
        self.assertEqual(_rd_quota_winner(high).name, "Week")            # no cap, no projection: highest use
        proj = QuotaAccount("d", [QuotaWindow("5h", 40.0, None, window="five_hour", runs_out_at=NOW + 600),
                                  QuotaWindow("Week", 70.0, None, window="seven_day", runs_out_at=NOW + 9000)])
        self.assertEqual(_rd_quota_winner(proj).name, "5h")              # earliest projected run-out wins
        tie = QuotaAccount("e", [QuotaWindow("Week", 40.0, None, window="seven_day"),
                                 QuotaWindow("5h", 40.0, None, window="five_hour")])
        self.assertEqual(_rd_quota_winner(tie).name, "5h")               # a tie falls to the shorter window
        self.assertIsNone(_rd_quota_winner(QuotaAccount("f", [])))

    def test_ttr_format(self) -> None:
        self.assertEqual(_rd_ttr(None, NOW), "")
        self.assertEqual(_rd_ttr(NOW + 30, NOW), "<1m")
        self.assertEqual(_rd_ttr(NOW + 45 * 60, NOW), "45m")
        self.assertEqual(_rd_ttr(NOW + 2 * 3600 + 14 * 60, NOW), "2h14m")
        self.assertEqual(_rd_ttr(NOW + 3 * 3600, NOW), "3h")
        self.assertEqual(_rd_ttr(NOW + 3 * 86400 + 4 * 3600, NOW), "3d4h")
        self.assertEqual(_rd_ttr(NOW + 2 * 86400, NOW), "2d")
        self.assertEqual(_rd_ttr(NOW - 5, NOW), "now")

    def test_masthead_shows_countdown_and_sheds_it_before_accounts(self) -> None:
        done = Worker("w", DONE, start=NOW - 7300, end=NOW - 7200)
        stage = station("/a", "alpha", [done], last=NOW - 7200, lead=LeadInfo("opus", "build"))
        quota = [QuotaAccount("claude", [QuotaWindow("5h", 62.0, None, window="five_hour",
                                                     resets_at=NOW + 2 * 3600 + 14 * 60)]),
                 QuotaAccount("codex", [QuotaWindow("Week", 100.0, None, window="seven_day",
                                                    resets_at=NOW + 3 * 86400 + 4 * 3600)])]
        gs = GlobalSnapshot(now=NOW, stages=[stage], quota=quota, quota_ok=True)
        line = next(ln for ln in plain(render_global(gs, UIState(), 100, 30, NOW)) if "claude" in ln)
        self.assertIn("codex 100% 3d4h", line)
        self.assertIn("claude 62% 2h14m", line)
        narrow = next(ln for ln in plain(render_global(gs, UIState(), 30, 30, NOW)) if "claude" in ln)
        self.assertIn("codex 100%", narrow)      # the account % survives ...
        self.assertIn("claude 62%", narrow)
        self.assertNotIn("3d4h", narrow)         # ... the countdowns go first
        self.assertNotIn("2h14m", narrow)

    def test_the_old_workers_banner_is_gone(self) -> None:
        def text(state: str, solo: bool = False, lead_state: str = "live") -> str:
            st = Stage("/a/.stage#1", "alpha", Snapshot(now=NOW), 0, NOW - 30, LeadInfo("opus"), lead_state=lead_state, solo=solo)
            st.state = state
            return "\n".join(plain(render_global(GlobalSnapshot(now=NOW, stages=[st]), UIState(), 100, 30, NOW)))
        for state in ("active", "idle", "closed"):
            self.assertNotIn("Workers be working", text(state))
            self.assertNotIn("All quiet.", text(state))

    def test_errors_and_dismissed_count_are_visible(self) -> None:
        gs = GlobalSnapshot(now=NOW, dismissed=3, hidden_idle=51, errors=["RuntimeError: broken"], stages=[])
        frame = plain(render_global(gs, UIState(), 80, 8, NOW))
        self.assertIn("RuntimeError: broken", "\n".join(frame))
        self.assertNotIn("idle sessions hidden", "\n".join(frame))
        self.assertIn("3 di", "\n".join(frame))   # the count rides on the closed line, cut to the width
        output = render_global_plain(gs, 100)
        self.assertIn("RuntimeError: broken", output)
        self.assertIn("3 dismissed", output)


class OverviewMarks(unittest.TestCase):
    """A station with live workers breathes: its ◆ mark changes colour over time. An idle station's ○ does not."""

    def mark_colour(self, gs: GlobalSnapshot, label: str, t: float) -> str:
        frame = render_global(gs, UIState(gsel=99, gopen={("", "more", "finished:/b")}), 100, 30, t)
        line = next(ln for ln in frame if re.search(rf"[◆◇◐] {label}", _wd_SGR_RE.sub("", ln)))
        return re.search(r"\x1b\[([0-9;]*)m[◆◇◐]", line).group(1)

    def test_live_station_mark_breathes(self) -> None:
        hot = Worker("hot", RUNNING, start=NOW - 60, quiet_s=2.0, family="codex")
        gs = GlobalSnapshot(now=NOW, stages=[station("/a", "alpha", [hot])])
        colours = {self.mark_colour(gs, "alpha", NOW + k * 0.2) for k in range(8)}
        self.assertGreater(len(colours), 2)

    def test_idle_station_mark_is_still(self) -> None:
        done = Worker("d", DONE, start=NOW - 90, end=NOW - 5)
        gs = GlobalSnapshot(now=NOW, stages=[station("/b", "beta", [done])])
        colours = {self.mark_colour(gs, "beta", NOW + k * 0.2) for k in range(8)}
        self.assertEqual(len(colours), 1)


class SoloRows(unittest.TestCase):
    """A solo station: a lead is running and has delegated nothing."""

    def solo_gs(self) -> GlobalSnapshot:
        st = station("/s", "solo-proj", [], lead=LeadInfo("opus"))
        st.lead_state, st.solo = "live", True
        return GlobalSnapshot(now=NOW, stages=[st])

    def test_solo_row_shows_solo_mark_without_zero_counts(self) -> None:
        out = "\n".join(plain(render_global(self.solo_gs(), UIState(), 100, 30, NOW)))
        line = next(ln for ln in out.splitlines() if "solo-proj" in ln and "◇" in ln)
        self.assertIn("◇", line)
        self.assertIn("◇", line)
        self.assertNotIn("0 live", line)

    def test_solo_mark_is_teal(self) -> None:
        teal = "38;2;{};{};{}".format(*widgets._wd_C_TEAL)
        frame = render_global(self.solo_gs(), UIState(), 100, 30, NOW)
        line = next(ln for ln in frame if "solo-proj" in ln and "◇" in ln)
        self.assertRegex(line, rf"\x1b\[{teal}m◇")

    def test_legend_has_the_solo_entry(self) -> None:
        out = "\n".join(plain(render_global(self.solo_gs(), UIState(glegend=True), 120, 40, NOW)))
        self.assertIn("◆ team  ◇ solo", out)

    def test_plain_output_marks_solo(self) -> None:
        out = render_global_plain(self.solo_gs())
        self.assertIn("solo-proj   live   solo", out)

    def test_new_lead_kinds_have_names(self) -> None:
        from render import _rd_LEAD_NAMES
        self.assertEqual(_rd_LEAD_NAMES["codex"], "Codex")
        self.assertEqual(_rd_LEAD_NAMES["claude work"], "Claude Code (work)")


if __name__ == "__main__":
    unittest.main()
