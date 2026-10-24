from __future__ import annotations

import fcntl
import io
import os
import pty
import select
import signal
import struct
import tempfile
import termios
import time
import unittest
import unittest.mock
from contextlib import redirect_stdout

import app
from model import (
    DONE,
    FAILED,
    NOREPORT,
    QUIET,
    RUNNING,
    STALLED,
    Finding,
    GlobalSnapshot,
    Review,
    Snapshot,
    Stage,
    UIState,
    Worker,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def snap_of(*workers: Worker, reviews: list[Review] | None = None) -> Snapshot:
    return Snapshot(workers=list(workers), reviews=reviews or [])


def ws(*names: str) -> Snapshot:
    return snap_of(*(Worker(n, RUNNING) for n in names))


class ParseKeys(unittest.TestCase):
    def test_allow_actions_flag_defaults_off(self) -> None:
        self.assertFalse(app._ap_parser().parse_args([]).allow_actions)
        self.assertTrue(app._ap_parser().parse_args(["--allow-actions"]).allow_actions)

    def test_parsed_allow_actions_reaches_ui_state(self) -> None:
        args = app._ap_parser().parse_args(["--allow-actions"])
        ui = app._ap_ui_from_args(args, "full")
        self.assertTrue(ui.allow_actions)

    def test_classic_close_dismisses_a_gone_row_when_actions_are_disabled(self) -> None:
        stage = Stage(path="/stage/one", label="one", lead_pid=1234, lead_state="live")
        gs = GlobalSnapshot(stages=[stage])
        ui = UIState()
        gc = unittest.mock.Mock()
        with unittest.mock.patch.object(app, "global_rows", return_value=[("/stage/one", "one", "stage")]):
            app._ap_close_selected(ui, gs, gc)
        self.assertIsNone(ui.close_ask)
        gc.dismiss.assert_not_called()

    def test_classic_close_of_gone_row_dismisses_without_actions(self) -> None:
        stage = Stage(path="/stage/one", label="one", lead_state="gone", live=0)
        gs = GlobalSnapshot(stages=[stage])
        ui = UIState()
        gc = unittest.mock.Mock()
        with unittest.mock.patch.object(app, "global_rows", return_value=[("/stage/one", "one", "stage")]):
            app._ap_close_selected(ui, gs, gc)
        gc.dismiss.assert_called_once_with("/stage/one")

    def test_classic_close_of_background_without_pid_dismisses_without_actions(self) -> None:
        stage = Stage(path="/stage/one", label="one", lead_state="background", live=0)
        gs = GlobalSnapshot(stages=[stage])
        ui = UIState()
        gc = unittest.mock.Mock()
        with unittest.mock.patch.object(app, "global_rows", return_value=[("/stage/one", "one", "stage")]):
            app._ap_close_selected(ui, gs, gc)
        gc.dismiss.assert_called_once_with("/stage/one")

    def test_classic_close_of_live_lead_is_blocked_with_note_when_actions_are_disabled(self) -> None:
        stage = Stage(path="/stage/one", label="one", lead_pid=1234, lead_state="live")
        gs = GlobalSnapshot(stages=[stage])
        ui = UIState()
        gc = unittest.mock.Mock()
        with unittest.mock.patch.object(app, "global_rows", return_value=[("/stage/one", "one", "stage")]):
            app._ap_close_selected(ui, gs, gc)
        self.assertIsNone(ui.close_ask)
        self.assertEqual(ui.note, "ending a session needs --allow-actions")
        gc.dismiss.assert_not_called()

    def test_end_lead_is_blocked_with_note_when_actions_are_disabled(self) -> None:
        ui = UIState(close_ask=("/stage/one", 1234))
        gc = unittest.mock.Mock()
        with unittest.mock.patch.object(app.os, "kill") as kill:
            app._ap_end_lead(ui, gc)
        self.assertIsNone(ui.close_ask)
        self.assertEqual(ui.note, "ending a session needs --allow-actions")
        gc.current_lead.assert_not_called()
        kill.assert_not_called()

    def test_allow_actions_help_explains_the_process_action_gate(self) -> None:
        help_text = app._ap_parser().format_help()
        self.assertIn("--allow-actions", help_text)
        self.assertIn("ending sessions", help_text)

    def test_main_passes_ui_built_from_args_into_interactive_modes(self) -> None:
        with tempfile.TemporaryDirectory() as stage_dir:
            for argv in (["--global", "--allow-actions"], ["--stage", stage_dir, "--allow-actions"]):
                with self.subTest(argv=argv), unittest.mock.patch.object(app.sys.stdout, "isatty", return_value=True), \
                        unittest.mock.patch.object(app.sys.stdin, "isatty", return_value=True), \
                        unittest.mock.patch.dict(os.environ, {"TERM": "xterm"}), \
                        unittest.mock.patch.object(app, "_ap_interactive", return_value=0) as interactive, \
                        unittest.mock.patch.object(app, "GlobalCollector") as global_collector, \
                        unittest.mock.patch.object(app, "Collector") as collector, \
                        unittest.mock.patch.object(app, "_ap_load_density", return_value="full"), \
                        unittest.mock.patch.object(app, "_ap_plain_size", return_value=None):
                    self.assertEqual(app.main(list(argv)), 0)
                    self.assertTrue(interactive.call_args.kwargs["ui"].allow_actions)
                    if argv[0] == "--global":
                        global_collector.assert_called_once()
                    else:
                        collector.assert_called_once()

    def test_sequences(self) -> None:
        cases = {
            b"\x1b[A": "up", b"\x1b[B": "down", b"\x1b[C": "right", b"\x1b[D": "left",
            b"\x1b[5~": "pgup", b"\x1b[6~": "pgdn", b"\x1b[H": "home", b"\x1b[F": "end",
            b"\x1b[1~": "home", b"\x1b[4~": "end", b"\x1b[Z": "btab",
            b"\x1bOA": "up", b"\x1bOB": "down", b"\x1bOC": "right", b"\x1bOD": "left",
            b"\x1bOH": "home", b"\x1bOF": "end", b"\x1b[1;2A": "up",
            b"\r": "enter", b"\n": "enter", b"\t": "tab", b"\x7f": "backspace", b"\x03": "ctrl-c",
            b"q": "q", b"G": "G", b"j": "j",
        }
        for raw, name in cases.items():
            self.assertEqual(app._ap_parse_keys(raw), ([name], b""), raw)

    def test_lone_escape(self) -> None:
        self.assertEqual(app._ap_parse_keys(b"\x1b"), ([], b"\x1b"))
        self.assertEqual(app._ap_parse_keys(b"\x1b", final=True), (["esc"], b""))

    def test_two_keys_in_one_read(self) -> None:
        self.assertEqual(app._ap_parse_keys(b"j\x1b[Bq"), (["j", "down", "q"], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b\x1b[A"), (["esc", "up"], b""))

    def test_split_sequence(self) -> None:
        keys, rest = app._ap_parse_keys(b"j\x1b[")
        self.assertEqual((keys, rest), (["j"], b"\x1b["))
        keys2, rest2 = app._ap_parse_keys(rest + b"6~")
        self.assertEqual((keys2, rest2), (["pgdn"], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b[", final=True), ([], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b[<0;4;9M"), (["click:4:9"], b""))
        self.assertEqual(app._ap_parse_keys(b"\x1b[<0;4;9m"), ([], b""))


class HandleKey(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = ws(*"abcdefgh")

    def press(self, ui: UIState, key: str) -> str:
        return app._ap_handle_key(ui, key, self.snap, 30)

    def test_quit_everywhere(self) -> None:
        for view in ("overview", "detail", "quota"):
            for key in ("q", "ctrl-c"):
                self.assertEqual(self.press(UIState(view=view), key), "quit")

    def test_overview_movement_clamped(self) -> None:
        ui = UIState()
        self.press(ui, "up")
        self.assertEqual(ui.sel, 0)
        self.press(ui, "j")
        self.press(ui, "down")
        self.assertEqual(ui.sel, 2)
        self.press(ui, "k")
        self.assertEqual(ui.sel, 1)
        # eight workers, then the timeline title row (ws() has no reviews, so it is the last row)
        self.press(ui, "pgdn")
        self.assertEqual((ui.sel, ui.panel), (6, ""))
        self.press(ui, "pgdn")
        self.assertEqual((ui.sel, ui.panel), (6, "timeline"))
        self.press(ui, "down")
        self.assertEqual((ui.sel, ui.panel), (6, "timeline"))
        self.press(ui, "pgup")
        self.assertEqual((ui.sel, ui.panel), (3, ""))
        self.press(ui, "home")
        self.assertEqual(ui.sel, 0)
        self.press(ui, "G")
        self.assertEqual(ui.panel, "timeline")
        self.press(ui, "g")
        self.assertEqual((ui.sel, ui.panel), (0, ""))
        self.press(ui, "end")
        self.assertEqual(ui.panel, "timeline")

    def test_empty_list(self) -> None:
        ui = UIState()
        self.assertEqual(app._ap_handle_key(ui, "down", Snapshot(), 30), "")
        self.assertEqual(app._ap_handle_key(ui, "enter", Snapshot(), 30), "")
        self.assertEqual(app._ap_handle_key(ui, "space", Snapshot(), 30), "")
        self.assertEqual((ui.sel, ui.view), (0, "overview"))

    def test_space_opens_detail(self) -> None:
        ui = UIState(sel=2, tab="report", follow=False, scroll=9, detail_text=["x"])
        self.assertEqual(self.press(ui, "space"), "load_detail")
        self.assertEqual((ui.view, ui.tab, ui.follow, ui.scroll, ui.detail_text, ui.sel),
                         ("detail", "live", True, 0, [], 2))

    def test_enter_on_a_worker_opens_then_closes_its_card_and_does_not_open_the_page(self) -> None:
        ui = UIState(sel=2)
        self.assertEqual(self.press(ui, "enter"), "")
        self.assertEqual((ui.view, ui.expanded), ("overview", {"c"}))
        self.press(ui, "enter")
        self.assertEqual(ui.expanded, set())

    def test_overview_misc(self) -> None:
        ui = UIState()
        self.assertEqual(self.press(ui, "a"), "toggle_all")
        self.assertEqual(self.press(ui, "m"), "")
        self.assertTrue(ui.muted)
        self.press(ui, "m")
        self.assertFalse(ui.muted)
        self.assertEqual(self.press(ui, "tab"), "")
        self.assertEqual(self.press(ui, "esc"), "")
        self.assertEqual(ui.view, "overview")
        self.press(ui, "u")
        self.assertEqual(ui.view, "quota")

    def test_lead_opens_on_key(self) -> None:
        ui = UIState()
        self.assertEqual(self.press(ui, "l"), "")
        self.assertTrue(ui.lead_open)

    def test_density_toggle_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            ui = UIState()
            self.assertEqual(app._ap_handle_key(ui, "d", self.snap, 30), "")
            self.assertEqual((ui.density, app._ap_load_density()), ("compact", "compact"))
            app._ap_handle_key(ui, "d", self.snap, 30)
            self.assertEqual((ui.density, app._ap_load_density()), ("full", "full"))


class DensitySettings(unittest.TestCase):
    def test_bad_cache_and_cli_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "station")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("block")
            with unittest.mock.patch.dict(os.environ, {"XDG_CACHE_HOME": path}):
                self.assertEqual(app._ap_load_density(), "full")
                self.assertEqual(app._ap_parser().parse_args(["--compact"]).compact, True)
                app._ap_save_density("compact")
                self.assertEqual(app._ap_load_density(), "full")  # directory blocks file creation

    def test_plain_size_environment(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"STATUS_ROWS": "42", "STATUS_COLS": "57"}):
            self.assertEqual(app._ap_plain_size(), (57, 42))

    def test_plain_uses_rows_and_everything_override(self) -> None:
        class FakeCollector:
            def snapshot(self, all_history: bool = False) -> Snapshot:
                return Snapshot(quota_ok=True)

        calls: list[tuple[int, int | None]] = []

        def capture(_snap: Snapshot, cols: int, rows: int | None) -> str:
            calls.append((cols, rows))
            return "ok\n"

        env = {"STATUS_ROWS": "42", "STATUS_COLS": "57"}
        with unittest.mock.patch.dict(os.environ, env), unittest.mock.patch.object(
                app, "render_plain", side_effect=capture), redirect_stdout(io.StringIO()):
            app._ap_plain(FakeCollector(), False)
            app._ap_plain(FakeCollector(), False, everything=True)
        self.assertEqual(calls, [(57, 40), (57, None)])

class HandleKeyDetail(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = ws(*"abcdefgh")

    def press(self, ui: UIState, key: str) -> str:
        return app._ap_handle_key(ui, key, self.snap, 30)

    def test_detail_live_scroll(self) -> None:
        ui = UIState(view="detail")
        self.press(ui, "up")
        self.assertEqual((ui.scroll, ui.follow), (1, False))
        self.press(ui, "k")
        self.assertEqual(ui.scroll, 2)
        self.press(ui, "pgup")
        self.assertEqual(ui.scroll, 2 + 22)
        self.press(ui, "down")
        self.press(ui, "j")
        self.assertEqual(ui.scroll, 22)
        self.press(ui, "pgdn")
        self.press(ui, "pgdn")
        self.assertEqual(ui.scroll, 0)
        self.press(ui, "home")
        self.assertGreater(ui.scroll, 1000)
        self.assertFalse(ui.follow)
        self.press(ui, "end")
        self.assertEqual((ui.scroll, ui.follow), (0, True))

    def test_detail_text_tabs_scroll(self) -> None:
        ui = UIState(view="detail", tab="brief")
        self.press(ui, "down")
        self.press(ui, "j")
        self.assertEqual(ui.scroll, 2)
        self.press(ui, "up")
        self.assertEqual(ui.scroll, 1)
        self.press(ui, "up")
        self.press(ui, "up")
        self.assertEqual(ui.scroll, 0)
        self.press(ui, "pgdn")
        self.assertEqual(ui.scroll, 22)
        self.press(ui, "g")
        self.assertEqual(ui.scroll, 0)
        self.press(ui, "G")
        self.assertGreater(ui.scroll, 1000)
        self.assertTrue(ui.follow)

    def test_tabs_cycle(self) -> None:
        ui = UIState(view="detail", scroll=4)
        order = []
        for _ in range(3):
            self.assertEqual(self.press(ui, "tab"), "load_detail")
            order.append(ui.tab)
        self.assertEqual(order, ["brief", "report", "live"])
        self.assertEqual(ui.scroll, 0)
        self.press(ui, "right")
        self.press(ui, "l")
        self.assertEqual(ui.tab, "report")
        self.press(ui, "btab")
        self.assertEqual(ui.tab, "brief")
        self.press(ui, "left")
        self.press(ui, "h")
        self.assertEqual(ui.tab, "report")

    def test_detail_back_and_quota(self) -> None:
        for key in ("esc", "backspace"):
            ui = UIState(view="detail", tab="brief", detail_text=["x"])
            self.press(ui, key)
            self.assertEqual((ui.view, ui.detail_text), ("overview", []))
        ui = UIState(view="detail")
        self.press(ui, "u")
        self.assertEqual(ui.view, "quota")
        self.assertEqual(self.press(ui, "a"), "")
        self.press(ui, "m")
        self.assertTrue(ui.muted)
        self.assertEqual(self.press(UIState(view="detail"), "enter"), "")

    def test_quota_keys(self) -> None:
        for key in ("esc", "backspace", "u"):
            ui = UIState(view="quota")
            self.press(ui, key)
            self.assertEqual(ui.view, "overview")
        ui = UIState(view="quota")
        for key in ("up", "down", "enter", "tab", "a", "pgdn"):
            self.assertEqual(self.press(ui, key), "")
        self.assertEqual((ui.view, ui.sel), ("quota", 0))
        self.press(ui, "m")
        self.assertTrue(ui.muted)


class Events(unittest.TestCase):
    def kinds(self, prev: Snapshot | None, new: Snapshot) -> list[tuple[str, str]]:
        return [(e.kind, e.key) for e in app._ap_detect_events(prev, new)]

    def test_first_snapshot_silent(self) -> None:
        self.assertEqual(self.kinds(None, snap_of(Worker("a", DONE))), [])

    def test_done(self) -> None:
        evs = app._ap_detect_events(ws("a"), snap_of(Worker("a", DONE, title="Hi")))
        self.assertEqual([(e.kind, e.summary, e.body, e.urgency) for e in evs],
                         [("done", "✔ a finished", "Hi", "normal")])
        evs = app._ap_detect_events(snap_of(Worker("a", QUIET)),
                                    snap_of(Worker("a", DONE, summary=["first", "x"])))
        self.assertEqual(evs[0].body, "first")

    def test_failed_noreport(self) -> None:
        evs = app._ap_detect_events(ws("a", "b"), snap_of(Worker("a", FAILED), Worker("b", NOREPORT)))
        self.assertEqual([(e.kind, e.summary, e.urgency) for e in evs],
                         [("failed", "✖ a failed", "critical"),
                          ("noreport", "· b finished without a report", "low")])

    def test_stalled_once(self) -> None:
        new = snap_of(Worker("a", STALLED, quiet_s=725))
        evs = app._ap_detect_events(ws("a"), new)
        self.assertEqual([(e.kind, e.summary, e.body) for e in evs],
                         [("stalled", "▲ a stalled", "no output for 12 min")])
        self.assertEqual(self.kinds(new, new), [])

    def test_quiet_cases(self) -> None:
        self.assertEqual(self.kinds(snap_of(Worker("a", DONE)), snap_of(Worker("a", FAILED))), [])
        self.assertEqual(self.kinds(ws("a"), ws("a")), [])
        self.assertEqual(self.kinds(ws("a"), ws("b")), [])  # new/removed workers are not events

    def test_p1(self) -> None:
        def rev(*prios: int) -> Review:
            return Review("review-x", findings=[Finding(p, f"t{i}") for i, p in enumerate(prios)])

        prev = snap_of(reviews=[rev(2)])
        evs = app._ap_detect_events(prev, snap_of(reviews=[rev(2, 1, 1)]))
        self.assertEqual([(e.kind, e.key, e.summary, e.body, e.urgency) for e in evs],
                         [("p1", "reviews", "review-x: 2 P1 finding(s)", "t1", "critical")])
        already = snap_of(reviews=[rev(1)])
        self.assertEqual(self.kinds(already, snap_of(reviews=[rev(1, 2)])), [])


class Selection(unittest.TestCase):
    def test_follows_name_when_reordered(self) -> None:
        ui = UIState(sel=0)
        self.assertEqual(app._ap_follow_selection(ui, "a", ws("a", "b", "c")), "a")
        self.assertEqual(app._ap_follow_selection(ui, "a", ws("c", "b", "a")), "a")
        self.assertEqual(ui.sel, 2)

    def test_clamps_when_gone(self) -> None:
        ui = UIState(sel=2)
        self.assertEqual(app._ap_follow_selection(ui, "c", ws("a", "b")), "b")
        self.assertEqual(ui.sel, 1)
        self.assertEqual(app._ap_follow_selection(ui, "b", Snapshot()), "")
        self.assertEqual(ui.sel, 0)
        self.assertEqual(app._ap_follow_selection(ui, "", ws("x", "y")), "x")


class Diff(unittest.TestCase):
    def test_only_changed_rows(self) -> None:
        out = app._ap_diff_frame(["a", "b", "c"], ["a", "B", "c"], False)
        self.assertEqual(out, b"\x1b[2;1HB\x1b[0m")
        self.assertEqual(app._ap_diff_frame(["a"], ["a"], False), b"")

    def test_full_and_first(self) -> None:
        want = b"\x1b[1;1Ha\x1b[0m\x1b[2;1Hb\x1b[0m"
        self.assertEqual(app._ap_diff_frame(["a", "b"], ["a", "b"], True), want)
        self.assertEqual(app._ap_diff_frame(None, ["a", "b"], False), want)

    def test_grown_frame(self) -> None:
        self.assertEqual(app._ap_diff_frame(["a"], ["a", "b"], False), b"\x1b[2;1Hb\x1b[0m")

    def test_fit(self) -> None:
        self.assertEqual(app._ap_fit(["a", "b", "c"], 2, 3), ["a", "b"])
        self.assertEqual(app._ap_fit(["a"], 3, 2), ["a", "  ", "  "])


class Plain(unittest.TestCase):
    def test_main_plain_demo(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = app.main(["--plain", "--demo"])
        self.assertEqual(code, 0)
        self.assertTrue(buf.getvalue().strip())
        self.assertNotIn("\x1b", buf.getvalue())

    def test_version_and_flags(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["--version"]), 0)
        self.assertEqual(app.main(["--fps", "99"]), 2)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["--plain", "--demo", "-v", "-c", "--no-notify", "--all"]), 0)


class Scanning(unittest.TestCase):
    def test_sanitised_and_truncated(self) -> None:
        stage = "/tmp/st\x1b[31mage\x00\tdir" + "x" * 300
        err = "OSError: \x1b]0;pwn\x07 bad\r\nline" + "y" * 300
        for cols in (20, 80, 100):
            lines = app._ap_scanning_lines(10, cols, stage, err)
            self.assertEqual(len(lines), 10)
            for ln in lines:
                self.assertNotRegex(ln, r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
                self.assertNotIn("\x1b", ln)
                self.assertLessEqual(len(ln), cols)
            self.assertIn("scanning", lines[5])
            self.assertIn("OSError", lines[6])

    def test_plain_stage_unchanged(self) -> None:
        lines = app._ap_scanning_lines(4, 40, "/a/b/stage/", "")
        self.assertEqual(lines[2].strip(), "scanning stage ...")
        self.assertEqual(lines[3], "")

    def test_safe_text(self) -> None:
        out = app._ap_safe_text("Traceback\n  File \x1b[2J\"x\"\n" + "z" * 200, 50)
        self.assertNotIn("\x1b", out)
        self.assertEqual(len(out.splitlines()), 3)
        self.assertTrue(all(len(ln) <= 50 for ln in out.splitlines()))


class Pty(unittest.TestCase):
    def run_child(self, finish: str) -> tuple[int, bytes]:
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            os.environ["TERM"] = "xterm"
            os.chdir(HERE)
            os._exit(app.main(["--demo"]))
        out = b""

        def drain(seconds: float) -> None:
            nonlocal out
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        data = os.read(fd, 65536)
                    except OSError:
                        return
                    if not data:
                        return
                    out += data

        drain(1.5)
        for key in (b"j", b"\r", b"\t", b"\x1b"):
            os.write(fd, key)
            drain(0.2)
        if finish == "q":
            os.write(fd, b"q")
        else:
            os.kill(pid, signal.SIGTERM)
        status = None
        end = time.monotonic() + 3
        while time.monotonic() < end:
            drain(0.05)
            done, st = os.waitpid(pid, os.WNOHANG)
            if done:
                status = st
                break
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            self.fail("child did not exit within 3 s")
        drain(0.2)
        os.close(fd)
        return os.waitstatus_to_exitcode(status), out

    def check(self, out: bytes) -> None:
        self.assertIn(b"\x1b[?1049h", out)
        self.assertTrue(out.endswith(b"\x1b[?1049l"), out[-40:])
        self.assertIn(b"\x1b[?25h", out)
        self.assertIn(b"\x1b[?7h", out)
        self.assertIn(b"\x1b[?2026h", out)

    def test_quit_key(self) -> None:
        code, out = self.run_child("q")
        self.assertEqual(code, 0)
        self.check(out)

    def test_sigterm(self) -> None:
        code, out = self.run_child("term")
        self.assertEqual(code, 0)
        self.check(out)


if __name__ == "__main__":
    unittest.main()
