"""Monitor per-stage proxy: allowlist, per-path cache, failure mapping and the route.

Upstream calls go through ``httpx.MockTransport``; the route is exercised in-process via
``TestClient``. No socket is opened and no real Monitor port is touched. The handler
answers both ``/api/state`` (the allowlist source) and ``/api/stage`` (the passthrough).
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
    MonitorStage,
)

BASE = "http://127.0.0.1:8743"
ROUTE = "/api/monitor/stage"
STAGE: dict[str, Any] = {
    "path": "proj/build",
    "label": "build",
    "milestones": [{"name": "plan", "state": "done"}],
    "groups": [{"name": "g1", "workers": ["w1"]}],
    "unknown_future_key": {"kept": True},
}


def _state(*paths: str) -> dict[str, Any]:
    return {"stages": [{"path": path, "label": path} for path in paths]}


STATE = _state("proj/build")


def _client(
    *,
    state: object = STATE,
    state_error: Exception | None = None,
    stage: object = None,
    stage_status: int = 200,
    stage_content: bytes | None = None,
    stage_error: Exception | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[MonitorClient, list[httpx.Request]]:
    """A client on an in-memory transport that answers state and stage. Records every call."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/state":
            if state_error is not None:
                raise state_error
            return httpx.Response(200, json=state)
        if stage_error is not None:
            raise stage_error
        if stage_content is not None:
            return httpx.Response(stage_status, content=stage_content)
        return httpx.Response(stage_status, json=stage)

    kwargs: dict[str, Any] = {} if clock is None else {"clock": clock}
    client = MonitorClient(BASE, transport=httpx.MockTransport(handler), **kwargs)
    return client, seen


def _read(client: MonitorClient, path: str) -> MonitorStage:
    return asyncio.run(client.stage(path))


def _stage_calls(seen: list[httpx.Request]) -> list[httpx.Request]:
    return [request for request in seen if request.url.path == "/api/stage"]


def _route_app(client: MonitorClient | None) -> TestClient:
    app = create_app(FakeStore({}), Config(monitor_url=None))
    app.state.monitor_client = client
    return TestClient(app)


def test_ok_passes_the_upstream_stage_through_unchanged() -> None:
    client, seen = _client(stage=STAGE)

    result = _read(client, "proj/build")

    assert result.online is True
    assert result.error is None
    assert result.stage == STAGE
    assert isinstance(result.fetched_at, float)
    calls = _stage_calls(seen)
    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert calls[0].url.params["path"] == "proj/build"
    assert "authorization" not in calls[0].headers


def test_path_absent_from_state_is_refused_without_an_upstream_call() -> None:
    client, seen = _client(stage=STAGE)

    result = _read(client, "proj/other")

    assert result.online is False
    assert result.error == "unknown stage"
    assert result.stage is None
    assert _stage_calls(seen) == []
    assert any(request.url.path == "/api/state" for request in seen)


def test_unknown_path_is_reported_as_unknown_stage() -> None:
    client, _ = _client(state=_state("proj/a", "proj/b"), stage=STAGE)

    result = _read(client, "proj/c")

    assert result.online is False
    assert result.error == "unknown stage"
    assert result.stage is None


def test_each_path_is_cached_separately_within_the_window() -> None:
    now = [100.0]
    client, seen = _client(
        state=_state("proj/a", "proj/b"), stage=STAGE, clock=lambda: now[0]
    )

    a_first = _read(client, "proj/a")
    b_first = _read(client, "proj/b")
    now[0] = 100.0 + CACHE_S - 0.5
    a_second = _read(client, "proj/a")
    b_second = _read(client, "proj/b")

    assert len(_stage_calls(seen)) == 2
    assert a_second is a_first
    assert b_second is b_first


def test_read_after_the_cache_window_calls_upstream_again() -> None:
    now = [100.0]
    client, seen = _client(stage=STAGE, clock=lambda: now[0])

    _read(client, "proj/build")
    now[0] = 100.0 + CACHE_S
    _read(client, "proj/build")

    assert len(_stage_calls(seen)) == 2


def test_offline_snapshot_is_returned_with_its_error_and_no_stage_call() -> None:
    client, seen = _client(state_error=httpx.ConnectError("connection refused"))

    result = _read(client, "proj/build")

    assert result.online is False
    assert result.error == "offline"
    assert result.stage is None
    assert _stage_calls(seen) == []


def test_timeout_is_reported_as_timeout() -> None:
    client, _ = _client(stage_error=httpx.ReadTimeout("too slow"))

    result = _read(client, "proj/build")

    assert result.online is False
    assert result.error == "timeout"
    assert result.stage is None


@pytest.mark.parametrize("status", [404, 500, 503])
def test_non_200_is_a_bad_response(status: int) -> None:
    client, _ = _client(stage_status=status, stage=STAGE)

    result = _read(client, "proj/build")

    assert result.online is False
    assert result.error == "bad response"
    assert result.stage is None


@pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b'"text"', b"null", b"42"])
def test_invalid_json_or_non_object_is_a_bad_response(content: bytes) -> None:
    client, _ = _client(stage_content=content)

    result = _read(client, "proj/build")

    assert result.online is False
    assert result.error == "bad response"
    assert result.stage is None


def test_oversized_body_is_too_large() -> None:
    client, _ = _client(stage_content=b"x" * (MAX_RESPONSE_BYTES + 1))

    result = _read(client, "proj/build")

    assert result.online is False
    assert result.error == "too large"
    assert result.stage is None


def test_path_with_hash_and_spaces_is_encoded_in_the_query() -> None:
    weird = "proj/a b#c"
    client, seen = _client(state=_state(weird), stage=STAGE)

    result = _read(client, weird)

    assert result.online is True
    calls = _stage_calls(seen)
    assert len(calls) == 1
    assert calls[0].url.path == "/api/stage"
    assert calls[0].url.params["path"] == weird
    assert calls[0].url.query == b"path=proj%2Fa+b%23c"
    assert "#" not in str(calls[0].url)


def test_route_returns_the_documented_shape_with_stage() -> None:
    client, _ = _client(stage=STAGE)

    response = _route_app(client).get(ROUTE, params={"path": "proj/build"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"online", "fetched_at", "error", "stage"}
    assert body["online"] is True
    assert body["error"] is None
    assert body["stage"] == STAGE


def test_route_offline_is_still_http_200() -> None:
    client, _ = _client(state_error=httpx.ConnectError("connection refused"))

    response = _route_app(client).get(ROUTE, params={"path": "proj/build"})

    assert response.status_code == 200
    assert response.json()["online"] is False
    assert response.json()["error"] == "offline"
    assert response.json()["stage"] is None


def test_route_unknown_path_is_http_200_with_unknown_stage() -> None:
    client, _ = _client(stage=STAGE)

    response = _route_app(client).get(ROUTE, params={"path": "proj/other"})

    assert response.status_code == 200
    assert response.json()["online"] is False
    assert response.json()["error"] == "unknown stage"
    assert response.json()["stage"] is None


def test_route_without_a_configured_url_reports_not_configured() -> None:
    response = _route_app(None).get(ROUTE, params={"path": "proj/build"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"online", "fetched_at", "error", "stage"}
    assert body["online"] is False
    assert body["error"] == "not configured"
    assert body["stage"] is None
    assert isinstance(body["fetched_at"], float)


def test_route_requires_a_path_query_parameter() -> None:
    client, _ = _client(stage=STAGE)

    assert _route_app(client).get(ROUTE).status_code == 422


def test_route_serves_a_second_read_from_the_per_path_cache() -> None:
    client, seen = _client(stage=STAGE)
    test_client = _route_app(client)

    for _ in range(3):
        assert test_client.get(ROUTE, params={"path": "proj/build"}).json()["stage"] == STAGE

    assert len(_stage_calls(seen)) == 1


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
    assert TestClient(app).post(ROUTE, params={"path": "proj/build"}).status_code == 405
