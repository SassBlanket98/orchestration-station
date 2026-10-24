"""monitor global tree: masthead, filter chips, row labels, plain output, footers, and group headings."""
from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from model import (
    FAILED,
    QUIET,
    RUNNING,
    Finding,
    GlobalSnapshot,
    LeadInfo,
    Review,
    Snapshot,
    Stage,
    UIState,
    Worker,
)
from render import (
    _rd_g_buckets,
    _rd_g_chips,
    _rd_g_group_info,
    _rd_g_rowdata,
    _rd_g_rows,
    _rd_g_session_names,
    _rd_g_short_labels,
    render_global,
    render_global_plain,
)
from widgets import _wd_SGR_RE

NOW = 1_760_050_000.0
HOME = "/home/u/Projects"
WIDTHS = (100, 46)


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


def chips(gs: GlobalSnapshot, ui: UIState, cols: int = 100) -> str:
    return _wd_SGR_RE.sub("", _rd_g_chips(gs, ui, cols, 0))


def masthead_stats(frame: list[str]) -> str:
    return next(line for line in frame if "working" in line and "problem" in line)


def session(path: str, label: str, workers: list[Worker], *, state: str = "active", name: str = "",
            tag: str = "CC·W", solo: bool = False, last: float = NOW - 30, lead_state: str = "live",
            unlinked: bool = False, session_id: str = "", **snap_kw) -> Stage:
    st = Stage(path, label, Snapshot(now=NOW, workers=workers, **snap_kw), sum(w.state == RUNNING for w in workers),
               last, LeadInfo("opus", "build"))
    st.state, st.lead_state, st.solo, st.unlinked = state, lead_state, solo, unlinked
    st.session_name, st.tag, st.lead_model, st.effort = name, tag, "sonnet", "medium"
    st.session_id = session_id
    return st


def tree_fixture() -> GlobalSnapshot:
    root = f"{HOME}/sample-project"
    stages = [
        session(f"{root}#run", "sample-project", [Worker("runner", RUNNING, start=NOW - 40)],
                state="active", name="Ship feature", tag="CC·W", lead_state="idle"),
        session(f"{root}#lead", "sample-project", [], state="active", solo=True, name="Lead desk", tag="CC·W",
                lead_state="live"),
        session(f"{root}#idle", "sample-project", [], state="idle", name="Old plan", tag="CC·W",
                lead_state="live", last=NOW - 900),
        session("/other/lag", "lagproj", [Worker("quiet", QUIET, start=NOW - 300, quiet_s=120)],
                state="active", tag="CDX", lead_state="idle"),
        session("/bad/fail", "failproj", [Worker("boom", FAILED, start=NOW - 60, end=NOW - 1)],
                state="active", lead_state="idle",
                reviews=[Review("r", findings=[Finding(1, "blocker")])]),
    ]
    return GlobalSnapshot(now=NOW, stages=stages, hidden_idle=2, dismissed=1)


def parse_masthead(line: str) -> dict[str, int]:
    out: dict[str, int] = {}
    if m := re.search(r"(\d+) live", line):
        out["active"] = int(m.group(1))
    if m := re.search(r"(\d+) idle", line):
        out["idle"] = int(m.group(1))
    if m := re.search(r"(\d+) workers?", line):
        out["workers"] = int(m.group(1))
    if m := re.search(r"(\d+) working", line):
        out["working"] = int(m.group(1))
    if m := re.search(r"(\d+) lagging", line):
        out["lagging"] = int(m.group(1))
    if m := re.search(r"(\d+) problem", line):
        out["problems"] = int(m.group(1))
    return out


def chip_count(chip_line: str, name: str) -> int:
    labels = {"all": ("all", "all"), "working": ("working", "work"), "lagging": ("lagging", "lag"),
              "problems": ("problems", "prob")}
    flt, short = labels[name]
    i = ("all", "working", "lagging", "problems").index(name) + 1
    for label in (flt, short):
        if m := re.search(rf"{i}\s+{label}\s+(\d+)", chip_line):
            return int(m.group(1))
    raise AssertionError(f"no {name} chip in {chip_line!r}")


class EnvPatch(unittest.TestCase):
    def setUp(self) -> None:
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)


class MastheadAndChips(EnvPatch):
    """Masthead metrics and filter chips at 100 and 46 columns."""

    def test_masthead_counts_at_both_widths(self) -> None:
        gs = tree_fixture()
        expect = {"active": 4, "idle": 1, "working": 2, "lagging": 1, "problems": 1}   # sessions with a problem: the same unit as the problems chip
        for cols in WIDTHS:
            head = parse_masthead(masthead_stats(plain(render_global(gs, UIState(), cols, 30, NOW))))
            for key, val in expect.items():
                if cols < 60 and key in ("active", "idle", "lagging"):
                    continue   # narrow masthead sheds the quieter totals first
                self.assertEqual(head.get(key), val, (cols, key, head))

    def test_chip_counts_match_rows_without_query(self) -> None:
        gs = tree_fixture()
        ui = UIState()
        for cols in (c for c in WIDTHS if c > 50):   # a phone drops the chip counts so the filter box fits
            line = chips(gs, ui, cols)
            self.assertEqual(chip_count(line, "all"), 5)
            self.assertEqual(chip_count(line, "working"), 2)
            self.assertEqual(chip_count(line, "lagging"), 1)
            self.assertEqual(chip_count(line, "problems"), 1)

    def test_chip_counts_respect_text_query(self) -> None:
        gs = tree_fixture()
        for cols in (c for c in WIDTHS if c > 50):
            wide = chips(gs, UIState(), cols)
            narrow = chips(gs, UIState(gquery="lagproj"), cols)
            self.assertEqual(chip_count(wide, "all"), 5)
            self.assertEqual(chip_count(narrow, "all"), 1)
            self.assertEqual(chip_count(narrow, "working"), 0)
            self.assertEqual(chip_count(narrow, "lagging"), 1)


class RowLabels(EnvPatch):
    def test_short_label_stable_when_sibling_sessions_appear_or_vanish(self) -> None:
        root = f"{HOME}/app"
        a = Stage(f"{root}#a", "app", Snapshot(now=NOW), dir=root)
        b = Stage(f"{root}#b", "app", Snapshot(now=NOW), dir=root)
        alone = _rd_g_short_labels([a])[root]
        both = _rd_g_short_labels([a, b])[root]
        only_b = _rd_g_short_labels([b])[root]
        self.assertEqual(alone, both)
        self.assertEqual(only_b, both)

    def test_rendered_project_label_unchanged_across_sibling_set(self) -> None:
        root = f"{HOME}/sample-project"
        keep = session(f"{root}#keep", "sample-project", [Worker("w", RUNNING, start=NOW - 5)],
                       state="active", name="keeper")
        extra = session(f"{root}#extra", "sample-project", [], state="idle", name="extra", last=NOW - 50)
        label_alone = _rd_g_short_labels([keep])[root]
        short_both = _rd_g_short_labels([keep, extra])
        self.assertEqual(label_alone, short_both[root])
        self.assertEqual(label_alone, "sample-project")

    def test_two_sessions_in_one_repo_show_distinct_session_names(self) -> None:
        root = f"{HOME}/app"
        a = session(f"{root}#a", "app", [], state="active", name="Plan", session_id="aaaa1111")
        b = session(f"{root}#b", "app", [], state="active", name="Plan", session_id="bbbb2222")
        names = _rd_g_session_names([a, b])
        self.assertEqual(names[a.path], "Plan #aaaa")
        self.assertEqual(names[b.path], "Plan #bbbb")
        text = "\n".join(plain(render_global(GlobalSnapshot(now=NOW, stages=[a, b]), UIState(), 100, 24, NOW)))
        self.assertIn("Plan #aaaa", text)
        self.assertIn("Plan #bbbb", text)


class PlainAndFooter(EnvPatch):
    def test_plain_working_matches_masthead_at_both_widths(self) -> None:
        gs = tree_fixture()
        for cols in WIDTHS:
            head = parse_masthead(masthead_stats(plain(render_global(gs, UIState(), cols, 30, NOW))))
            plain_head = render_global_plain(gs, cols).splitlines()[0]
            plain_working = int(re.search(r"(\d+) working", plain_head).group(1))
            self.assertEqual(head["working"], plain_working)

    def test_hidden_idle_footer_is_gone_and_dismissed_rides_the_closed_line(self) -> None:
        gs = tree_fixture()
        wide = "\n".join(plain(render_global(gs, UIState(), 100, 50, NOW)))
        self.assertNotIn("idle sessions hidden", wide)
        self.assertIn("1 dismissed", wide)
        self.assertIn("closed · ", render_global_plain(gs, 100))
        self.assertIn("1 dismissed", render_global_plain(gs, 100))

    def test_empty_snapshot_message(self) -> None:
        gs = GlobalSnapshot(now=NOW)
        for cols in WIDTHS:
            frame = "\n".join(plain(render_global(gs, UIState(), cols, 16, NOW)))
            self.assertIn("no orchestrators with recent activity", frame)
        self.assertRegex(render_global_plain(gs, 100).splitlines()[0], r"◆ Station   0 working   0 sessions")


class GroupHeadings(EnvPatch):
    def test_idle_project_headings_match_rows(self) -> None:
        stages = []
        for i in range(6):
            s = session(f"/idle/s{i}", f"s{i}", [], state="idle", tag="CC·W", lead_state="live", last=NOW - i * 10)
            stages.append(s)
        gs = GlobalSnapshot(now=NOW, stages=stages)
        ui = UIState()
        rows = [r for r in _rd_g_rows(gs, ui) if r.section == "idle"]
        self.assertEqual(len(rows), 6)
        info = _rd_g_group_info(ui, rows)
        self.assertEqual(len(info), 6)
        self.assertEqual(info["p:/idle/s0"], ("s0", "1 idle"))
        text = "\n".join(plain(render_global(gs, ui, 100, 40, NOW)))
        self.assertRegex(text, r"idle · 6")
        heading = next(line for line in text.splitlines() if "s0" in line and "1 idle" in line)
        self.assertIn("s0", heading)

    def test_rendered_rows_carry_expected_project_names(self) -> None:
        gs = tree_fixture()
        text = "\n".join(plain(render_global(gs, UIState(), 100, 50, NOW)))
        self.assertIn("sample-project", text)
        self.assertIn("lagproj", text)
        self.assertIn("failproj", text)

    def test_empty_root_label_buckets_without_error(self) -> None:
        stage = Stage("", "", Snapshot(now=NOW), 0, NOW - 5, LeadInfo("opus"))
        stage.state = "active"
        row = _rd_g_rowdata(stage)
        buckets = _rd_g_buckets("active", [row])
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0][0], "p:")
        text = "\n".join(plain(render_global(GlobalSnapshot(now=NOW, stages=[stage]), UIState(), 100, 30, NOW)))
        self.assertNotIn("render error", text)


if __name__ == "__main__":
    unittest.main()
