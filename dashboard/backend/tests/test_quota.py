"""quota_service quota client: URL guard, bounded reads, status mapping, bearer secrecy."""

import asyncio
import json
import logging
import pathlib
import ssl

import certifi
import httpx
import pytest

from stagewatch import quota
from stagewatch.quota import (
    MAX_RESPONSE_BYTES,
    PickQuery,
    QuotaClient,
    QuotaStatus,
    pick_from_upstream,
    usage_from_upstream,
    validate_base_url,
)

FAKE_TOKEN = "fake-bearer-token-for-tests"
BASE = "http://127.0.0.1:8744"
HTTPS_BASE = "https://127.0.0.1:8744"

# Trimmed from the real quota_service replies in .stage/redesign/quota_service-samples (no secrets
# there). Upstream keys stay camelCase; timestamps are Unix epoch milliseconds.
USAGE_SAMPLE = {
    "generatedAt": 1791511190896,
    "subscriptions": [
        {
            "id": "codex",
            "account": "codex:desktop-linux",
            "lastResort": False,
            "stale": False,
            "limits": [
                {"window": "five_hour", "name": "5h", "usedPct": 7,
                 "pacePct": 19.249116666666666, "projectedPct": 36.36530507460516,
                 "runsOutAt": None, "resetsAt": 1791525726000, "stale": False},
                {"window": "seven_day", "name": "Week", "usedPct": 31,
                 "pacePct": 6.525767361111111, "projectedPct": None,
                 "runsOutAt": None, "resetsAt": 1792076523000, "stale": False},
            ],
        },
        {
            "id": "claude",
            "account": "claude:desktop-linux",
            "lastResort": False,
            "stale": False,
            "limits": [
                {"window": "five_hour", "name": "5h", "usedPct": 99,
                 "pacePct": 89.94581666666667, "projectedPct": 110.06626396743668,
                 "runsOutAt": 1791511354349.8484, "resetsAt": 1791513000565, "stale": False},
                {"window": "seven_day", "name": "Week", "usedPct": 59,
                 "pacePct": 29.165054067460318, "projectedPct": 202.29689910236362,
                 "runsOutAt": 1791633767085.3389, "resetsAt": 1791939600565, "stale": False},
            ],
        },
        {
            "id": "opencode-go",
            "account": "opencode-go:desktop-linux",
            "lastResort": False,
            "stale": False,
            "limits": [
                {"window": "five_hour", "name": "5h", "usedPct": 5,
                 "pacePct": 49.46027222222222, "projectedPct": 10.109123495186765,
                 "runsOutAt": None, "resetsAt": 1791520288000, "stale": False},
                {"window": "seven_day", "name": "Week", "usedPct": 88,
                 "pacePct": 58.33182027116403, "projectedPct": 150.8610559226835,
                 "runsOutAt": 1791559298692.0454, "resetsAt": 1791763200000, "stale": False},
                {"window": "thirty_day", "name": "30 days", "usedPct": 68,
                 "pacePct": 17.081498245221027, "projectedPct": 398.09154339856974,
                 "runsOutAt": 1791726490072.0588, "resetsAt": 1793732080000, "stale": False},
            ],
        },
        {
            "id": "cursor-api",
            "account": "cursor:desktop-linux",
            "lastResort": True,
            "stale": False,
            "limits": [
                {"window": "month_api", "name": "API reserve", "usedPct": 0.35555555555555557,
                 "pacePct": 41.495481905864196, "projectedPct": 0.8568536602663432,
                 "runsOutAt": None, "resetsAt": 1793027628000, "stale": False},
            ],
        },
        {
            "id": "grok-bot",
            "account": "grok:desktop-linux",
            "lastResort": False,
            "stale": True,
            "limits": [
                {"window": "seven_day", "name": "Week", "usedPct": 72.900986,
                 "pacePct": 23.34998412698413, "projectedPct": 312.2100023860524,
                 "runsOutAt": 1791563685958.7524, "resetsAt": 1791974770192, "stale": True},
            ],
        },
    ],
}

# `/api/pick?job=review`: one candidate, a lead with a pace warning, and a skipped model.
PICK_REVIEW_SAMPLE = {
    "generatedAt": 1791511190993,
    "job": "review",
    "tierUsed": "mid",
    "stretched": True,
    "candidates": [
        {
            "model": "grok-medium",
            "effort": "default",
            "cli": "cursor",
            "cliModel": "cursor-grok-4.6-medium",
            "subscription": "cursor-models",
            "tier": "mid",
            "group": "runs-out",
            "score": 2.2,
            "runway": 0.04311158290093786,
            "affordable": False,
            "nearLimit": True,
            "stale": False,
            "shortest": {"window": "month_auto", "usedPct": 97.47777777777777,
                         "headroomPct": 2.5222222222222257, "resetsAt": 1793027628000},
            "longest": {"window": "month_auto", "usedPct": 97.47777777777777,
                        "pacePct": 41.4954856095679, "projectedPct": 234.91176533020655,
                        "runsOutAt": 1791539021010.7146, "resetsAt": 1793027628000},
            "reason": "cursor: Auto 97% used at 41% of the Auto, runs out ~57% early",
        }
    ],
    "assignment": [{"model": "grok-medium", "effort": "default", "slices": 1}],
    "fallback": None,
    "skipped": [{"model": "luna", "why": "Cannot review the lead or a builder"}],
    "lead": {
        "model": "grok-bot",
        "overPace": True,
        "suggestion": "sol (7% used) is the available lead",
    },
    "warnings": ["estimated load exceeds remaining headroom; best-effort assignment"],
}

# `/api/pick?job=build&slices=2`: candidates in ranked order, a last-resort candidate last,
# no lead, and a fallback. The last-resort window has no pace or projection fields at all.
PICK_BUILD_SAMPLE = {
    "generatedAt": 1791511194672,
    "job": "build",
    "tierUsed": "small",
    "stretched": False,
    "candidates": [
        {
            "model": "haiku-work",
            "effort": "medium",
            "cli": "claude",
            "cliModel": "claude-haiku-5-5",
            "subscription": "claude-work",
            "tier": "small",
            "group": "ok",
            "score": 45.1,
            "runway": 0.9029882423405945,
            "affordable": True,
            "nearLimit": False,
            "stale": False,
            "shortest": {"window": "five_hour", "usedPct": 31, "headroomPct": 69,
                         "resetsAt": 1791518399956},
            "longest": {"window": "seven_day", "usedPct": 14, "pacePct": 4.760664682539682,
                        "projectedPct": None, "runsOutAt": None, "resetsAt": 1792087199956},
            "reason": "claude: 5h 31% used; Week 14% used; runway 0.9x",
        },
        {
            "model": "luna",
            "effort": "medium",
            "cli": "codex",
            "cliModel": "gpt-6-luna",
            "subscription": "codex",
            "tier": "small",
            "group": "ok",
            "score": 36.9,
            "runway": 0.7381743544008911,
            "affordable": True,
            "nearLimit": False,
            "stale": False,
            "shortest": {"window": "five_hour", "usedPct": 7, "headroomPct": 93,
                         "resetsAt": 1791525726000},
            "longest": {"window": "seven_day", "usedPct": 31, "pacePct": 6.526148478835979,
                        "projectedPct": None, "runsOutAt": None, "resetsAt": 1792076523000},
            "reason": "codex: 5h 7% used; Week 31% used; runway 0.7x",
        },
        {
            "model": "haiku-cursor",
            "effort": "default",
            "cli": "cursor",
            "cliModel": "claude-haiku-5-5-medium",
            "subscription": "cursor-api",
            "tier": "small",
            "group": "last-resort",
            "score": 85.2,
            "runway": 1.7031965361051584,
            "affordable": True,
            "nearLimit": False,
            "stale": False,
            "shortest": {"window": "month_api", "usedPct": 0.35555555555555557,
                         "headroomPct": 99.64444444444445, "resetsAt": 1793027628000},
            "longest": {"window": "month_api", "usedPct": 0.35555555555555557,
                        "pacePct": 41.495627584876544, "projectedPct": 0.8568506521037436,
                        "runsOutAt": None, "resetsAt": 1793027628000},
            "reason": "cursor: API reserve 0% used; runway 1.7x",
        },
    ],
    "assignment": [
        {"model": "haiku-work", "effort": "medium", "slices": 1},
        {"model": "luna", "effort": "medium", "slices": 1},
    ],
    "fallback": {"model": "deepseek", "effort": "default", "subscription": "opencode-go"},
    "skipped": [],
    "lead": None,
    "warnings": [],
}


def _client(handler: object, base: str = BASE) -> QuotaClient:
    return QuotaClient(base, FAKE_TOKEN, transport=httpx.MockTransport(handler))


def _get(client: QuotaClient, method: str, **kwargs: object) -> quota.QuotaResult:
    async def go() -> quota.QuotaResult:
        return await getattr(client, method)(**kwargs)

    return asyncio.run(go())


# --------------------------------------------------------------------------- #
# Base URL guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8744", "http://127.0.0.1:8744"),
        ("http://localhost:1234", "http://localhost:1234"),
        ("http://[::1]:8744", "http://[::1]:8744"),
        ("https://127.0.0.1:443", "https://127.0.0.1:443"),
        ("http://127.0.0.1", "http://127.0.0.1"),
        ("http://127.1.2.3", "http://127.1.2.3"),
        ("  http://127.0.0.1:8744/  ", "http://127.0.0.1:8744"),
    ],
)
def test_validate_base_url_accepts_loopback(url: str, expected: str) -> None:
    assert validate_base_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://quota.example.com:8744",
        "http://0.0.0.0:8744",
        "http://[::2]:8744",
        "ftp://127.0.0.1",
        "http://user:pass@127.0.0.1:8744",
        "http://127.0.0.1:8744/some/path",
        "http://127.0.0.1:8744/?job=x",
        "http://127.0.0.1:8744/#frag",
        "not a url",
    ],
)
def test_validate_base_url_rejects_non_loopback(url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_validate_base_url_normalises_trailing_slash() -> None:
    assert validate_base_url("http://127.0.0.1:8744/") == "http://127.0.0.1:8744"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:bad",
        "http://127.0.0.1:99999",
        "http://127.0.0.1:0",
        "http://localhost:-1",
        "http://127.0.0.1:1.5",
        "http://127.0.0.1:",
        "http://[::1]:",
    ],
)
def test_validate_base_url_rejects_bad_port(url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:bad",
        "http://127.0.0.1:99999",
    ],
)
def test_client_rejects_bad_port_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        QuotaClient(url, FAKE_TOKEN)


def test_client_rejects_empty_secret() -> None:
    with pytest.raises(ValueError):
        QuotaClient(BASE, "   ")


def test_client_repr_hides_secret() -> None:
    client = _client(lambda request: httpx.Response(200, json={}))
    assert FAKE_TOKEN not in repr(client)
    assert FAKE_TOKEN not in str(client)


# --------------------------------------------------------------------------- #
# Online calls
# --------------------------------------------------------------------------- #
def test_usage_online_returns_data_and_metrics() -> None:
    seen: list[httpx.Request] = []
    payload = {"model": "luna", "used": 10, "pace": 0.5, "projected": 20.5, "score": 0.9}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.ONLINE
    assert result.online is True
    assert result.http_status == 200
    assert result.data == payload
    assert (result.used, result.pace, result.projected, result.score) == (10.0, 0.5, 20.5, 0.9)
    assert seen[0].url.path == "/api/pick/usage"
    assert seen[0].headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert dict(seen[0].url.params) == {}


def test_pick_online_sends_all_four_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"score": 0.75})

    result = _get(_client(handler), "pick", job="build", slices=4, lead="lead-1", builder="b8")

    assert result.status is QuotaStatus.ONLINE
    assert result.score == 0.75
    # The pick endpoint has no usage numbers; nothing is invented for them.
    assert (result.used, result.pace, result.projected) == (None, None, None)
    request = seen[0]
    assert request.url.path == "/api/pick"
    assert dict(request.url.params) == {
        "job": "build",
        "slices": "4",
        "lead": "lead-1",
        "builder": "b8",
    }
    assert request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"


def test_online_without_metrics_does_not_invent_them() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": "x"})

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.ONLINE
    assert result.data == {"job": "x"}
    assert (result.used, result.pace, result.projected, result.score) == (None, None, None, None)


# --------------------------------------------------------------------------- #
# Invalid query (local, no upstream call)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kwargs",
    [
        {"job": "", "slices": 4, "lead": "l", "builder": "b"},
        {"job": "   ", "slices": 4, "lead": "l", "builder": "b"},
        {"job": "j", "slices": 0, "lead": "l", "builder": "b"},
        {"job": "j", "slices": -1, "lead": "l", "builder": "b"},
        {"job": "j", "slices": "abc", "lead": "l", "builder": "b"},
        {"job": "j", "slices": True, "lead": "l", "builder": "b"},
        {"job": "j", "slices": 4, "lead": "", "builder": "b"},
        {"job": "j", "slices": 4, "lead": "l", "builder": "  "},
    ],
)
def test_pick_invalid_query_never_calls_upstream(kwargs: dict[str, object]) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    result = _get(_client(handler), "pick", **kwargs)

    assert result.status is QuotaStatus.INVALID_QUERY
    assert result.data is None
    assert (result.used, result.pace, result.projected, result.score) == (None, None, None, None)
    assert calls == []


def test_pick_query_parse_trims_text_and_keeps_int() -> None:
    query = PickQuery.parse(" build ", "4", " lead ", " b8 ")
    assert query.params() == {"job": "build", "slices": "4", "lead": "lead", "builder": "b8"}


# --------------------------------------------------------------------------- #
# Failure mapping
# --------------------------------------------------------------------------- #
def test_offline_connect_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.OFFLINE
    assert result.http_status is None
    assert result.data is None
    assert (result.used, result.pace, result.projected, result.score) == (None, None, None, None)


def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.TIMEOUT
    assert result.data is None
    assert result.used is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, QuotaStatus.BAD_REQUEST),
        (503, QuotaStatus.UNAVAILABLE),
        (500, QuotaStatus.ERROR),
    ],
)
def test_http_status_mapping(status: int, expected: QuotaStatus) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "nope"})

    result = _get(_client(handler), "usage")

    assert result.status is expected
    assert result.http_status == status
    assert result.data is None
    assert result.used is None


def test_invalid_json_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json", headers={"content-type": "text/plain"})

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.ERROR
    assert result.data is None


def test_non_object_json_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.ERROR
    assert result.data is None


def test_oversized_body_is_rejected_and_capped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = b'{"blob":"' + b"a" * MAX_RESPONSE_BYTES + b'"}'
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    result = _get(_client(handler), "usage")

    assert result.status is QuotaStatus.ERROR
    assert result.data is None
    assert result.detail is not None and str(MAX_RESPONSE_BYTES) in result.detail


def test_body_exactly_at_the_cap_is_accepted() -> None:
    prefix, suffix = b'{"blob":"', b'"}'
    filler = b"a" * (MAX_RESPONSE_BYTES - len(prefix) - len(suffix))
    body = prefix + filler + suffix

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    result = _get(_client(handler), "usage")

    assert len(body) == MAX_RESPONSE_BYTES
    assert result.status is QuotaStatus.ONLINE


# --------------------------------------------------------------------------- #
# Timeout configuration + bearer secrecy
# --------------------------------------------------------------------------- #
def test_client_uses_a_five_second_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    handler = lambda request: httpx.Response(200, json={})  # noqa: E731
    client = QuotaClient(BASE, FAKE_TOKEN, transport=httpx.MockTransport(handler))

    asyncio.run(client.usage())

    assert seen["timeout"] == 5.0


def test_client_does_not_honour_environment_proxies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    seen: dict[str, object] = {}
    created: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    handler = lambda request: httpx.Response(200, json={})  # noqa: E731
    client = QuotaClient(BASE, FAKE_TOKEN, transport=httpx.MockTransport(handler))

    result = asyncio.run(client.usage())

    # The proxy env is visible to the process, yet the bearer still reaches the
    # loopback upstream: the client opted out of environment proxies entirely.
    assert result.status is QuotaStatus.ONLINE
    assert seen["trust_env"] is False
    assert created and created[0]._trust_env is False
    assert created[0]._mounts == {}


def test_client_applies_env_ca_bundle_but_not_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))
    seen: dict[str, object] = {}
    created: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    class Capturing(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            seen.update(kwargs)
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", Capturing)
    handler = lambda request: httpx.Response(200, json={})  # noqa: E731
    client = QuotaClient(HTTPS_BASE, FAKE_TOKEN, transport=httpx.MockTransport(handler))

    asyncio.run(client.usage())

    verify = seen["verify"]
    assert isinstance(verify, ssl.SSLContext)
    assert verify.get_ca_certs(), "SSL_CERT_FILE should have been loaded as a CA bundle"
    assert seen["trust_env"] is False
    assert created[0]._mounts == {}


def test_tls_verify_defaults_to_httpx_bundle_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    assert quota._tls_verify() is True


def test_missing_ca_file_is_an_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent-ca-file-for-tests.pem")
    handler = lambda request: httpx.Response(200, json={})  # noqa: E731

    result = _get(_client(handler, HTTPS_BASE), "usage")

    assert result.status is QuotaStatus.ERROR
    assert result.data is None


def test_bad_ca_settings_do_not_break_an_http_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the CA bundle was loaded before the scheme was checked, so a broken
    # SSL_CERT_FILE/SSL_CERT_DIR turned every plain-http call into an error.
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent-ca-file-for-tests.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent-ca-dir-for-tests")
    handler = lambda request: httpx.Response(200, json={"generatedAt": 1})  # noqa: E731

    result = _get(_client(handler, BASE), "usage")

    assert result.status is QuotaStatus.ONLINE
    assert result.data == {"generatedAt": 1}


def test_bad_ca_settings_still_fail_closed_for_https(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    garbage = tmp_path / "not-a-certificate.pem"
    garbage.write_text("this is not a certificate\n", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(garbage))
    handler = lambda request: httpx.Response(200, json={})  # noqa: E731

    result = _get(_client(handler, HTTPS_BASE), "usage")

    assert result.status is QuotaStatus.ERROR
    assert result.data is None


@pytest.mark.parametrize(
    "exc",
    [
        httpx.InvalidURL("not a usable url"),
        OverflowError("port overflow"),
        ValueError("bad url value"),
    ],
)
def test_request_errors_become_results_not_exceptions(exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    result = _get(_client(handler), "usage")

    assert result.status in (QuotaStatus.ERROR, QuotaStatus.OFFLINE)
    assert result.status is not QuotaStatus.ONLINE
    assert result.data is None
    assert (result.used, result.pace, result.projected, result.score) == (None, None, None, None)


def test_bearer_never_in_results_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"used": 1, "score": 2})

    client = _client(handler)
    online = _get(client, "usage")
    picked = _get(client, "pick", job="build", slices=1, lead="l", builder="b")

    # The bearer really is sent (otherwise secrecy would be trivially true).
    assert seen and all(r.headers["authorization"] == f"Bearer {FAKE_TOKEN}" for r in seen)

    for result in (online, picked):
        assert FAKE_TOKEN not in repr(result)
        assert FAKE_TOKEN not in str(result)
        assert FAKE_TOKEN not in json.dumps(result.data)
    assert FAKE_TOKEN not in repr(client)
    assert FAKE_TOKEN not in caplog.text
    assert any("quota call" in rec.message for rec in caplog.records)


def test_quota_module_constant_is_five_seconds() -> None:
    assert quota.DEFAULT_TIMEOUT_S == 5.0


# --------------------------------------------------------------------------- #
# Allowlisted mapping of the real payload shapes (camelCase in, snake_case out)
# --------------------------------------------------------------------------- #
def test_usage_maps_credits_allowlisted_and_in_ms() -> None:
    data = {
        "generatedAt": 1,
        "subscriptions": [],
        "credits": [
            {
                "account": "opencode-zen",
                "balanceUsd": 1.84,
                "burnPerDayUsd": 0.5,
                "runsOutAt": 1791563685958.7,
                "low": True,
                "reason": "under $5",
                "secret": "dropped",
            },
            "junk",
        ],
    }
    usage = usage_from_upstream(data, fetched_at=1.0)

    assert [c.model_dump() for c in usage.credits] == [
        {
            "account": "opencode-zen",
            "balance_usd": 1.84,
            "burn_per_day_usd": 0.5,
            "runs_out_at": 1791563685958.7,
            "low": True,
            "reason": "under $5",
        }
    ]
    assert usage_from_upstream({"subscriptions": []}, fetched_at=1.0).credits == []


def test_usage_keeps_subscription_order_windows_and_ms_units() -> None:
    usage = usage_from_upstream(USAGE_SAMPLE, fetched_at=1700000000.25)

    assert usage.generated_at == 1791511190896.0  # upstream ms, not converted
    assert usage.fetched_at == 1700000000.25  # dashboard s
    assert [sub.id for sub in usage.subscriptions] == [
        "codex",
        "claude",
        "opencode-go",
        "cursor-api",
        "grok-bot",
    ]
    by_id = {sub.id: sub for sub in usage.subscriptions}
    # A third window is kept in upstream order, not dropped or re-labelled.
    assert [limit.window for limit in by_id["opencode-go"].limits] == [
        "five_hour",
        "seven_day",
        "thirty_day",
    ]
    assert by_id["cursor-api"].last_resort is True
    assert by_id["grok-bot"].stale is True
    assert by_id["grok-bot"].limits[0].runs_out_at == 1791563685958.7524  # fractional ms
    claude_week = by_id["claude"].limits[1]
    assert (claude_week.used_pct, claude_week.projected_pct) == (59.0, 202.29689910236362)
    assert claude_week.resets_at == 1791939600565.0
    assert by_id["codex"].limits[1].projected_pct is None  # nullable projection


def test_usage_dump_has_only_the_allowlisted_keys() -> None:
    dumped = usage_from_upstream(USAGE_SAMPLE, fetched_at=1.0).model_dump(mode="json")

    assert set(dumped) == {
        "offline",
        "generated_at",
        "fetched_at",
        "error_code",
        "subscriptions",
        "credits",
    }
    assert set(dumped["subscriptions"][0]) == {"id", "account", "stale", "last_resort", "limits"}
    assert set(dumped["subscriptions"][0]["limits"][0]) == {
        "window",
        "name",
        "used_pct",
        "pace_pct",
        "projected_pct",
        "runs_out_at",
        "resets_at",
    }


def test_review_pick_maps_lead_skips_warnings_and_candidate_windows() -> None:
    pick = pick_from_upstream(PICK_REVIEW_SAMPLE)

    assert pick.model_dump(mode="json") == {
        "job": "review",
        "generated_at": 1791511190993.0,
        "tier_used": "mid",
        "stretched": True,
        "lead": {
            "model": "grok-bot",
            "over_pace": True,
            "suggestion": "sol (7% used) is the available lead",
        },
        "fallback": None,
        "candidates": [
            {
                "model": "grok-medium",
                "effort": "default",
                "subscription": "cursor-models",
                "score": 2.2,
                "runway": 0.04311158290093786,
                "group": "runs-out",
                "tier": "mid",
                "reason": "cursor: Auto 97% used at 41% of the Auto, runs out ~57% early",
                "shortest": {
                    "window": "month_auto",
                    "name": None,
                    "used_pct": 97.47777777777777,
                    "pace_pct": None,
                    "projected_pct": None,
                    "runs_out_at": None,
                    "resets_at": 1793027628000.0,
                    "headroom_pct": 2.5222222222222257,
                },
                "longest": {
                    "window": "month_auto",
                    "name": None,
                    "used_pct": 97.47777777777777,
                    "pace_pct": 41.4954856095679,
                    "projected_pct": 234.91176533020655,
                    "runs_out_at": 1791539021010.7146,
                    "resets_at": 1793027628000.0,
                    "headroom_pct": None,
                },
            }
        ],
        "assignment": [{"model": "grok-medium", "effort": "default", "slices": 1}],
        "skipped": [{"model": "luna", "why": "Cannot review the lead or a builder"}],
        "warnings": ["estimated load exceeds remaining headroom; best-effort assignment"],
    }


def test_build_pick_keeps_ranked_order_including_last_resort() -> None:
    pick = pick_from_upstream(PICK_BUILD_SAMPLE)

    assert [c.model for c in pick.candidates] == ["haiku-work", "luna", "haiku-cursor"]
    assert pick.candidates[-1].group == "last-resort"
    assert pick.candidates[-1].score == 85.2  # kept as sent, not moved up
    assert pick.lead is None
    assert pick.fallback is not None and pick.fallback.model == "deepseek"
    assert pick.fallback.subscription == "opencode-go"
    # The shortest window has no pace or projection in upstream, so those stay null.
    assert pick.candidates[0].shortest is not None
    assert pick.candidates[0].shortest.pace_pct is None
    assert pick.candidates[0].shortest.headroom_pct == 69.0
    assert [a.slices for a in pick.assignment] == [1, 1]
    assert pick.skipped == []
    assert pick.warnings == []


def test_pick_result_has_no_top_level_score_or_candidate() -> None:
    dumped = pick_from_upstream(PICK_BUILD_SAMPLE).model_dump(mode="json")

    assert set(dumped) == {
        "job",
        "generated_at",
        "tier_used",
        "stretched",
        "lead",
        "fallback",
        "candidates",
        "assignment",
        "skipped",
        "warnings",
    }
    assert set(dumped["candidates"][0]) == {
        "model",
        "effort",
        "subscription",
        "score",
        "runway",
        "group",
        "tier",
        "reason",
        "shortest",
        "longest",
    }


def test_mapping_drops_secret_like_and_unlisted_upstream_fields() -> None:
    raw = {
        **PICK_BUILD_SAMPLE,
        "token": FAKE_TOKEN,
        "headers": {"Authorization": f"Bearer {FAKE_TOKEN}"},
        "configPath": "/home/someone/.secrets/quota_service",
    }
    raw["candidates"] = [{**raw["candidates"][0], "authorization": FAKE_TOKEN}]
    dumped = json.dumps(pick_from_upstream(raw).model_dump(mode="json"))

    assert FAKE_TOKEN not in dumped
    assert "configPath" not in dumped
    assert "affordable" not in dumped and "cliModel" not in dumped


def test_absent_score_stays_null_rather_than_invented() -> None:
    raw = {"job": "build", "candidates": [{"model": "m", "group": "ok"}]}
    pick = pick_from_upstream(raw)

    assert pick.candidates[0].score is None
    assert pick.candidates[0].shortest is None
    assert pick.assignment == [] and pick.skipped == [] and pick.warnings == []


@pytest.mark.parametrize(
    "value",
    [True, False, "12", None, [1], {}, float("nan"), float("inf"), -float("inf"), 10**400],
)
def test_non_finite_boolean_string_and_huge_numbers_become_none(value: object) -> None:
    assert quota._number(value) is None


@pytest.mark.parametrize(("value", "expected"), [(0, 0.0), (1.5, 1.5), (-2, -2.0), (10**5, 1e5)])
def test_plain_numbers_survive(value: object, expected: float) -> None:
    assert quota._number(value) == expected


def test_metrics_skip_bad_values_without_raising() -> None:
    found = quota._metrics({"used": True, "pace": "1", "projected": 10**400, "score": 0.5})

    assert found == {"score": 0.5}


def test_mapping_survives_bad_numbers_everywhere() -> None:
    bad = {
        "generatedAt": 10**400,
        "subscriptions": [
            {
                "limits": [
                    {
                        "usedPct": True,
                        "pacePct": "12",
                        "projectedPct": float("nan"),
                        "runsOutAt": float("inf"),
                        "resetsAt": 10**400,
                    }
                ]
            }
        ],
    }
    usage = usage_from_upstream(bad, fetched_at=None)
    [limit] = usage.subscriptions[0].limits

    assert usage.generated_at is None
    assert (limit.used_pct, limit.pace_pct, limit.projected_pct) == (None, None, None)
    assert (limit.runs_out_at, limit.resets_at) == (None, None)

    pick = pick_from_upstream(
        {
            "candidates": [{"score": 10**400, "runway": "x", "shortest": {"usedPct": 10**400}}],
            "assignment": [{"slices": 2.5}, {"slices": 2.0}, {"slices": 10**400}],
            "lead": {"overPace": "yes"},
            "warnings": ["kept", 7, None],
        }
    )
    assert pick.candidates[0].score is None
    assert pick.candidates[0].runway is None
    assert pick.candidates[0].shortest is not None and pick.candidates[0].shortest.used_pct is None
    assert [a.slices for a in pick.assignment] == [None, 2, None]
    assert pick.lead is not None and pick.lead.over_pace is None
    assert pick.warnings == ["kept"]


def test_non_object_entries_are_skipped_not_mapped() -> None:
    raw = {"subscriptions": ["codex", None, {"id": "claude", "limits": "nope"}]}
    usage = usage_from_upstream(raw, fetched_at=None)

    assert [sub.id for sub in usage.subscriptions] == ["claude"]
    assert usage.subscriptions[0].limits == []
