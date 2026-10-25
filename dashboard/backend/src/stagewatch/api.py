"""HTTP routes and SSE streams (project plan §3). Every route here is read-only."""

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Hashable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from stagewatch import __version__
from stagewatch.activity_feed import ActivityFeed
from stagewatch.config import Config
from stagewatch.contracts import StoreProtocol
from stagewatch.logparse import detect_format, parse_lines
from stagewatch.models import (
    LogChunk,
    LogIndexEntry,
    PickResult,
    QuotaUsage,
    SessionDetail,
    WorkerDetail,
)
from stagewatch.monitor import (
    MonitorClient,
    MonitorSnapshot,
    MonitorStage,
    slim_state,
)
from stagewatch.push import MAX_SUBSCRIPTION_BYTES, PushManager, waiting_sessions
from stagewatch.quota import (
    PickQuery,
    QuotaClient,
    QuotaResult,
    QuotaStatus,
    pick_from_upstream,
    usage_from_upstream,
)
from stagewatch.scanner import Scanner
from stagewatch.stage import scope_detail, session_lead_matches
from stagewatch.tail import MAX_WINDOW, read_from, read_tail

SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+")
EVENT_BYTES = 256 * 1024
_SCAN_CHUNK = 64 * 1024
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
QUOTA_CACHE_S = 10.0
QUOTA_CACHE_MAX = 256
LOG_INDEX_MAX = 500
_QUOTA_DETAIL = {
    QuotaStatus.INVALID_QUERY: "invalid quota query",
    QuotaStatus.OFFLINE: "quota offline",
    QuotaStatus.TIMEOUT: "quota timed out",
    QuotaStatus.UNAVAILABLE: "quota unavailable",
    QuotaStatus.BAD_REQUEST: "quota rejected the query",
    QuotaStatus.ERROR: "quota error",
}
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SseTiming:
    """Stream intervals in seconds. Tests shorten these instead of sleeping for real."""

    poll_s: float = 0.7
    ping_s: float = 15.0


def _frame(event: str, data: Any, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _tail_bytes(tail_kb: int) -> int:
    return min(max(tail_kb, 1), 1024) * 1024


def _sessions_payload(store: StoreProtocol) -> dict[str, Any]:
    sessions = [s.model_dump(mode="json") for s in store.summaries()]
    return {
        "generated_at": time.time(),
        "sessions": sessions,
        "monitor_online": store.monitor_online,
    }


def _stage_dir(store: StoreProtocol, sid: str) -> Path:
    for summary in store.summaries():
        if summary.id == sid:
            return Path(summary.path)
    raise _not_found("session not found")


def _log_file(store: StoreProtocol, sid: str, stem: str) -> Path:
    """Resolve `stem` to a top-level `<stem>.log` or `.txt` inside the stage dir, or 404."""
    stage = _stage_dir(store, sid)
    if not SAFE_NAME.fullmatch(stem):
        raise _not_found("log not found")
    path = store.resolve_log(sid, stem)
    if path is None or path.name not in (f"{stem}.log", f"{stem}.txt"):
        raise _not_found("log not found")
    try:
        resolved = path.resolve()
        inside = resolved.is_relative_to(stage.resolve())
    except (OSError, RuntimeError) as exc:
        raise _not_found("log not found") from exc
    if not inside or not resolved.is_file():
        raise _not_found("log not found")
    return resolved


def _unterminated_len(path: Path, size: int) -> int:
    """Bytes after the last newline, scanning back at most MAX_WINDOW from the end."""
    with path.open("rb") as fh:
        end = size
        while end > 0 and size - end < MAX_WINDOW:
            start = max(0, end - _SCAN_CHUNK)
            fh.seek(start)
            idx = fh.read(end - start).rfind(b"\n")
            if idx != -1:
                return size - (start + idx + 1)
            end = start
    return size - end


def _initial_window(stem: str, path: Path, tail_bytes: int) -> tuple[LogChunk, int, int]:
    """Tail window that ends on a newline, plus the append offset and inode it came from.

    A trailing unterminated fragment is left out, so it arrives whole in a later append.
    """
    chunk = read_tail(path, tail_bytes)
    lines, offset = chunk.lines, chunk.offset
    partial = _unterminated_len(path, chunk.size)
    if partial:
        lines, offset = lines[:-1], offset - partial
    fmt = detect_format(path)
    log_chunk = LogChunk(
        stem=stem,
        format=fmt,
        size=chunk.size,
        offset=offset,
        truncated=chunk.truncated or partial > 0,
        lines=parse_lines(lines, fmt),
    )
    return log_chunk, offset, chunk.inode


def _session_frame(store: StoreProtocol, sid: str, lead_session: str | None = None) -> str:
    detail = store.detail(sid)
    if detail is None:
        return _frame("session_gone", {"id": sid})
    if lead_session is not None:
        detail = scope_detail(detail, lead_session)
    return _frame("session", detail.model_dump(mode="json"), detail.rev)


async def _event_stream(
    store: StoreProtocol,
    scanner: Scanner,
    sid: str | None,
    timing: SseTiming,
    lead_session: str | None = None,
) -> AsyncIterator[str]:
    queue = scanner.subscribe()
    try:
        yield "retry: 2000\n\n"
        sessions = _sessions_payload(store)
        yield _frame("sessions", sessions, max((s["rev"] for s in sessions["sessions"]), default=0))
        if sid is not None:
            yield _session_frame(store, sid, lead_session)
        last_ping = time.monotonic()
        while True:
            wait = max(timing.ping_s - (time.monotonic() - last_ping), 0.0)
            try:
                changed = await asyncio.wait_for(queue.get(), timeout=wait)
            except TimeoutError:
                yield ": ping\n\nevent: ping\ndata: {}\n\n"
                last_ping = time.monotonic()
                continue
            while not queue.empty():
                changed = changed | queue.get_nowait()
            sessions = _sessions_payload(store)
            yield _frame(
                "sessions", sessions, max((s["rev"] for s in sessions["sessions"]), default=0)
            )
            if sid is not None and sid in changed:
                yield _session_frame(store, sid, lead_session)
    finally:
        scanner.unsubscribe(queue)


async def _log_events(
    stem: str, path: Path, tail_bytes: int, timing: SseTiming
) -> AsyncIterator[str]:
    try:
        init, offset, inode = _initial_window(stem, path, tail_bytes)
    except OSError as exc:
        yield _frame("error", {"message": f"log unavailable: {exc.__class__.__name__}"})
        return
    yield _frame("init", init.model_dump(mode="json"))
    fmt = init.format
    last_ping = time.monotonic()
    unavailable = False
    while True:
        await asyncio.sleep(timing.poll_s)
        if unavailable:
            try:
                init, offset, inode = _initial_window(stem, path, tail_bytes)
            except OSError:
                if time.monotonic() - last_ping >= timing.ping_s:
                    yield ": ping\n\nevent: ping\ndata: {}\n\n"
                    last_ping = time.monotonic()
                continue
            yield _frame("reset", {})
            yield _frame("init", init.model_dump(mode="json"))
            fmt = init.format
            unavailable = False
            continue
        more = True
        while more:
            try:
                chunk = await asyncio.to_thread(read_from, path, offset, EVENT_BYTES)
            except OSError as exc:
                yield _frame("error", {"message": f"log unavailable: {exc.__class__.__name__}"})
                unavailable = True
                break
            if chunk.inode != inode or chunk.size < offset:
                yield _frame("reset", {})
                try:
                    init, offset, inode = _initial_window(stem, path, tail_bytes)
                except OSError as exc:
                    yield _frame("error", {"message": f"log unavailable: {exc.__class__.__name__}"})
                    unavailable = True
                    break
                yield _frame("init", init.model_dump(mode="json"))
                fmt = init.format
                break
            if chunk.lines:
                if fmt in ("empty", "missing"):
                    fmt = detect_format(path)
                lines = [line.model_dump(mode="json") for line in parse_lines(chunk.lines, fmt)]
                yield _frame("append", {"offset": chunk.offset, "lines": lines})
            offset = chunk.offset
            more = chunk.truncated
        if time.monotonic() - last_ping >= timing.ping_s:
            yield ": ping\n\nevent: ping\ndata: {}\n\n"
            last_ping = time.monotonic()


@dataclass(frozen=True)
class _Fetched:
    result: QuotaResult
    at: float  # wall-clock time the upstream call finished


class _QuotaCache:
    """Per-process TTL cache for quota results, keyed by the validated query.

    Failures are cached too, so a dead upstream is not hit once per dashboard poll.
    """

    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._entries: dict[Hashable, tuple[float, _Fetched]] = {}

    async def fetch(self, key: Hashable, call: Callable[[], Awaitable[QuotaResult]]) -> _Fetched:
        now = self.clock()
        hit = self._entries.get(key)
        if hit is not None and now - hit[0] < self.ttl_s:
            return hit[1]
        fetched = _Fetched(await call(), time.time())
        stored = self.clock()
        if len(self._entries) >= QUOTA_CACHE_MAX:
            self._prune(stored)
        self._entries[key] = (stored, fetched)
        return fetched

    def _prune(self, now: float) -> None:
        self._entries = {k: v for k, v in self._entries.items() if now - v[0] < self.ttl_s}
        if len(self._entries) >= QUOTA_CACHE_MAX:
            self._entries.clear()


def _quota_client(request: Request) -> QuotaClient | None:
    return getattr(request.app.state, "quota_client", None)


def _monitor_client(request: Request) -> MonitorClient | None:
    return getattr(request.app.state, "monitor_client", None)


def _json_model(model: QuotaUsage | PickResult, status: int) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status)


def _quota_error(status: QuotaStatus) -> JSONResponse:
    code = 400 if status is QuotaStatus.INVALID_QUERY else 503
    return JSONResponse({"detail": _QUOTA_DETAIL.get(status, "quota error")}, status_code=code)


def create_app(store: StoreProtocol, cfg: Config, timing: SseTiming | None = None) -> FastAPI:
    """Build the API. The app owns a Scanner whose loop runs for the app's lifetime."""
    if timing is None:
        timing = SseTiming()
    scanner = Scanner(store, cfg)
    quota_cache = _QuotaCache(QUOTA_CACHE_S)
    push_manager = PushManager(
        cfg.push_store_file, cfg.push_vapid_private_key_file, cfg.push_subject
    )

    async def push_loop() -> None:
        while True:
            try:
                if await asyncio.to_thread(lambda: push_manager.configured):
                    quota_client = _quota_client_for_app(app)
                    usage: list[dict[str, Any]] = []
                    if quota_client is not None:
                        fetched = await quota_cache.fetch(("usage",), quota_client.usage)
                        if fetched.result.status is QuotaStatus.ONLINE:
                            usage = [
                                item.model_dump(mode="json")
                                for item in usage_from_upstream(
                                    fetched.result.data or {}, fetched.at
                                ).subscriptions
                            ]
                    monitor_client = app.state.monitor_client
                    snapshot = (
                        await monitor_client.snapshot()
                        if monitor_client is not None
                        else None
                    )
                    # Offline Monitor: pass None so finished transitions are not lost.
                    stages = (
                        (snapshot.state or {}).get("stages", [])
                        if snapshot and snapshot.online
                        else None
                    )
                    await asyncio.to_thread(push_manager.poll, usage, stages)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - push polling cannot take down the app
                log.warning("push poll failed (%s)", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(scanner.run())
        push_task = asyncio.create_task(push_loop())
        try:
            yield
        finally:
            task.cancel()
            push_task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with suppress(asyncio.CancelledError):
                await push_task

    app = FastAPI(title="stagewatch", version=__version__, lifespan=lifespan)
    # JSON compresses ~10x, which matters on a phone. Starlette's gzip skips text/event-stream.
    app.add_middleware(GZipMiddleware, minimum_size=4096)
    app.state.scanner = scanner
    app.state.quota_cache = quota_cache
    app.state.monitor_client = (
        MonitorClient(cfg.monitor_url) if cfg.monitor_url is not None else None
    )
    app.state.push_manager = push_manager
    app.state.activity_feed = ActivityFeed()

    def _quota_client_for_app(_app: FastAPI) -> QuotaClient | None:
        return getattr(_app.state, "quota_client", None)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    def require_push() -> None:
        if not push_manager.configured:
            raise HTTPException(status_code=503, detail="push not configured")

    async def push_request_body(request: Request) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_SUBSCRIPTION_BYTES:
                raise HTTPException(status_code=413, detail="subscription too large")
            chunks.append(chunk)
        return b"".join(chunks)

    @app.get("/api/push/key")
    def push_key() -> dict[str, str]:
        require_push()
        public_key = push_manager.public_key
        if public_key is None:
            raise HTTPException(status_code=503, detail="push not configured")
        return {"publicKey": public_key}

    @app.post("/api/push/subscribe")
    async def push_subscribe(request: Request) -> dict[str, bool]:
        require_push()
        body = await push_request_body(request)
        try:
            payload = json.loads(body)
            push_manager.subscribe(payload)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid subscription") from None
        return {"ok": True}

    @app.post("/api/push/unsubscribe")
    async def push_unsubscribe(request: Request) -> dict[str, bool]:
        require_push()
        body = await push_request_body(request)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid endpoint") from None
        endpoint = payload.get("endpoint") if isinstance(payload, dict) else None
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise HTTPException(status_code=400, detail="invalid endpoint")
        push_manager.unsubscribe(endpoint)
        return {"ok": True}

    @app.get("/api/push/badge")
    async def push_badge(request: Request) -> dict[str, Any]:
        require_push()
        client = _monitor_client(request)
        if client is None:
            return {"waiting": 0, "names": []}
        try:
            snapshot = await client.snapshot()
            stages = (snapshot.state or {}).get("stages", []) if snapshot.online else []
            return waiting_sessions(stages)
        except Exception:  # noqa: BLE001 - badge is best-effort
            return {"waiting": 0, "names": []}

    @app.get("/api/sessions")
    def sessions() -> dict[str, Any]:
        return _sessions_payload(store)

    # `lead_session` limits the workers to one lead session's (see `scope_detail`).
    @app.get("/api/sessions/{sid}")
    def session_detail(
        sid: str, lead_session: str | None = Query(default=None, min_length=1, max_length=128)
    ) -> SessionDetail:
        detail = store.detail(sid)
        if detail is None:
            raise _not_found("session not found")
        if lead_session is None:
            return detail
        return scope_detail(detail, lead_session)

    @app.get("/api/sessions/{sid}/workers/{name}")
    def worker_detail(
        sid: str,
        name: str,
        lead_session: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> WorkerDetail:
        detail = store.worker_detail(sid, name) if SAFE_NAME.fullmatch(name) else None
        if detail is None:
            raise _not_found("worker not found")
        if lead_session is not None:
            session = store.detail(sid)
            if session is None or not session_lead_matches(Path(session.path), name, lead_session):
                raise _not_found("worker not found")
        return detail

    @app.get("/api/sessions/{sid}/logs/{stem}")
    def log_tail(sid: str, stem: str, tail_kb: int = 64) -> LogChunk:
        path = _log_file(store, sid, stem)
        try:
            chunk = read_tail(path, _tail_bytes(tail_kb))
            fmt = detect_format(path)
        except OSError as exc:
            raise _not_found("log not found") from exc
        return LogChunk(
            stem=stem,
            format=fmt,
            size=chunk.size,
            offset=chunk.offset,
            truncated=chunk.truncated,
            lines=parse_lines(chunk.lines, fmt),
        )

    @app.get("/api/sessions/{sid}/logs/{stem}/stream")
    def log_stream(sid: str, stem: str, tail_kb: int = 64) -> StreamingResponse:
        path = _log_file(store, sid, stem)
        events = _log_events(stem, path, _tail_bytes(tail_kb), timing)
        return StreamingResponse(events, media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/events")
    def events(
        session: str | None = None,
        lead_session: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> StreamingResponse:
        stream = _event_stream(store, scanner, session, timing, lead_session)
        return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/api/logs")
    def logs(sid: str | None = None) -> dict[str, Any]:
        """Bounded index of stage artifacts from each session's `log_index`. No file reads."""
        ids = [s.id for s in store.summaries()]
        if sid is not None:
            if sid not in ids:
                raise _not_found("session not found")
            ids = [sid]
        entries: list[LogIndexEntry] = []
        for owner in ids:
            detail = store.detail(owner)
            if detail is None or not detail.log_index:
                continue
            for entry in detail.log_index:
                entries.append(entry.model_copy(update={"session_id": entry.session_id or owner}))
        entries.sort(key=lambda e: (e.mtime is None, -(e.mtime or 0.0)))
        return {
            "entries": [e.model_dump(mode="json") for e in entries[:LOG_INDEX_MAX]],
            "truncated": len(entries) > LOG_INDEX_MAX,
        }

    @app.get("/api/quota/usage")
    async def quota_usage(request: Request) -> JSONResponse:
        client = _quota_client(request)
        if client is None:
            return _json_model(QuotaUsage(offline=True, error_code=QuotaStatus.OFFLINE.value), 503)
        fetched = await quota_cache.fetch(("usage",), client.usage)
        result = fetched.result
        if result.status is not QuotaStatus.ONLINE:
            return _json_model(QuotaUsage(offline=True, error_code=result.status.value), 503)
        return _json_model(usage_from_upstream(result.data or {}, fetched.at), 200)

    @app.get("/api/quota/pick")
    async def quota_pick(
        request: Request,
        job: str | None = None,
        slices: str | None = None,
        lead: str | None = None,
        builder: str | None = None,
    ) -> JSONResponse:
        try:
            query = PickQuery.parse(job, slices, lead, builder)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        client = _quota_client(request)
        if client is None:
            return _quota_error(QuotaStatus.OFFLINE)
        fetched = await quota_cache.fetch(
            ("pick", query),
            lambda: client.pick(
                job=query.job, slices=query.slices, lead=query.lead, builder=query.builder
            ),
        )
        result = fetched.result
        if result.status is not QuotaStatus.ONLINE:
            return _quota_error(result.status)
        return _json_model(pick_from_upstream(result.data or {}), 200)

    @app.get("/api/monitor")
    async def monitor(request: Request) -> dict[str, Any]:
        """Monitor's state, cached and never failing hard: always HTTP 200."""
        client = _monitor_client(request)
        if client is None:
            not_configured = MonitorSnapshot(
                online=False, fetched_at=time.time(), error="not configured", state=None
            )
            return not_configured.as_dict()
        try:
            snap = (await client.snapshot()).as_dict()
            if isinstance(snap.get("state"), dict):
                snap["state"] = slim_state(snap["state"])
            return snap
        except Exception:  # noqa: BLE001 - the route promises HTTP 200
            return MonitorSnapshot(
                online=False, fetched_at=time.time(), error="offline", state=None
            ).as_dict()

    @app.get("/api/activity")
    async def activity(request: Request) -> dict[str, Any]:
        """Best-effort current lead activity from cached Monitor stages."""
        client = _monitor_client(request)
        if client is None:
            return {"items": {}}
        try:
            snapshot = await client.snapshot()
            stages = (snapshot.state or {}).get("stages", [])
            feed: ActivityFeed = request.app.state.activity_feed
            return {"items": await asyncio.to_thread(feed.get, stages)}
        except Exception:  # noqa: BLE001 - activity is optional and always HTTP 200
            return {"items": {}}

    @app.get("/api/monitor/stage")
    async def monitor_stage(request: Request, path: str) -> dict[str, Any]:
        """One stage's full record, cached per path. HTTP 200 whatever the upstream does."""
        client = _monitor_client(request)
        if client is None:
            not_configured = MonitorStage(
                online=False, fetched_at=time.time(), error="not configured", stage=None
            )
            return not_configured.as_dict()
        try:
            return (await client.stage(path)).as_dict()
        except Exception:  # noqa: BLE001 - the route promises HTTP 200
            return MonitorStage(
                online=False, fetched_at=time.time(), error="offline", stage=None
            ).as_dict()

    return app
