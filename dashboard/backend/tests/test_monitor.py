"""Monitor state proxy: passthrough, 2 s cache, failure mapping, size cap and the route.

Upstream calls go through ``httpx.MockTransport``; the route is exercised in-process via
``TestClient``. No socket is opened and no real Monitor port is touched.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fake_store import FakeStore
from fastapi.testclient import TestClient

from stagewatch.api import create_app
from stagewatch.config import Config
from stagewatch.monitor import (
    CACHE_S,
    MAX_RESPONSE_BYTES,
    MonitorClient,
    MonitorSnapshot,
    slim_state,  # noqa: I001
)

BASE = "http://127.0.0.1:8743"
ROUTE = "/api/monitor"
STATE: dict[str, Any] = {
    "stages": [
        {
            "path": "proj/build",
            "label": "build",
            "tag": "b",
            "lead_ctx": None,
            "lead_state": "idle",
            "latest_milestone": None,
            "counts": {"running": 1},
            "workers": [],
            "groups": [],
            "session_name": "lead-a",
            "lead_model": "opus",
            "effort": "high",
            "section": "build",
            "session_id": "s1",
            "cwd": "/home/user/Projects/proj",
        }
    ],
    "unknown_future_key": {"kept": True},
}


def _upstream(
    *,
    status: int = 200,
    body: object = None,
    content: bytes | None = None,
    error: Exception | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[MonitorClient, list[httpx.Request]]:
    """A client on an in-memory transport. The list records each upstream call."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if error is not None:
            raise error
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    client = MonitorClient(BASE, transport=httpx.MockTransport(handler), **kwargs)
    return client, seen


def _read(client: MonitorClient) -> MonitorSnapshot:
    return asyncio.run(client.snapshot())


def _route_app(client: MonitorClient | None) -> TestClient:
    app = create_app(FakeStore({}), Config(monitor_url=None))
    app.state.monitor_client = client
    return TestClient(app)


def test_ok_passes_the_upstream_state_through_unchanged() -> None:
    client, seen = _upstream(body=STATE)

    snap = _read(client)

    assert snap.online is True
    assert snap.error is None
    assert snap.state == STATE
    assert isinstance(snap.fetched_at, float)
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/state"
    assert "authorization" not in seen[0].headers


def test_second_read_within_two_seconds_is_served_from_cache() -> None:
    now = [100.0]
    client, seen = _upstream(body=STATE, clock=lambda: now[0])

    first = _read(client)
    now[0] = 100.0 + CACHE_S - 0.5
    second = _read(client)

    assert len(seen) == 1
    assert second is first


def test_read_after_the_cache_window_calls_upstream_again() -> None:
    now = [100.0]
    client, seen = _upstream(body=STATE, clock=lambda: now[0])

    _read(client)
    now[0] = 100.0 + CACHE_S
    _read(client)

    assert len(seen) == 2


def test_concurrent_reads_share_one_upstream_call() -> None:
    client, seen = _upstream(body=STATE)

    async def burst() -> list[MonitorSnapshot]:
        return list(await asyncio.gather(*(client.snapshot() for _ in range(5))))

    results = asyncio.run(burst())

    assert len(seen) == 1
    assert all(result.state == STATE for result in results)


def test_offline_upstream_is_a_result_not_an_exception() -> None:
    client, _ = _upstream(error=httpx.ConnectError("connection refused"))

    snap = _read(client)

    assert snap.online is False
    assert snap.error == "offline"
    assert snap.state is None


def test_timeout_is_reported_as_timeout() -> None:
    client, _ = _upstream(error=httpx.ReadTimeout("too slow"))

    snap = _read(client)

    assert snap.online is False
    assert snap.error == "timeout"
    assert snap.state is None


@pytest.mark.parametrize("status", [404, 500, 503])
def test_non_200_is_a_bad_response(status: int) -> None:
    client, _ = _upstream(status=status, body=STATE)

    snap = _read(client)

    assert snap.online is False
    assert snap.error == "bad response"
    assert snap.state is None


@pytest.mark.parametrize(
    "content",
    [b"not json", b"[1, 2]", b'"text"', b"null", b"42", b"{}", b'{"stages": "x"}'],
)
def test_non_object_or_invalid_json_is_a_bad_response(content: bytes) -> None:
    client, _ = _upstream(content=content)

    snap = _read(client)

    assert snap.online is False
    assert snap.error == "bad response"
    assert snap.state is None


def test_oversized_body_is_too_large() -> None:
    client, _ = _upstream(content=b"x" * (MAX_RESPONSE_BYTES + 1))

    snap = _read(client)

    assert snap.online is False
    assert snap.error == "too large"
    assert snap.state is None


def test_body_exactly_at_the_cap_is_accepted() -> None:
    prefix, suffix = b'{"stages":[],"p":"', b'"}'
    value_len = MAX_RESPONSE_BYTES - len(prefix) - len(suffix)
    content = prefix + b"a" * value_len + suffix
    assert len(content) == MAX_RESPONSE_BYTES
    client, _ = _upstream(content=content)

    snap = _read(client)

    assert snap.online is True
    assert snap.state is not None
    assert len(snap.state["p"]) == value_len


@pytest.mark.parametrize("url", ["http://10.0.0.5:8743", "https://example.com"])
def test_non_loopback_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        MonitorClient(url)


def test_route_returns_the_documented_shape_with_state() -> None:
    client, _ = _upstream(body=STATE)

    response = _route_app(client).get(ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"online", "fetched_at", "error", "state"}
    assert body["online"] is True
    assert body["error"] is None
    assert body["state"] == slim_state(STATE)


def test_route_offline_is_still_http_200() -> None:
    client, _ = _upstream(error=httpx.ConnectError("connection refused"))

    response = _route_app(client).get(ROUTE)

    assert response.status_code == 200
    assert response.json()["online"] is False
    assert response.json()["error"] == "offline"
    assert response.json()["state"] is None


def test_route_without_a_configured_url_reports_not_configured() -> None:
    response = _route_app(None).get(ROUTE)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"online", "fetched_at", "error", "state"}
    assert body["online"] is False
    assert body["error"] == "not configured"
    assert body["state"] is None
    assert isinstance(body["fetched_at"], float)


def test_many_browsers_in_one_window_cause_one_upstream_call() -> None:
    client, seen = _upstream(body=STATE)
    test_client = _route_app(client)

    for _ in range(3):
        assert test_client.get(ROUTE).json()["state"] == slim_state(STATE)

    assert len(seen) == 1


def test_route_is_get_only() -> None:
    app = create_app(FakeStore({}), Config(monitor_url=None))
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", None) == ROUTE
        for method in route.methods
    }

    assert methods <= {"GET", "HEAD"}
    assert "GET" in methods
    assert TestClient(app).post(ROUTE).status_code == 405


def test_slim_state_keeps_overview_fields_and_drops_closed_workers() -> None:
    state = {"v": 2, "stages": [
        {"path": "a", "section": "active", "lead_state": "live", "groups": [1], "goal": "g" * 500,
         "workers": [{"name": "w", "state": "running", "goal": "x" * 500, "has_report": False,
                      "execution_id": "e"}]},
        {"path": "b", "section": "closed", "lead_state": "gone", "workers": [{"name": "old"}]},
    ]}
    slim = slim_state(state)
    live, closed = slim["stages"]
    assert "groups" not in live and len(live["goal"]) == 240
    (worker,) = live["workers"]
    assert "execution_id" not in worker
    assert worker["name"] == "w" and worker["has_report"] is False and len(worker["goal"]) == 240
    assert closed["workers"] == [] and slim["v"] == 2
