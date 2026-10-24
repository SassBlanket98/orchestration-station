"""App factory wiring of the quota_service quota client: attached when configured, else offline."""

import asyncio
import logging
from pathlib import Path

import pytest
from fake_store import FakeStore
from fastapi.testclient import TestClient

from stagewatch.config import QUOTA_SERVICE_SECRET_ENV, Config, ConfigError
from stagewatch.main import create_app
from stagewatch.quota import QuotaClient, QuotaStatus

SECRET = "tw-secret-value-456"
URL = "http://127.0.0.1:8787"
# Nothing listens on port 1, so a call fails fast as offline without touching a real daemon.
REFUSED_URL = "http://127.0.0.1:1"


@pytest.fixture
def store() -> FakeStore:
    return FakeStore({})


@pytest.fixture(autouse=True)
def _no_ambient_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(QUOTA_SERVICE_SECRET_ENV, raising=False)


def test_client_attached_when_url_and_env_secret_set(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)

    app = create_app(store, Config(quota_service_url=URL))

    assert isinstance(app.state.quota_client, QuotaClient)


def test_client_attached_when_secret_comes_from_file(store: FakeStore, tmp_path: Path) -> None:
    secret_file = tmp_path / "tw.secret"
    secret_file.write_text(f"{SECRET}\n", encoding="utf-8")

    app = create_app(store, Config(quota_service_url=URL, quota_service_secret_file=secret_file))

    assert isinstance(app.state.quota_client, QuotaClient)


def test_client_is_none_without_url(store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)

    app = create_app(store, Config())

    assert app.state.quota_client is None


def test_client_is_none_without_secret(store: FakeStore) -> None:
    app = create_app(store, Config(quota_service_url=URL))

    assert app.state.quota_client is None


def test_default_config_is_offline(store: FakeStore) -> None:
    app = create_app(store, Config())

    assert app.state.quota_client is None


def test_unreadable_secret_file_fails_at_app_build(store: FakeStore, tmp_path: Path) -> None:
    cfg = Config(quota_service_url=URL, quota_service_secret_file=tmp_path / "absent.secret")

    with pytest.raises(ConfigError):
        create_app(store, cfg)


def test_unused_secret_file_is_not_read_without_url(store: FakeStore, tmp_path: Path) -> None:
    cfg = Config(quota_service_secret_file=tmp_path / "absent.secret")

    app = create_app(store, cfg)

    assert app.state.quota_client is None


def test_secret_is_absent_from_client_repr_and_api_output(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)
    app = create_app(store, Config(quota_service_url=URL))

    client = TestClient(app)
    responses = [client.get("/api/health"), client.get("/api/sessions")]

    assert SECRET not in repr(app.state.quota_client)
    for response in responses:
        assert SECRET not in response.text


def test_secret_is_absent_from_logs_on_upstream_failure(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(QUOTA_SERVICE_SECRET_ENV, SECRET)
    caplog.set_level(logging.DEBUG)
    app = create_app(store, Config(quota_service_url=REFUSED_URL))

    result = asyncio.run(app.state.quota_client.usage())

    assert result.status is QuotaStatus.OFFLINE
    assert "quota call" in caplog.text
    assert SECRET not in caplog.text


def test_spa_page_revalidates_and_hashed_assets_cache(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from stagewatch.main import _mount_spa

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets" / "index-abc.js").write_text("x")
    (tmp_path / "sw.js").write_text("y")
    app = FastAPI()
    _mount_spa(app, tmp_path)
    client = TestClient(app)

    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/runs/x").headers["cache-control"] == "no-cache"
    assert client.get("/sw.js").headers["cache-control"] == "no-cache"
    assert "immutable" in client.get("/assets/index-abc.js").headers["cache-control"]
