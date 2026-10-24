"""SSE streams: /api/events frame order and the log stream init/append/reset sequence.

Generators are driven directly with short poll and ping intervals, so no test waits on
real long sleeps. One live-server test checks the route wiring over real HTTP.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import uvicorn
from fake_store import FakeStore, make_session

from stagewatch.api import SseTiming, _event_stream, _log_events, create_app
from stagewatch.config import Config
from stagewatch.scanner import Scanner

FAST = SseTiming(poll_s=0.01, ping_s=60.0)
TIMEOUT = 5.0


def event_of(frame: str) -> str | None:
    for line in frame.splitlines():
        if line.startswith("event: "):
            return line.removeprefix("event: ")
    return None


def data_of(frame: str) -> dict:
    for line in frame.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"no data line in {frame!r}")


async def next_frame(gen: AsyncIterator[str]) -> str:
    return await asyncio.wait_for(gen.__anext__(), timeout=TIMEOUT)


async def next_event(gen: AsyncIterator[str]) -> tuple[str | None, str]:
    frame = await next_frame(gen)
    return event_of(frame), frame


@pytest.fixture
def stage(tmp_path: Path) -> Path:
    stage = tmp_path / "proj" / ".stage"
    stage.mkdir(parents=True)
    return stage


def test_events_first_frames_are_retry_then_sessions(stage: Path) -> None:
    store = FakeStore({"proj": stage})
    store.set_session("proj", make_session("proj", stage, rev=3))
    store.pending.clear()
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, None, FAST)
        assert await next_frame(gen) == "retry: 2000\n\n"
        frame = await next_frame(gen)
        assert event_of(frame) == "sessions"
        assert frame.startswith("id: 3\n")
        payload = data_of(frame)
        assert set(payload) == {"generated_at", "sessions", "monitor_online"}
        assert [s["id"] for s in payload["sessions"]] == ["proj"]
        await gen.aclose()

    asyncio.run(run())


def test_session_param_adds_session_frame_on_connect(stage: Path) -> None:
    store = FakeStore({"proj": stage})
    store.set_session("proj", make_session("proj", stage, running=1))
    store.pending.clear()
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, "proj", FAST)
        await next_frame(gen)  # retry
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session"
        assert data_of(frame)["id"] == "proj"
        assert data_of(frame)["workers"][0]["name"] == "w0"
        await gen.aclose()

    asyncio.run(run())


def test_unknown_session_param_sends_session_gone(stage: Path) -> None:
    store = FakeStore({})
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, "ghost", FAST)
        await next_frame(gen)  # retry
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session_gone"
        assert data_of(frame) == {"id": "ghost"}
        await gen.aclose()

    asyncio.run(run())


def test_changes_resend_sessions_and_session_frames(stage: Path) -> None:
    store = FakeStore({"proj": stage})
    store.set_session("proj", make_session("proj", stage, rev=1))
    store.pending.clear()
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, "proj", FAST)
        await next_frame(gen)  # retry
        await next_frame(gen)  # sessions
        await next_frame(gen)  # session

        store.set_session("proj", make_session("proj", stage, rev=2, running=1))
        assert await scanner.scan_once() == frozenset({"proj"})
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session"
        assert frame.startswith("id: 2\n")
        assert data_of(frame)["rev"] == 2
        await gen.aclose()

    asyncio.run(run())


def test_change_for_other_session_only_resends_sessions(stage: Path) -> None:
    store = FakeStore({"proj": stage, "other": stage})
    store.set_session("proj", make_session("proj", stage))
    store.set_session("other", make_session("other", stage))
    store.pending.clear()
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, "proj", FAST)
        for _ in range(3):  # retry, sessions, session
            await next_frame(gen)

        store.set_session("other", make_session("other", stage, rev=2))
        await scanner.scan_once()
        assert (await next_event(gen))[0] == "sessions"

        # Nothing else is due: no session frame for "proj", and the 60 s ping is far off.
        pending = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)
        assert not pending.done()
        pending.cancel()
        with suppress(asyncio.CancelledError):
            await pending

    asyncio.run(run())


def test_removed_session_sends_session_gone(stage: Path) -> None:
    store = FakeStore({"proj": stage})
    store.set_session("proj", make_session("proj", stage))
    store.pending.clear()
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, "proj", FAST)
        for _ in range(3):
            await next_frame(gen)

        store.set_session("proj", None)  # the stage dir disappeared
        await scanner.scan_once()
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session_gone"
        assert data_of(frame) == {"id": "proj"}
        await gen.aclose()

    asyncio.run(run())


def test_nested_station_arrives_and_is_removed(stage: Path) -> None:
    sid = "run:3JvbnJhbQ"
    store = FakeStore({})
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, sid, FAST)
        await next_frame(gen)  # retry
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session_gone"
        assert data_of(frame) == {"id": sid}

        store.set_session(sid, make_session(sid, stage, rev=1))
        assert await scanner.scan_once() == frozenset({sid})
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session"
        assert data_of(frame)["id"] == sid

        store.set_session(sid, None)
        assert await scanner.scan_once() == frozenset({sid})
        assert (await next_event(gen))[0] == "sessions"
        name, frame = await next_event(gen)
        assert name == "session_gone"
        assert data_of(frame) == {"id": sid}
        await gen.aclose()

    asyncio.run(run())


def test_ping_comment_is_sent_when_idle(stage: Path) -> None:
    store = FakeStore({})
    scanner = Scanner(store, Config())
    timing = SseTiming(poll_s=0.01, ping_s=0.02)

    async def run() -> None:
        gen = _event_stream(store, scanner, None, timing)
        await next_frame(gen)  # retry
        await next_frame(gen)  # sessions
        assert await next_frame(gen) == ": ping\n\nevent: ping\ndata: {}\n\n"
        await gen.aclose()

    asyncio.run(run())


def test_closing_the_stream_unsubscribes(stage: Path) -> None:
    store = FakeStore({})
    scanner = Scanner(store, Config())

    async def run() -> None:
        gen = _event_stream(store, scanner, None, FAST)
        await next_frame(gen)
        assert len(scanner._subscribers) == 1
        await gen.aclose()
        assert scanner._subscribers == set()

    asyncio.run(run())


def test_scan_loop_publishes_changes_to_subscribers(stage: Path) -> None:
    store = FakeStore({"proj": stage})
    cfg = Config(scan_interval_s=0.01, discover_interval_s=60)
    scanner = Scanner(store, cfg)
    queue = scanner.subscribe()

    async def run() -> None:
        task = asyncio.create_task(scanner.run())
        try:
            store.set_session("proj", make_session("proj", stage))
            changed = await asyncio.wait_for(queue.get(), timeout=TIMEOUT)
            assert changed == frozenset({"proj"})
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


def test_subscriber_queue_is_bounded_and_unions_changes(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeStore({})
    scanner = Scanner(store, Config())
    queue = scanner.subscribe()
    changes = iter([{"old"}, {"new"}])
    store.refresh = lambda: next(changes)

    async def inline_to_thread(func, *args):
        return func(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)

    async def run() -> None:
        assert queue.maxsize == 1
        await scanner.scan_once()
        await scanner.scan_once()
        assert queue.qsize() == 1
        assert queue.get_nowait() == frozenset({"old", "new"})

    asyncio.run(run())


def test_unsubscribe_discards_subscriber_queue(stage: Path) -> None:
    scanner = Scanner(FakeStore({}), Config())
    queue = scanner.subscribe()
    assert queue in scanner._subscribers

    scanner.unsubscribe(queue)

    assert queue not in scanner._subscribers


def test_scan_loop_survives_a_failing_refresh(stage: Path) -> None:
    class Flaky(FakeStore):
        def refresh(self) -> set[str]:
            self.refresh_calls += 1
            if self.refresh_calls == 1:
                raise RuntimeError("disk on fire")
            return {"proj"}

    store = Flaky({"proj": stage})
    scanner = Scanner(store, Config(scan_interval_s=0.01, discover_interval_s=60))
    queue = scanner.subscribe()

    async def run() -> None:
        task = asyncio.create_task(scanner.run())
        try:
            assert await asyncio.wait_for(queue.get(), timeout=TIMEOUT) == frozenset({"proj"})
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


# --- log stream -------------------------------------------------------------------------


async def _collect_until(gen: AsyncIterator[str], names: list[str]) -> list[tuple[str, dict]]:
    """Read frames until the event names in `names` have all been seen, in order."""
    seen: list[tuple[str, dict]] = []
    for expected in names:
        while True:
            frame = await next_frame(gen)
            name = event_of(frame)
            if name is None:
                continue
            seen.append((name, data_of(frame)))
            assert name == expected, f"expected {expected}, got {name}: {frame!r}"
            break
    return seen


def test_log_stream_init_then_append_then_reset(stage: Path) -> None:
    log = stage / "w.log"
    log.write_text("alpha\nbeta\n", encoding="utf-8")

    async def run() -> None:
        gen = _log_events("w", log, 64 * 1024, FAST)
        init = (await _collect_until(gen, ["init"]))[0][1]
        assert init["stem"] == "w"
        assert [line["text"] for line in init["lines"]] == ["alpha", "beta"]
        assert init["offset"] == log.stat().st_size

        with log.open("a", encoding="utf-8") as fh:
            fh.write("gamma\n")
        append = (await _collect_until(gen, ["append"]))[0][1]
        assert [line["text"] for line in append["lines"]] == ["gamma"]
        assert append["offset"] == log.stat().st_size

        log.write_text("new\n", encoding="utf-8")  # shrinks the file
        events = await _collect_until(gen, ["reset", "init"])
        assert events[1][1]["lines"][0]["text"] == "new"
        await gen.aclose()

    asyncio.run(run())


def test_log_stream_sends_only_complete_lines(stage: Path) -> None:
    log = stage / "w.log"
    log.write_text("done\npart", encoding="utf-8")

    async def run() -> None:
        gen = _log_events("w", log, 64 * 1024, FAST)
        init = (await _collect_until(gen, ["init"]))[0][1]
        assert [line["text"] for line in init["lines"]] == ["done"]
        assert init["offset"] == len(b"done\n")

        await asyncio.sleep(0.05)  # several polls with only a fragment on disk
        with log.open("a", encoding="utf-8") as fh:
            fh.write("ial\n")
        append = (await _collect_until(gen, ["append"]))[0][1]
        assert [line["text"] for line in append["lines"]] == ["partial"]
        await gen.aclose()

    asyncio.run(run())


def test_log_stream_resets_when_inode_changes(stage: Path) -> None:
    log = stage / "w.log"
    log.write_text("one\n", encoding="utf-8")

    async def run() -> None:
        gen = _log_events("w", log, 64 * 1024, FAST)
        await _collect_until(gen, ["init"])
        replacement = stage / "w.new"
        replacement.write_text("two\n", encoding="utf-8")
        replacement.replace(log)  # new inode under the same name
        events = await _collect_until(gen, ["reset", "init"])
        assert events[1][1]["lines"][0]["text"] == "two"
        await gen.aclose()

    asyncio.run(run())


def test_log_stream_stays_open_after_the_file_stops_growing(stage: Path) -> None:
    log = stage / "w.log"
    log.write_text("x\n", encoding="utf-8")

    async def run() -> None:
        timing = SseTiming(poll_s=0.01, ping_s=0.02)
        gen = _log_events("w", log, 64 * 1024, timing)
        await _collect_until(gen, ["init"])
        assert await next_frame(gen) == ": ping\n\nevent: ping\ndata: {}\n\n"
        await gen.aclose()

    asyncio.run(run())


def test_log_stream_pings_while_file_is_unavailable(
    stage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = stage / "w.log"
    log.write_text("x\n", encoding="utf-8")

    async def inline_to_thread(func, *args):
        return func(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline_to_thread)

    async def run() -> None:
        timing = SseTiming(poll_s=0.001, ping_s=0.01)
        gen = _log_events("w", log, 64 * 1024, timing)
        await _collect_until(gen, ["init"])
        log.unlink()
        await _collect_until(gen, ["error"])
        assert await asyncio.wait_for(anext(gen), 0.1) == ": ping\n\nevent: ping\ndata: {}\n\n"
        assert not log.exists()
        await gen.aclose()

    asyncio.run(run())


# --- live server: route wiring over real HTTP --------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_server(stage: Path):
    store = FakeStore({"proj": stage})
    store.set_session("proj", make_session("proj", stage))
    store.pending.clear()
    (stage / "w.log").write_text("hello\n", encoding="utf-8")
    cfg = Config(scan_interval_s=0.01, discover_interval_s=60)
    app = create_app(store, cfg, timing=SseTiming(poll_s=0.01, ping_s=60.0))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.01)
    try:
        yield store, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(TIMEOUT)


def _frames(response: httpx.Response) -> Iterator[str]:
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            yield frame + "\n\n"


def _take(frames: Iterator[str], count: int) -> list[str]:
    return [next(frames) for _ in range(count)]


def test_live_events_route_streams_sessions_then_changes(live_server) -> None:
    store, base = live_server
    with httpx.stream("GET", f"{base}/api/events?session=proj", timeout=TIMEOUT) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        frames = _frames(resp)
        first = _take(frames, 3)  # retry, sessions, session
        assert first[0] == "retry: 2000\n\n"
        assert event_of(first[1]) == "sessions"
        assert event_of(first[2]) == "session"

        store.set_session("proj", make_session("proj", store.stages["proj"], rev=9))
        more = _take(frames, 2)
        assert [event_of(f) for f in more] == ["sessions", "session"]
        assert data_of(more[1])["rev"] == 9


def test_live_log_stream_route_sends_init(live_server) -> None:
    _store, base = live_server
    with httpx.stream("GET", f"{base}/api/sessions/proj/logs/w/stream", timeout=TIMEOUT) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        (frame,) = _take(_frames(resp), 1)
        assert event_of(frame) == "init"
        assert data_of(frame)["lines"][0]["text"] == "hello"
