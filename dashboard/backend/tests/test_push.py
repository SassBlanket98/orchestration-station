"""Push delivery state and API validation, tested without network access."""

import asyncio
import json
from pathlib import Path

import pytest
from fake_store import FakeStore
from fastapi import HTTPException
from starlette.requests import Request

from stagewatch.api import create_app
from stagewatch.config import Config
from stagewatch.push import PushManager, generate_vapid_keys, waiting_sessions
from stagewatch.push import main as push_main


def endpoint(app: object, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def request_with_body(path: str, body: bytes) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": []}, receive)


def stage(path: str, **values: object) -> dict[str, object]:
    return {"path": path, "label": path, **values}


def test_usage_dedupes_per_subscription_window_threshold_and_reset(tmp_path: Path) -> None:
    manager = PushManager(tmp_path / "push.json", private_key_file=None, subject="mailto:a@b")
    limit = {"window": "5h", "used_pct": 82, "resets_at": 2_000_000}
    subs = [{"id": "claude", "limits": [limit]}]

    first = manager.usage_alerts(subs)
    assert [alert["title"] for alert in first] == ["Usage 82% · claude 5h"]
    assert manager.usage_alerts(subs) == []
    assert manager.usage_alerts([{**subs[0], "id": "other"}])
    assert manager.usage_alerts([{"id": "claude", "limits": [{**limit, "used_pct": 96}]}])
    assert manager.usage_alerts([{"id": "claude", "limits": [{**limit, "resets_at": 3_000_000}]}])


def test_finished_transition_only_after_initial_baseline(tmp_path: Path) -> None:
    file = tmp_path / "push.json"
    manager = PushManager(file, None, "mailto:a@b")
    assert manager.finished_alerts([stage("a", lead_state="live")]) == []
    manager = PushManager(file, None, "mailto:a@b")
    assert manager.finished_alerts([stage("a", lead_state="gone", outcome="success")]) == [
        {"title": "Finished · a", "body": "Session finished", "tag": "finished:a"}
    ]
    assert manager.finished_alerts([stage("a", lead_state="gone", outcome="success")]) == []


def test_startup_baseline_does_not_alert_for_already_finished_stage(tmp_path: Path) -> None:
    manager = PushManager(tmp_path / "push.json", None, "mailto:a@b")
    assert manager.finished_alerts([stage("a", lead_state="gone", outcome="success")]) == []


def test_waiting_sessions_need_stale_live_lead_and_no_live_workers() -> None:
    now = 1_000
    waiting = stage(
        "a", lead_state="live", session_name="one", last_activity=800, workers=[], app={"on": True}
    )
    active = stage("b", lead_state="live", last_activity=800, workers=[{"state": "RUNNING"}])
    fresh = stage("c", lead_state="live", last_activity=900, workers=[])
    gone = stage("d", lead_state="gone", last_activity=800, workers=[])

    assert waiting_sessions([waiting, active, fresh, gone], now) == {"waiting": 1, "names": ["one"]}


def test_subscribe_validates_endpoint_keys_and_caps(tmp_path: Path) -> None:
    manager = PushManager(tmp_path / "push.json", None, "mailto:a@b")
    valid = {"endpoint": "https://fcm.googleapis.com/f/e", "keys": {"p256dh": "p", "auth": "a"}}
    with pytest.raises(ValueError):
        manager.subscribe({**valid, "endpoint": "http://push.example/e"})
    with pytest.raises(ValueError):
        manager.subscribe({"endpoint": valid["endpoint"], "keys": {"p256dh": "p"}})
    with pytest.raises(ValueError):
        manager.subscribe({"endpoint": valid["endpoint"], "keys": {"p256dh": 4, "auth": "a"}})
    manager.subscribe(valid)
    manager.subscribe(valid)
    for index in range(9):
        manager.subscribe(
            {
                "endpoint": f"https://fcm.googleapis.com/f/{index}",
                "keys": {"p256dh": "p", "auth": "a"},
            }
        )
    with pytest.raises(ValueError, match="limit"):
        manager.subscribe(
            {"endpoint": "https://fcm.googleapis.com/f/extra", "keys": {"p256dh": "p", "auth": "a"}}
        )
    assert len(json.loads((tmp_path / "push.json").read_text())["subscriptions"]) == 10


def test_unconfigured_routes_return_503(tmp_path: Path) -> None:
    app = create_app(FakeStore({}), Config(push_store_file=tmp_path / "push.json"))
    paths = (
        "/api/push/key",
        "/api/push/subscribe",
        "/api/push/unsubscribe",
        "/api/push/badge",
    )
    for path in paths:
        handler = endpoint(app, path)
        with pytest.raises(HTTPException) as error:
            if path.endswith(("subscribe", "unsubscribe")):
                asyncio.run(handler(request_with_body(path, b"{}")))
            elif path.endswith("badge"):
                asyncio.run(handler(request_with_body(path, b"{}")))
            else:
                handler()
        assert error.value.status_code == 503
        assert error.value.detail == "push not configured"


def test_gone_subscription_is_removed_and_other_errors_are_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_file = tmp_path / "vapid.pem"
    generate_vapid_keys(key_file)
    manager = PushManager(tmp_path / "push.json", key_file, "mailto:a@b")
    manager.subscribe(
        {"endpoint": "https://fcm.googleapis.com/f/e", "keys": {"p256dh": "p", "auth": "a"}}
    )
    seen: list[str] = []

    class Gone(Exception):
        status_code = 410

    def sender(subscription: dict[str, object], payload: dict[str, object]) -> None:
        seen.append(str(subscription["endpoint"]))
        raise Gone()

    monkeypatch.setattr(manager, "_send", sender)
    manager.deliver([{"title": "x", "body": "y", "tag": "z"}], {"waiting": 0, "names": []})
    assert seen == ["https://fcm.googleapis.com/f/e"]
    assert json.loads((tmp_path / "push.json").read_text())["subscriptions"] == []


def test_other_delivery_errors_do_not_log_endpoint_or_drop_subscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    key_file = tmp_path / "vapid.pem"
    generate_vapid_keys(key_file)
    manager = PushManager(tmp_path / "push.json", key_file, "mailto:a@b")
    endpoint_url = "https://fcm.googleapis.com/f/private-endpoint"
    manager.subscribe({"endpoint": endpoint_url, "keys": {"p256dh": "p", "auth": "a"}})

    def sender(subscription: dict[str, object], payload: dict[str, object]) -> None:
        raise RuntimeError(f"failed at {endpoint_url}")

    monkeypatch.setattr(manager, "_send", sender)
    manager.deliver([{"title": "x", "body": "y", "tag": "z"}], {"waiting": 2, "names": ["one"]})
    saved = json.loads((tmp_path / "push.json").read_text())
    assert len(saved["subscriptions"]) == 1
    assert endpoint_url not in caplog.text


def test_generated_key_is_private_and_routes_accept_subscribe(tmp_path: Path) -> None:
    key_file = tmp_path / "vapid.pem"
    public_key = generate_vapid_keys(key_file)
    assert key_file.stat().st_mode & 0o777 == 0o600
    assert public_key
    app = create_app(
        FakeStore({}),
        Config(push_vapid_private_key_file=key_file, push_store_file=tmp_path / "push.json"),
    )
    assert endpoint(app, "/api/push/key")() == {"publicKey": public_key}
    valid = {"endpoint": "https://fcm.googleapis.com/f/e", "keys": {"p256dh": "p", "auth": "a"}}
    subscribe = endpoint(app, "/api/push/subscribe")
    unsubscribe = endpoint(app, "/api/push/unsubscribe")
    subscription_request = request_with_body("/api/push/subscribe", json.dumps(valid).encode())
    assert asyncio.run(subscribe(subscription_request)) == {"ok": True}
    assert asyncio.run(
        unsubscribe(
            request_with_body(
                "/api/push/unsubscribe", json.dumps({"endpoint": valid["endpoint"]}).encode()
            )
        )
    ) == {"ok": True}


def test_init_cli_prints_only_public_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key_file = tmp_path / "vapid.pem"
    monkeypatch.setattr("sys.argv", ["stagewatch.push", "init", str(key_file)])
    push_main()
    printed = capsys.readouterr().out.strip()
    assert printed == PushManager(tmp_path / "push.json", key_file, "mailto:a@b").public_key
    assert "PRIVATE KEY" not in printed
    assert key_file.stat().st_mode & 0o777 == 0o600


def test_subscription_payload_size_is_capped(tmp_path: Path) -> None:
    key_file = tmp_path / "vapid.pem"
    generate_vapid_keys(key_file)
    app = create_app(
        FakeStore({}),
        Config(push_vapid_private_key_file=key_file, push_store_file=tmp_path / "push.json"),
    )
    subscribe = endpoint(app, "/api/push/subscribe")
    with pytest.raises(HTTPException) as error:
        asyncio.run(subscribe(request_with_body("/api/push/subscribe", b" " * 4097)))
    assert error.value.status_code == 413


def test_only_browser_push_services_are_accepted() -> None:
    keys = {"p256dh": "p", "auth": "a"}
    for bad in (
        "https://127.0.0.1/x",
        "https://192.168.1.5/x",
        "http://fcm.googleapis.com/x",
        "https://user:pw@fcm.googleapis.com/x",
        "https://fcm.googleapis.com:8443/x",
        "https://fcm.googleapis.com.evil.test/x",
    ):
        with pytest.raises(ValueError):
            PushManager.validate_subscription({"endpoint": bad, "keys": keys})
    for good in ("https://fcm.googleapis.com/f/a", "https://web.push.apple.com/abc"):
        checked = PushManager.validate_subscription({"endpoint": good, "keys": keys})
        assert checked["endpoint"] == good


def test_undelivered_alerts_are_retried_and_offline_keeps_transitions(tmp_path: Path) -> None:
    manager = PushManager(tmp_path / "push.json", None, "mailto:x@example.com")
    usage = [{"id": "claude", "limits": [{"name": "5h", "used_pct": 85, "resets_at": 1}]}]
    manager.poll(usage, [{"path": "/s", "lead_state": "live"}], now=0)
    # Not configured: nothing was delivered, so the 80% alert is still pending.
    assert manager.usage_alerts(usage, 0)
    manager.poll([], None, now=0)
    finished = manager.finished_alerts([{"path": "/s", "lead_state": "gone", "outcome": "success"}])
    assert [alert["tag"] for alert in finished] == ["finished:/s"]


def test_waiting_skips_unknown_times_and_parents_of_busy_spawns() -> None:
    stages = [
        {"session_id": "a", "lead_state": "live", "last_activity": 0, "label": "no-time"},
        {"session_id": "p", "lead_state": "live", "last_activity": 100, "label": "parent"},
        {
            "session_id": "c", "parent_session": "p", "lead_state": "live",
            "last_activity": 990, "workers": [{"state": "running"}], "label": "child",
        },
        {
            "session_id": "q", "lead_state": "live", "last_activity": 100,
            "label": "quiet", "app": {"on": True},
        },
        {"session_id": "b", "lead_state": "live", "last_activity": 100, "label": "bot"},
    ]
    assert waiting_sessions(stages, now=1000)["names"] == ["quiet"]
