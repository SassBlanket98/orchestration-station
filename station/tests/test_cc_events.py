"""C2: the event model (`row_events`, `fleet_events`) and the log tail reader (`log_tail`)."""
from __future__ import annotations

import os
import tempfile
import unittest

from events import FLEET_LIMIT, ROW_LIMIT, fleet_events, log_tail, row_events
from model import (
    DONE,
    FAILED,
    NOREPORT,
    RUNNING,
    CCCall,
    GlobalSnapshot,
    LogState,
    Snapshot,
    Stage,
    Step,
    Turn,
    Worker,
)

NOW = 1_800_000_000.0


def stage(path: str = "/p/a", calls: list[CCCall] | None = None, doing: list[tuple[float, str]] | None = None,
          turns: list[Turn] | None = None, workers: list[Worker] | None = None) -> Stage:
    st = Stage(path, "sample-project", Snapshot(now=NOW, workers=list(workers or [])))
    st.calls = list(calls or [])
    st.doing = list(doing or [])
    st.turns = list(turns or [])
    return st


def worker(name: str = "i2", state: str = RUNNING, steps: list[Step] | None = None,
           calls: list[CCCall] | None = None, end: float | None = None, failure: str = "") -> Worker:
    return Worker(name, state, log=LogState(steps=list(steps or [])), calls=list(calls or []),
                  end=end, failure=failure)


def step(ts: float, kind: str = "tool", tool: str = "bash", text: str = "cmd") -> Step:
    return Step(ts, kind, tool, text, 0.5, "ok")


def texts(events: list) -> list[str]:
    return [ev.text for ev in events]


class WorkerRows(unittest.TestCase):
    def test_step_kinds_and_one_line_text(self) -> None:
        wk = worker(steps=[step(NOW, text="python3 -m unittest\n-q"),
                           step(NOW + 1, "text", "", "wrote the file"),
                           step(NOW + 2, "error", "", "boom")])
        events = row_events(stage(workers=[wk]), wk)
        self.assertEqual([ev.kind for ev in events], ["tool", "msg", "err"])
        self.assertEqual(texts(events), ["bash python3 -m unittest -q", "wrote the file", "boom"])
        self.assertTrue(all(ev.key == "/p/a|i2" for ev in events))

    def test_call_events_and_a_failed_call_is_an_error(self) -> None:
        wk = worker(calls=[CCCall(NOW, "read", "src/a.py"),
                           CCCall(NOW + 5, "bash", "make", outcome="failed", err="exit 1\nModuleNotFound")])
        events = row_events(stage(workers=[wk]), wk)
        self.assertEqual([(ev.kind, ev.text) for ev in events],
                         [("tool", "read src/a.py"), ("err", "exit 1 ModuleNotFound")])

    def test_a_step_and_its_call_are_listed_once_as_the_call(self) -> None:
        wk = worker(steps=[step(NOW, text="make"), step(NOW + 3, text="make again"),
                           step(NOW, tool="read", text="a.py")],
                    calls=[CCCall(NOW + 1.5, "bash", "make")])
        events = row_events(stage(workers=[wk]), wk)
        self.assertEqual([(ev.kind, ev.ts, ev.text) for ev in events],
                         [("tool", NOW, "read a.py"), ("tool", NOW + 1.5, "bash make"),
                          ("tool", NOW + 3, "bash make again")])

    def test_state_event_when_the_worker_ended(self) -> None:
        for name, state, failure in (("done", DONE, ""), ("failed", FAILED, "exit 1"),
                                     ("noreport", NOREPORT, "")):
            with self.subTest(state=state):
                wk = worker(state=state, steps=[step(NOW - 10)], end=NOW, failure=failure)
                events = row_events(stage(workers=[wk]), wk)
                self.assertEqual(events[-1].kind, "state")
                self.assertEqual(events[-1].ts, NOW)
                self.assertEqual(events[-1].text, state.upper() + (f" · {failure}" if failure else ""))

    def test_a_live_worker_has_no_state_event(self) -> None:
        wk = worker(steps=[step(NOW)])
        self.assertEqual([ev.kind for ev in row_events(stage(workers=[wk]), wk)], ["tool"])

    def test_events_are_oldest_first(self) -> None:
        wk = worker(steps=[step(NOW + 20), step(NOW + 30)],
                    calls=[CCCall(NOW + 10, "read", "x")], end=NOW + 40)
        events = row_events(stage(workers=[wk]), wk)
        self.assertEqual([ev.ts for ev in events], [NOW + 10, NOW + 20, NOW + 30, NOW + 40])

    def test_the_cap_keeps_the_newest(self) -> None:
        wk = worker(steps=[step(NOW + i, text=f"cmd {i}") for i in range(10)])
        events = row_events(stage(workers=[wk]), wk, limit=3)
        self.assertEqual(texts(events), ["bash cmd 7", "bash cmd 8", "bash cmd 9"])

    def test_limit_above_the_default_and_zero(self) -> None:
        wk = worker(steps=[step(NOW)])
        st = stage(workers=[wk])
        self.assertEqual(len(row_events(st, wk, limit=ROW_LIMIT + 10)), 1)
        self.assertEqual(row_events(st, wk, limit=0), [])

    def test_untimed_steps_are_skipped_when_something_else_has_a_time(self) -> None:
        wk = worker(steps=[step(0.0, text="no time"), step(NOW, text="timed")])
        self.assertEqual(texts(row_events(stage(workers=[wk]), wk)), ["bash timed"])

    def test_untimed_steps_are_kept_first_when_nothing_has_a_time(self) -> None:
        wk = worker(steps=[step(0.0, text="one"), step(0.0, text="two")], state=DONE, end=NOW)
        events = row_events(stage(workers=[wk]), wk)
        self.assertEqual(texts(events), ["bash one", "bash two", "DONE"])
        self.assertEqual([ev.ts for ev in events], [0.0, 0.0, NOW])


class LeadRows(unittest.TestCase):
    def test_calls_doing_and_turns(self) -> None:
        st = stage(calls=[CCCall(NOW, "edit", "render.py")],
                   doing=[(NOW + 1, "Edit render.py"), (NOW + 10, "Run tests")],
                   turns=[Turn("r1", NOW - 5, NOW, ask="fix the thing", model="opus-5.5")])
        events = row_events(st)
        self.assertEqual(texts(events), ["edit render.py", "fix the thing · opus-5.5", "Run tests"])
        self.assertEqual(texts(events).count("edit render.py"), 1)   # the doing line repeats the call
        msg = next(ev for ev in events if ev.kind == "msg")
        self.assertEqual((msg.ts, msg.text), (NOW, "fix the thing · opus-5.5"))
        self.assertEqual(msg.key, "/p/a|")

    def test_a_doing_line_matches_a_call_two_seconds_apart_only(self) -> None:
        st = stage(calls=[CCCall(NOW, "edit", "render.py")], doing=[(NOW + 2, "Edit render.py")])
        self.assertEqual(texts(row_events(st)), ["edit render.py"])
        st = stage(calls=[CCCall(NOW, "edit", "render.py")], doing=[(NOW + 2.5, "Edit render.py")])
        self.assertEqual(texts(row_events(st)), ["edit render.py", "Edit render.py"])

    def test_a_failed_turn_adds_an_error_after_its_message(self) -> None:
        st = stage(turns=[Turn("r1", NOW, NOW + 3, ask="do it", model="luna", outcome="error",
                               error="rate limited")])
        events = row_events(st)
        self.assertEqual([(ev.kind, ev.text) for ev in events],
                         [("msg", "do it · luna"), ("err", "rate limited")])

    def test_an_aborted_turn_is_an_error_and_an_ok_turn_is_not(self) -> None:
        st = stage(turns=[Turn("r1", NOW, NOW + 3, ask="a", model="m", outcome="aborted"),
                          Turn("r2", NOW + 10, NOW + 12, ask="b", model="m")])
        self.assertEqual([(ev.kind, ev.text) for ev in row_events(st)],
                         [("msg", "a · m"), ("err", "aborted"), ("msg", "b · m")])

    def test_a_running_turn_is_timed_at_its_start(self) -> None:
        st = stage(turns=[Turn("r1", NOW, None, ask="a", model="m", outcome="running")])
        self.assertEqual([(ev.ts, ev.kind) for ev in row_events(st)], [(NOW, "msg")])

    def test_a_lead_row_reads_nothing_from_workers(self) -> None:
        st = stage(workers=[worker(steps=[step(NOW)])])
        self.assertEqual(row_events(st), [])


class Fleet(unittest.TestCase):
    def fixture(self) -> GlobalSnapshot:
        w1 = worker(name="w1", steps=[step(NOW - 5, text="worker line")])
        st1 = stage("/p/a", calls=[CCCall(NOW - 10, "read", "a.py")], workers=[w1])
        st2 = stage("/p/b", calls=[CCCall(NOW, "bash", "x")])
        return GlobalSnapshot(now=NOW, stages=[st1, st2])

    def test_merge_carries_every_row_key_oldest_first(self) -> None:
        events = fleet_events(self.fixture())
        self.assertEqual([(ev.ts, ev.key) for ev in events],
                         [(NOW - 10, "/p/a|"), (NOW - 5, "/p/a|w1"), (NOW, "/p/b|")])

    def test_the_cap_keeps_the_newest(self) -> None:
        events = fleet_events(self.fixture(), limit=2)
        self.assertEqual([ev.ts for ev in events], [NOW - 5, NOW])

    def test_equal_times_keep_the_stage_and_worker_order(self) -> None:
        w1 = worker(name="w1", steps=[step(NOW, text="w")])
        st1 = stage("/p/a", calls=[CCCall(NOW, "read", "a")], workers=[w1])
        st2 = stage("/p/b", calls=[CCCall(NOW, "read", "b")])
        events = fleet_events(GlobalSnapshot(now=NOW, stages=[st1, st2]))
        self.assertEqual(texts(events), ["read a", "bash w", "read b"])

    def test_no_stages_and_zero_limit(self) -> None:
        self.assertEqual(fleet_events(GlobalSnapshot()), [])
        self.assertEqual(fleet_events(self.fixture(), limit=0), [])

    def test_the_fleet_limit_default_is_the_promise(self) -> None:
        self.assertEqual((ROW_LIMIT, FLEET_LIMIT), (200, 500))


class LogTail(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data: bytes | str, name: str = "log") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as handle:
            handle.write(data.encode("utf-8") if isinstance(data, str) else data)
        return path

    def test_only_the_last_n_lines(self) -> None:
        path = self.write("".join(f"line {i}\n" for i in range(10)))
        self.assertEqual(log_tail(path, 3), ["line 7", "line 8", "line 9"])
        self.assertEqual(log_tail(path, 100), [f"line {i}" for i in range(10)])

    def test_ansi_escapes_are_stripped(self) -> None:
        path = self.write("\x1b[31mred\x1b[0m plain\n\x1b[1;32mok\x1b[0m\n")
        self.assertEqual(log_tail(path), ["red plain", "ok"])

    def test_a_partial_first_line_is_dropped(self) -> None:
        path = self.write("x" * 300_000 + "\none\ntwo\n")
        self.assertGreater(os.path.getsize(path), 256 * 1024)
        self.assertEqual(log_tail(path), ["one", "two"])

    def test_crlf_and_long_lines_are_kept_whole(self) -> None:
        long = "y" * 500
        path = self.write(f"a\r\n{long}\r\n")
        self.assertEqual(log_tail(path), ["a", long])

    def test_a_missing_file_is_empty(self) -> None:
        self.assertEqual(log_tail(os.path.join(self.tmp.name, "nope")), [])

    def test_an_empty_file_is_empty(self) -> None:
        self.assertEqual(log_tail(self.write("")), [])

    def test_a_directory_is_empty(self) -> None:
        self.assertEqual(log_tail(self.tmp.name), [])

    def test_bad_utf8_is_replaced_not_raised(self) -> None:
        path = self.write(b"\xff\xfeabc\n")
        self.assertEqual(log_tail(path), ["\ufffd\ufffdabc"])

    def test_zero_lines_returns_nothing(self) -> None:
        self.assertEqual(log_tail(self.write("a\n"), 0), [])


if __name__ == "__main__":
    unittest.main()
