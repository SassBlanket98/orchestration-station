"""R7: the global console tree: one project per root, the idle divider, finished folds, fill, swarms and keys."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import app
import render
from model import (
    DONE,
    RUNNING,
    GlobalSnapshot,
    Group,
    LeadInfo,
    LogState,
    Snapshot,
    Stage,
    Step,
    UIState,
    Worker,
    WorkState,
)
from render import (
    _rd_g_fill,
    _rd_g_nodes,
    _rd_g_swarm_workers,
    global_rows,
    render_global,
)
from widgets import _wd_SGR_RE, _wd_width

NOW = 1_800_000_000.0


def plain(frame: list[str]) -> list[str]:
    return [_wd_SGR_RE.sub("", line) for line in frame]


def stage(path: str, label: str, workers: list[Worker], *, state: str = "active", name: str = "",
          root: str = "", cluster: str = "", session_id: str = "", last: float = NOW - 60,
          work: WorkState | None = None, steps: list[Step] | None = None) -> Stage:
    snap = Snapshot(now=NOW, workers=workers)
    st = Stage(path, label, snap, sum(wk.state == RUNNING for wk in workers), last, LeadInfo("opus"))
    st.state, st.lead_state = state, "live" if state != "closed" else "gone"
    st.session_name, st.tag, st.lead_model, st.effort = name, "CC·W", "sonnet", "medium"
    st.dir, st.cluster, st.session_id, st.work = root, cluster, session_id, work
    st.activity = last
    if steps:
        for wk in workers:
            wk.log.steps = list(steps)
            wk.log.last_ts = NOW - 5
    return st


def three_workers() -> tuple[list[Worker], Group]:
    workers = [Worker("w0", RUNNING, start=NOW - 100), Worker("w1", RUNNING, start=NOW - 90),
               Worker("w2", DONE, start=NOW - 80, end=NOW - 10)]
    group = Group(label="build", workers=[wk.name for wk in workers], started=NOW - 100, done=1, total=3,
                  live=2, current=True)
    return workers, group


class ProjectTree(unittest.TestCase):
    def test_live_and_idle_sessions_share_one_project_heading(self) -> None:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10)], state="active",
                     name="live one", root="/p")
        idle = stage("/p/.stage/b", "proj", [], state="idle", name="idle one", root="/p", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[live, idle])
        rows = global_rows(gs, UIState())
        self.assertEqual([r for r in rows if r[1] == "group"], [("", "group", "p:/p")])
        self.assertIn(("/p/.stage/a", "stage", ""), rows)
        self.assertIn(("/p/.stage/b", "stage", ""), rows)   # an idle session of a live project stays listed
        text = "\n".join(plain(render_global(gs, UIState(), 120, 40, NOW)))
        self.assertEqual(text.count("proj  ─"), 1)
        self.assertIn("1 live · 1 idle", text)

    def test_idle_only_projects_are_collapsed_under_the_divider(self) -> None:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10)], state="active", root="/p")
        idle = stage("/q/.stage/a", "quietproj", [], state="idle", root="/q", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[live, idle])
        rows = global_rows(gs, UIState())
        divider = rows.index(("", "idle", ""))
        group = rows.index(("", "group", "p:/q"))
        self.assertLess(divider, group)
        self.assertNotIn(("/q/.stage/a", "stage", ""), rows)   # shut until the user opens it or the fill shows it
        opened = UIState(gopen_keys={"p:/q"})
        self.assertIn(("/q/.stage/a", "stage", ""), global_rows(gs, opened))

    def test_finished_line_is_the_last_child_of_its_project(self) -> None:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10)], state="active", root="/p")
        done = stage("/p/.stage/b", "proj", [Worker("d", DONE, start=NOW - 90, end=NOW - 60)], state="closed",
                     name="old work", root="/p", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[live, done])
        nodes = _rd_g_nodes(gs, UIState())
        self.assertEqual((nodes[-1].kind, nodes[-1].name), ("more", "finished:/p"))
        self.assertNotIn(("/p/.stage/b", "stage", ""), global_rows(gs, UIState()))

    def test_closed_count_line_replaces_the_old_footer_and_carries_dismissed(self) -> None:
        closed = [stage(f"/c{i}", f"c{i}", [], state="closed", root=f"/c{i}", last=NOW - 3 * 3600,
                        work=WorkState(dirty=(i == 0))) for i in range(30)]
        gs = GlobalSnapshot(now=NOW, stages=closed, dismissed=2, hidden_idle=5)
        text = "\n".join(plain(render_global(gs, UIState(), 140, 40, NOW)))
        self.assertIn("30 closed · 1 need sweep", text)
        self.assertIn("2 dismissed", text)
        self.assertNotIn("idle sessions hidden", text)
        self.assertNotIn("a shows them", text)

    def test_crons_are_plain_openclaw_sessions_after_the_chats(self) -> None:
        chat = stage("/openclaw/.stage/chat", "Bot", [], state="idle", name="dashboard chat", root="/openclaw",
                     last=NOW - 100)
        cron = stage("/openclaw/.stage/cron", "Bot", [], state="idle", name="morning brief", root="/openclaw",
                     cluster="cron", last=NOW - 90)
        cron.level_tag = "CRON"
        gs = GlobalSnapshot(now=NOW, stages=[chat, cron])
        rows = global_rows(gs, UIState(gopen_keys={"p:/openclaw"}))
        self.assertEqual([r for r in rows if r[1] == "group"], [("", "group", "p:/openclaw")])
        self.assertEqual([r[0] for r in rows if r[1] == "stage"], ["/openclaw/.stage/chat", "/openclaw/.stage/cron"])
        text = "\n".join(plain(render_global(gs, UIState(gopen_keys={"p:/openclaw"}), 120, 40, NOW)))
        self.assertIn("CRON", text)
        self.assertNotIn("·cron", text)
        self.assertNotIn("cron ·", text)


class SwarmBand(unittest.TestCase):
    def gs(self, workers: list[Worker], group: Group | None) -> GlobalSnapshot:
        st = stage("/p/.stage/a", "proj", workers, state="active", name="lead", root="/p", session_id="sid")
        st.snap.groups = [group] if group else []
        return GlobalSnapshot(now=NOW, stages=[st])

    def test_band_row_appears_at_three_workers_and_not_at_two(self) -> None:
        workers, group = three_workers()
        gs = self.gs(workers, group)
        with patch.object(render, "_og_swarm_band", return_value="BAND"):
            frame = render_global(gs, UIState(), 120, 40, NOW)
        self.assertTrue(any("BAND" in line for line in plain(frame)))
        two = self.gs(workers[:2], Group("build", ["w0", "w1"], NOW - 100, 0, 2, 1, current=True))
        with patch.object(render, "_og_swarm_band", return_value="BAND"):
            frame = render_global(two, UIState(), 120, 40, NOW)
        self.assertFalse(any("BAND" in line for line in plain(frame)))
        session_node = next(n for n in _rd_g_nodes(gs, UIState()) if n.kind == "stage")
        self.assertEqual(len(_rd_g_swarm_workers(session_node.row)), 3)

    def test_band_row_is_exactly_cols_wide(self) -> None:
        workers, group = three_workers()
        gs = self.gs(workers, group)
        with patch.object(render, "_og_swarm_band", return_value="BAND"):
            for cols in (80, 120, 160):
                frame = render_global(gs, UIState(), cols, 40, NOW)
                band = next(line for line in frame if "BAND" in plain([line])[0])
                self.assertEqual(_wd_width(band), cols)

    def test_folded_swarm_session_shows_swarm_on_its_one_line(self) -> None:
        workers, group = three_workers()
        gs = self.gs(workers, group)
        frame = render_global(gs, UIState(gshut_keys={"s:sid"}), 140, 40, NOW)
        line = next(ln for ln in plain(frame) if "lead" in ln)
        self.assertIn("SWARM", line)
        self.assertNotIn("w0", "\n".join(plain(frame)))

    def test_animation_keeps_running_while_a_swarm_is_shown(self) -> None:
        workers, group = three_workers()
        gs = self.gs(workers, group)
        self.assertTrue(app._ap_animating(gs, UIState(), NOW))


class Fill(unittest.TestCase):
    def fixture(self) -> GlobalSnapshot:
        steps = [Step(NOW - 30 - i, "tool", "read", f"step text {i}", dur=0.5, outcome="ok") for i in range(3)]
        live = stage("/live/.stage/a", "liveproj", [Worker("w", RUNNING, start=NOW - 100)], state="active",
                     name="live sess", root="/live", steps=steps)
        idle = stage("/idle/.stage/a", "idleproj", [], state="idle", name="idle sess", root="/idle", last=NOW - 600)
        done = stage("/done/.stage/a", "doneproj", [Worker("d", DONE, start=NOW - 200, end=NOW - 100)],
                     state="closed", name="done sess", root="/done", last=NOW - 600)
        return GlobalSnapshot(now=NOW, stages=[live, idle, done])

    def test_fill_spends_rows_in_order_steps_then_finished(self) -> None:
        gs = self.fixture()
        nodes = _rd_g_nodes(gs, UIState())
        plan = render._og_plan(120)
        first, _, _ = _rd_g_fill(gs, UIState(), nodes, plan, 120, 1, NOW, NOW,
                                 render._rd_g_session_names(gs.stages), render._rd_g_short_labels(gs.stages))
        self.assertTrue(first)
        worker = next(i for i, n in enumerate(nodes) if n.kind == "worker")
        self.assertIn(worker, first)                       # (1) a step under the live worker
        second, opened, _ = _rd_g_fill(gs, UIState(), nodes, plan, 120, 6, NOW, NOW,
                                       render._rd_g_session_names(gs.stages), render._rd_g_short_labels(gs.stages))
        idle_group = next(i for i, n in enumerate(nodes) if n.kind == "group" and n.name == "p:/idle")
        self.assertNotIn(idle_group, second)               # an idle project never opens by itself (the user 2026-10-24)
        self.assertNotIn("/idle", opened)
        third, _, _ = _rd_g_fill(gs, UIState(), nodes, plan, 120, 12, NOW, NOW,
                                 render._rd_g_session_names(gs.stages), render._rd_g_short_labels(gs.stages))
        finished = next(i for i, n in enumerate(nodes) if n.kind == "more" and n.name == "finished:/done")
        self.assertIn(finished, third)                     # (2) the recent-finished list

    def test_fill_writes_no_expand_keys_and_no_selectable_rows(self) -> None:
        gs = self.fixture()
        ui = UIState()
        before = (set(ui.gopen_keys), set(ui.gshut_keys), set(ui.gopen))
        frame = render_global(gs, ui, 120, 50, NOW)
        self.assertEqual((set(ui.gopen_keys), set(ui.gshut_keys), set(ui.gopen)), before)
        self.assertIn("step text 2", "\n".join(plain(frame)))      # fill row
        self.assertNotIn("idle sess", "\n".join(plain(frame)))     # an idle project stays shut
        self.assertNotIn(("/idle/.stage/a", "stage", ""), global_rows(gs, ui))

    def test_fill_rows_have_no_click_target(self) -> None:
        gs = self.fixture()
        ui = UIState()
        frame = plain(render_global(gs, ui, 120, 50, NOW))
        row = next(i for i, line in enumerate(frame) if "09:59:28" in line)   # a fill row, not the worker's own NOW line
        navs = [hit for hit in ui.hits if hit[4] == "nav" and hit[0] <= row + 1 <= hit[1]]   # hits are 1-based
        self.assertEqual(navs, [])

    def test_fill_gives_rows_back_as_the_list_grows(self) -> None:
        gs = self.fixture()
        tall = "\n".join(plain(render_global(gs, UIState(), 120, 50, NOW))).count("step text")
        short = "\n".join(plain(render_global(gs, UIState(), 120, 22, NOW))).count("step text")
        self.assertGreaterEqual(tall, short)
        grown = GlobalSnapshot(now=NOW, stages=list(gs.stages) + [
            stage(f"/more{i}/.stage/a", f"more{i}", [Worker(f"m{i}", RUNNING, start=NOW - 5)], state="active",
                  root=f"/more{i}") for i in range(6)])
        crowded = "\n".join(plain(render_global(grown, UIState(), 120, 50, NOW))).count("step text")
        self.assertLessEqual(crowded, tall)


class FrameShape(unittest.TestCase):
    def fixture(self) -> GlobalSnapshot:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10)], state="active",
                     name="live sess", root="/p", session_id="sid")
        idle = stage("/q/.stage/a", "quietproj", [], state="idle", name="idle sess", root="/q", last=NOW - 600)
        done = stage("/r/.stage/a", "doneproj", [], state="closed", name="done sess", root="/r", last=NOW - 600)
        return GlobalSnapshot(now=NOW, stages=[live, idle, done])

    def test_every_line_is_exactly_cols_wide(self) -> None:
        gs = self.fixture()
        for cols in (80, 100, 120, 150, 160, 200):
            frame = render_global(gs, UIState(), cols, 40, NOW)
            self.assertTrue(all(_wd_width(line) == cols for line in frame), cols)

    def test_column_header_and_cell_columns_align_on_a_real_row(self) -> None:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10, log=LogState(tokens_in=1000,
                                                                                               cost=1.5))],
                     state="active", name="live sess", root="/p")
        live.lead_cost, live.lead_tokens = 1.5, 12_000
        gs = GlobalSnapshot(now=NOW, stages=[live])
        lines = plain(render_global(gs, UIState(), 120, 30, NOW))
        head = next(line for line in lines if "SESSION" in line and "ELAPSED" in line)
        row = next(line for line in lines if "live sess" in line)
        cost_end = head.index("COST") + len("COST")
        elapsed_end = head.index("ELAPSED") + len("ELAPSED")
        self.assertNotEqual(row[cost_end - 1], " ")
        self.assertNotEqual(row[elapsed_end - 1], " ")

    def test_no_color_frame_is_stable_text(self) -> None:
        gs = self.fixture()
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            first = render_global(gs, UIState(), 120, 40, NOW)
            second = render_global(gs, UIState(), 120, 40, NOW)
        self.assertEqual(first, second)
        self.assertFalse(any("\x1b[" in line for line in first))

    def test_phone_mode_keeps_its_bottom_bar(self) -> None:
        gs = self.fixture()
        frame = "\n".join(plain(render_global(gs, UIState(), 80, 30, NOW)))
        self.assertIn("Open", frame)
        self.assertIn("Close", frame)


class Keys(unittest.TestCase):
    def test_e_opens_and_c_shuts_every_project_and_session(self) -> None:
        live = stage("/p/.stage/a", "proj", [Worker("w", RUNNING, start=NOW - 10)], state="active", root="/p",
                     session_id="live")
        idle = stage("/q/.stage/a", "quietproj", [], state="idle", root="/q", session_id="idle", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[live, idle])
        ui = UIState()
        app._ap_global_key(ui, "e", gs, 60)
        self.assertIn("p:/q", ui.gopen_keys)
        self.assertIn("s:idle", ui.gopen_keys)
        app._ap_global_key(ui, "c", gs, 60)
        self.assertIn("p:/q", ui.gshut_keys)
        self.assertIn("s:idle", ui.gshut_keys)
        self.assertIn("p:/p", ui.gshut_keys)
        self.assertIn("s:live", ui.gshut_keys)

    def test_enter_and_arrows_toggle_the_one_project_key(self) -> None:
        idle = stage("/q/.stage/a", "quietproj", [], state="idle", root="/q", session_id="idle", last=NOW - 600)
        gs = GlobalSnapshot(now=NOW, stages=[idle])
        ui = UIState(gsel=global_rows(gs, UIState()).index(("", "group", "p:/q")))
        app._ap_global_key(ui, "enter", gs, 60)
        self.assertIn("p:/q", ui.gopen_keys)
        app._ap_global_key(ui, "left", gs, 60)
        self.assertIn("p:/q", ui.gshut_keys)


if __name__ == "__main__":
    unittest.main()
