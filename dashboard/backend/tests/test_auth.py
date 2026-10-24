"""Tunnel requests: the Google sign-in gate, the allow-list, and fail-closed without config."""

import json
import time
from pathlib import Path

import httpx
import pytest
from fake_store import FakeStore
from fastapi.testclient import TestClient

from stagewatch import auth
from stagewatch.config import AuthConfig, Config
from stagewatch.main import create_app

OWNER = "owner@example.com"
PUBLIC = {"cf-connecting-ip": "203.0.113.7"}
BASE = "https://testserver"


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    (tmp_path / "session-secret").write_text("s3ssion-secret-for-tests\n", encoding="utf-8")
    (tmp_path / "google-secret").write_text("g00gle-secret-for-tests\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def cfg(secrets_dir: Path) -> Config:
    return Config(
        auth=AuthConfig(
            google_client_id="client-id.apps.googleusercontent.com",
            google_client_secret_file=secrets_dir / "google-secret",
            session_secret_file=secrets_dir / "session-secret",
            allowed_email=OWNER.upper(),
            public_base_url="https://dashboard.example.com",
        )
    )


@pytest.fixture
def secret(secrets_dir: Path) -> bytes:
    return (secrets_dir / "session-secret").read_text(encoding="utf-8").strip().encode()


@pytest.fixture
def emails(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    """The email Google 'confirms' for each code. Tests set `emails["code"]`."""
    answers: dict[str, str | None] = {}

    async def fake_email_for_code(self: auth.GoogleLogin, code: str) -> str | None:
        return answers.get(code)

    monkeypatch.setattr(auth.GoogleLogin, "email_for_code", fake_email_for_code)
    return answers


def _client(cfg: Config) -> TestClient:
    store = FakeStore({})
    return TestClient(create_app(store, cfg), base_url=BASE)


def _session_cookie(secret: bytes, email: str, exp_offset: float = 3600) -> str:
    return auth.sign({"email": email, "exp": time.time() + exp_offset}, secret)


def test_local_requests_are_not_gated(cfg: Config) -> None:
    client = _client(cfg)
    assert client.get("/api/health").status_code == 200


def test_public_api_without_session_is_401(cfg: Config) -> None:
    client = _client(cfg)
    response = client.get("/api/sessions", headers=PUBLIC)
    assert response.status_code == 401
    assert response.json() == {"detail": "sign-in required"}


def test_public_page_without_session_redirects_to_login(cfg: Config) -> None:
    client = _client(cfg)
    response = client.get("/", headers=PUBLIC, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_page_is_reachable_publicly(cfg: Config) -> None:
    client = _client(cfg)
    response = client.get("/login", headers=PUBLIC)
    assert response.status_code == 200
    assert "Sign in with Google" in response.text
    assert "Halt, mortal." in response.text


def test_start_redirects_to_google_and_sets_state_cookie(cfg: Config) -> None:
    client = _client(cfg)
    response = client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert (
        "redirect_uri=https%3A%2F%2Fdashboard.example.com%2Fauth%2Fgoogle%2Fcallback" in location
    )
    assert "scope=openid+email" in location
    assert auth.STATE_COOKIE in response.headers["set-cookie"]


def test_full_login_for_allowed_account_grants_session(cfg: Config, emails: dict) -> None:
    emails["good-code"] = OWNER
    client = _client(cfg)
    start = client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]

    callback = client.get(
        f"/auth/google/callback?code=good-code&state={state}",
        headers=PUBLIC,
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/"
    assert auth.SESSION_COOKIE in callback.headers["set-cookie"]

    page = client.get("/api/sessions", headers=PUBLIC)
    assert page.status_code == 200


def test_callback_with_wrong_state_is_rejected(cfg: Config, emails: dict) -> None:
    emails["good-code"] = OWNER
    client = _client(cfg)
    client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    response = client.get("/auth/google/callback?code=good-code&state=forged", headers=PUBLIC)
    assert response.status_code == 400
    assert auth.SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_callback_without_state_cookie_is_rejected(cfg: Config, emails: dict) -> None:
    emails["good-code"] = OWNER
    client = _client(cfg)
    response = client.get("/auth/google/callback?code=good-code&state=x", headers=PUBLIC)
    assert response.status_code == 400


def test_other_google_account_is_denied(cfg: Config, emails: dict) -> None:
    emails["other-code"] = "stranger@example.com"
    client = _client(cfg)
    start = client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    response = client.get(f"/auth/google/callback?code=other-code&state={state}", headers=PUBLIC)
    assert response.status_code == 403
    assert "Nice try." in response.text
    assert auth.SESSION_COOKIE not in response.headers.get("set-cookie", "")


def test_unverified_or_failed_google_exchange_is_rejected(cfg: Config, emails: dict) -> None:
    client = _client(cfg)
    start = client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    response = client.get(f"/auth/google/callback?code=unknown&state={state}", headers=PUBLIC)
    assert response.status_code == 400
    assert "Google did not confirm" in response.text


def test_google_network_error_is_a_failed_sign_in_not_a_500(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unreachable(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("google unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", unreachable)
    client = _client(cfg)
    start = client.get("/auth/google/start", headers=PUBLIC, follow_redirects=False)
    state = start.headers["location"].split("state=")[1].split("&")[0]
    response = client.get(f"/auth/google/callback?code=any&state={state}", headers=PUBLIC)
    assert response.status_code == 400
    assert "Google did not confirm" in response.text


def test_valid_session_cookie_passes(cfg: Config, secret: bytes) -> None:
    client = _client(cfg)
    client.cookies.set(auth.SESSION_COOKIE, _session_cookie(secret, OWNER))
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 200


def test_session_for_other_email_is_rejected(cfg: Config, secret: bytes) -> None:
    client = _client(cfg)
    client.cookies.set(auth.SESSION_COOKIE, _session_cookie(secret, "stranger@example.com"))
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401


def test_expired_session_is_rejected(cfg: Config, secret: bytes) -> None:
    client = _client(cfg)
    client.cookies.set(auth.SESSION_COOKIE, _session_cookie(secret, OWNER, exp_offset=-1))
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401


def test_tampered_session_cookie_is_rejected(cfg: Config, secret: bytes) -> None:
    client = _client(cfg)
    token = _session_cookie(secret, "stranger@example.com")
    forged_body = auth.sign({"email": OWNER, "exp": time.time() + 3600}, b"wrong-key")
    client.cookies.set(auth.SESSION_COOKIE, forged_body)
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401


def test_logout_clears_session_cookie(cfg: Config) -> None:
    client = _client(cfg)
    response = client.get("/auth/logout", headers=PUBLIC, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    assert auth.SESSION_COOKIE in response.headers["set-cookie"]


def test_public_requests_fail_closed_without_auth_config() -> None:
    client = TestClient(create_app(FakeStore({}), Config()), base_url=BASE)
    assert client.get("/login", headers=PUBLIC).status_code == 503
    assert client.get("/api/health").status_code == 200


def test_missing_secret_file_fails_at_startup(tmp_path: Path) -> None:
    cfg = Config(
        auth=AuthConfig(
            google_client_id="id",
            google_client_secret_file=tmp_path / "missing",
            session_secret_file=tmp_path / "missing",
            allowed_email=OWNER,
            public_base_url="https://dashboard.example.com",
        )
    )
    with pytest.raises(FileNotFoundError):
        create_app(FakeStore({}), cfg)


@pytest.fixture
def user_cfg(cfg: Config, secrets_dir: Path) -> Config:
    users = secrets_dir / "users.json"
    users.write_text(json.dumps({"symz": auth.hash_password("correct horse")}), encoding="utf-8")
    assert cfg.auth is not None
    cfg.auth.users_file = users
    return cfg


def _login(client: TestClient, username: str, password: str) -> httpx.Response:
    return client.post(
        "/login",
        data={"username": username, "password": password},
        headers=PUBLIC,
        follow_redirects=False,
    )


def test_password_login_sets_session(user_cfg: Config) -> None:
    client = _client(user_cfg)
    assert 'name="password"' in client.get("/login", headers=PUBLIC).text
    response = _login(client, "SYMZ", "correct horse")
    assert response.status_code == 303
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 200


def test_password_login_rejects_bad_credentials(user_cfg: Config) -> None:
    client = _client(user_cfg)
    assert _login(client, "symz", "wrong").status_code == 401
    assert _login(client, "nobody", "correct horse").status_code == 401
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401


def test_password_login_is_throttled(user_cfg: Config) -> None:
    client = _client(user_cfg)
    for _ in range(auth.MAX_FAILS):
        assert _login(client, "symz", "wrong").status_code == 401
    assert _login(client, "symz", "correct horse").status_code == 429


def test_no_password_form_without_users(cfg: Config) -> None:
    client = _client(cfg)
    assert 'name="password"' not in client.get("/login", headers=PUBLIC).text
    assert _login(client, "symz", "x").status_code == 401


def test_removed_user_session_is_rejected(user_cfg: Config, secret: bytes) -> None:
    client = _client(user_cfg)
    client.cookies.set(
        auth.SESSION_COOKIE, auth.sign({"user": "gone", "exp": time.time() + 99}, secret)
    )
    assert client.get("/api/sessions", headers=PUBLIC).status_code == 401


def test_oversized_login_body_is_rejected(user_cfg: Config) -> None:
    client = _client(user_cfg)
    response = _login(client, "symz", "x" * 5000)
    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/manifest.webmanifest", "/sw.js", "/icons/icon-192.png"])
def test_pwa_assets_are_public(cfg: Config, path: str) -> None:
    """The browser fetches the manifest, service worker and icons without cookies."""
    client = _client(cfg)
    response = client.get(path, headers=PUBLIC, follow_redirects=False)
    assert response.status_code not in (302, 401)
    assert "location" not in response.headers


def test_open_paths_stop_at_pwa_assets_and_icons() -> None:
    assert auth._is_open_path("/sw.js")
    assert auth._is_open_path("/manifest.webmanifest")
    assert auth._is_open_path("/icons/icon-512.png")
    assert auth._is_open_path("/login")
    assert not auth._is_open_path("/icons/../api/sessions")
    assert not auth._is_open_path("/api/sessions")
    assert not auth._is_open_path("/iconsx/icon.png")
    assert not auth._is_open_path("/sw.js.map")
    assert not auth._is_open_path("/")
