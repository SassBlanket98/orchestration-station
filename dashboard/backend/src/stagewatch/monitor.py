"""Read-only client for the local Monitor state server.

The dashboard proxies Monitor's ``GET /api/state`` so the browser never talks to the
loopback port directly. Shape and security rules follow ``quota.py``:

* The upstream is the caller-supplied loopback origin, checked by ``validate_base_url``.
* Two upstream calls exist: ``GET {base}/api/state`` and, for one stage only,
  ``GET {base}/api/stage?path=``. No headers from the request are forwarded, and no response
  headers are read or exposed.
* A stage path is only forwarded upstream when it appears verbatim as a ``stages[*].path``
  string in the cached state, so the browser cannot probe arbitrary upstream paths.
* Response bodies are streamed and capped at ``MAX_RESPONSE_BYTES``.
* The state result is cached for ``CACHE_S`` seconds on a monotonic clock, failures included,
  and concurrent callers share one in-flight call, so many browsers cause one upstream hit.
  Stage results are cached per path for the same window, capped at ``STAGE_CACHE_MAX`` paths.
* Every failure becomes a result with ``online=False`` and a short ``error``; nothing raises
  to the caller. The upstream payload is passed through unchanged as ``state``/``stage``.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from stagewatch.quota import validate_base_url

log = logging.getLogger(__name__)

TIMEOUT_S = 2.0
CACHE_S = 2.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
STATE_PATH = "/api/state"
STAGE_PATH = "/api/stage"
STAGE_CACHE_MAX = 16


def fetch_state_sync(base_url: str | None) -> dict[str, Any] | None:
    """Fetch one bounded state payload for the synchronous stage scanner."""
    if base_url is None:
        return None
    try:
        url = validate_base_url(base_url) + STATE_PATH
        with httpx.Client(timeout=TIMEOUT_S, trust_env=False) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    return None
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        return None
                    chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("stages"), list):
        return payload
    return None


@dataclass(frozen=True)
class MonitorSnapshot:
    """One state read. ``state`` is set only when ``online``; ``error`` only when not."""

    online: bool
    fetched_at: float  # wall-clock seconds when the upstream call finished
    error: str | None
    state: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "fetched_at": self.fetched_at,
            "error": self.error,
            "state": self.state,
        }


@dataclass(frozen=True)
class MonitorStage:
    """One stage read. ``stage`` is set only when ``online``; ``error`` only when not."""

    online: bool
    fetched_at: float  # wall-clock seconds when the upstream call finished
    error: str | None
    stage: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "online": self.online,
            "fetched_at": self.fetched_at,
            "error": self.error,
            "stage": self.stage,
        }


async def _read_bounded(response: httpx.Response) -> bytes | None:
    """Read at most ``MAX_RESPONSE_BYTES``; ``None`` once the cap is exceeded."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class MonitorClient:
    """Cached ``/api/state`` reader for one Monitor origin."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        cache_s: float = CACHE_S,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._base = validate_base_url(base_url)
        self._url = self._base + STATE_PATH
        self._stage_url = self._base + STAGE_PATH
        self._transport = transport
        self._timeout = timeout
        self._cache_s = cache_s
        self._clock = clock
        self._wall = wall
        self._cached: tuple[float, MonitorSnapshot] | None = None
        self._lock = asyncio.Lock()
        self._stages: dict[str, tuple[float, MonitorStage]] = {}
        self._stage_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"MonitorClient(url={self._url!r})"

    def _fresh(self) -> MonitorSnapshot | None:
        if self._cached is None:
            return None
        stored_at, snapshot = self._cached
        return snapshot if self._clock() - stored_at < self._cache_s else None

    async def snapshot(self) -> MonitorSnapshot:
        """The cached snapshot when fresh; otherwise one upstream call, shared by waiters."""
        fresh = self._fresh()
        if fresh is not None:
            return fresh
        async with self._lock:
            fresh = self._fresh()
            if fresh is not None:
                return fresh
            result = await self._fetch()
            self._cached = (self._clock(), result)
            return result

    async def _fetch(self) -> MonitorSnapshot:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, trust_env=False
            ) as client:
                async with client.stream(
                    "GET", self._url, headers={"Accept": "application/json"}
                ) as response:
                    if response.status_code != 200:
                        log.debug("monitor state -> HTTP %s", response.status_code)
                        return self._failure("bad response")
                    body = await _read_bounded(response)
        except httpx.TimeoutException:
            return self._failure("timeout")
        except httpx.HTTPError:
            return self._failure("offline")

        if body is None:
            return self._failure("too large")
        try:
            data = json.loads(body)
        except ValueError:
            return self._failure("bad response")
        if not isinstance(data, dict) or not isinstance(data.get("stages"), list):
            return self._failure("bad response")
        log.debug("monitor state -> online")
        return MonitorSnapshot(online=True, fetched_at=self._wall(), error=None, state=data)

    def _failure(self, error: str) -> MonitorSnapshot:
        log.debug("monitor state -> %s", error)
        return MonitorSnapshot(online=False, fetched_at=self._wall(), error=error, state=None)

    @staticmethod
    def _known_path(state: dict[str, Any] | None, path: str) -> bool:
        """True when ``path`` is exactly one of the cached state's ``stages[*].path`` values."""
        if not isinstance(state, dict) or not isinstance(state.get("stages"), list):
            return False
        return any(
            isinstance(entry, dict) and entry.get("path") == path for entry in state["stages"]
        )

    def _fresh_stage(self, path: str) -> MonitorStage | None:
        if path not in self._stages:
            return None
        stored_at, stage = self._stages[path]
        return stage if self._clock() - stored_at < self._cache_s else None

    async def stage(self, path: str) -> MonitorStage:
        """One stage's full record, cached per path, for a path the cached state already lists.

        The cached snapshot is consulted first: when it is offline its error is returned and
        no upstream call is made, and a path absent from the cached state is refused without
        forwarding anything upstream. Otherwise one ``GET /api/stage?path=`` is made.
        """
        snap = await self.snapshot()
        if not snap.online:
            return MonitorStage(
                online=False, fetched_at=self._wall(), error=snap.error or "offline", stage=None
            )
        if not self._known_path(snap.state, path):
            log.debug("monitor stage -> unknown stage")
            return MonitorStage(
                online=False, fetched_at=self._wall(), error="unknown stage", stage=None
            )
        fresh = self._fresh_stage(path)
        if fresh is not None:
            return fresh
        async with self._stage_lock:
            fresh = self._fresh_stage(path)
            if fresh is not None:
                return fresh
            result = await self._fetch_stage(path)
            self._store_stage(path, result)
            return result

    def _store_stage(self, path: str, stage: MonitorStage) -> None:
        self._stages[path] = (self._clock(), stage)
        while len(self._stages) > STAGE_CACHE_MAX:
            oldest = min(self._stages, key=lambda key: self._stages[key][0])
            del self._stages[oldest]

    async def _fetch_stage(self, path: str) -> MonitorStage:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, trust_env=False
            ) as client:
                async with client.stream(
                    "GET",
                    self._stage_url,
                    params={"path": path},
                    headers={"Accept": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        log.debug("monitor stage -> HTTP %s", response.status_code)
                        return self._stage_failure("bad response")
                    body = await _read_bounded(response)
        except httpx.TimeoutException:
            return self._stage_failure("timeout")
        except httpx.HTTPError:
            return self._stage_failure("offline")

        if body is None:
            return self._stage_failure("too large")
        try:
            data = json.loads(body)
        except ValueError:
            return self._stage_failure("bad response")
        if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
            return self._stage_failure("bad response")
        log.debug("monitor stage -> online")
        return MonitorStage(online=True, fetched_at=self._wall(), error=None, stage=data)

    def _stage_failure(self, error: str) -> MonitorStage:
        log.debug("monitor stage -> %s", error)
        return MonitorStage(online=False, fetched_at=self._wall(), error=error, stage=None)


# The overview reads only these. The rest (task groups, ids, tool bins, turn summaries) stays on
# the server: the full state is ~1 MB, which a phone would pull every few seconds.
_STAGE_KEEP = frozenset(
    {
        "path",
        "label",
        "tag",
        "lead_ctx",
        "lead_ctx_window",
        "lead_state",
        "lead_kind",
        "live",
        "last_activity",
        "solo",
        "latest_milestone",
        "counts",
        "session_name",
        "lead_model",
        "effort",
        "section",
        "session_id",
        "project",
        "parent_session",
        "root_session",
        "launch_depth",
        "level_tag",
        "display_name",
        "goal",
        "tokens_total",
        "cost_total",
        "lead_tokens",
        "lead_cost",
        "lead_tools",
        "app",
    }
)
_WORKER_KEEP = frozenset(
    {
        "name",
        "state",
        "level_tag",
        "depth",
        "title",
        "model",
        "job",
        "is_review",
        "start",
        "end",
        "tokens_total",
        "cost",
        "goal",
        "harness",
        "has_report",
        "errors",
        "failure",
        "outcome",
        "quiet_s",
        "flags",
    }
)
_GOAL_MAX = 240
_WORKER_TEXT = ("goal", "failure")


def _slim_worker(worker: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in worker.items() if k in _WORKER_KEEP}
    for key in _WORKER_TEXT:
        value = out.get(key)
        if isinstance(value, str) and len(value) > _GOAL_MAX:
            out[key] = value[: _GOAL_MAX - 1] + "…"
    return out


def slim_state(state: dict[str, Any]) -> dict[str, Any]:
    """A copy of Monitor's state with only what the overview draws. Closed stages lose their
    worker lists (their counts stay); long goals are cut."""
    stages = state.get("stages")
    if not isinstance(stages, list):
        return state
    slim: list[Any] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        out = {k: v for k, v in stage.items() if k in _STAGE_KEEP}
        goal = out.get("goal")
        if isinstance(goal, str) and len(goal) > _GOAL_MAX:
            out["goal"] = goal[: _GOAL_MAX - 1] + "…"
        workers = stage.get("workers")
        closed = (
            stage.get("section") not in ("active", "idle") and stage.get("lead_state") != "live"
        )
        if isinstance(workers, list) and not closed:
            out["workers"] = [_slim_worker(w) for w in workers if isinstance(w, dict)]
        else:
            out["workers"] = []
        slim.append(out)
    return {**{k: v for k, v in state.items() if k != "stages"}, "stages": slim}
