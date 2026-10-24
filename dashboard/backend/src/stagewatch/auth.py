"""Google sign-in for requests that arrive through the Cloudflare tunnel.

Cloudflare adds a `Cf-Connecting-Ip` header to every proxied request, so that header marks a
public request. Public requests need a signed session cookie from a Google account on the
allow-list; everything else gets sent to the login page. Local requests (the server binds
127.0.0.1) are not gated.

Cookies are HMAC-signed JSON with an expiry. The secret comes from a file, never from config.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from stagewatch.config import AuthConfig, Config

SESSION_COOKIE = "sd_session"
STATE_COOKIE = "sd_oauth_state"
STATE_TTL_S = 600
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# Paths a public visitor may reach without a session. The PWA manifest, service worker and icons
# are fetched by the browser without cookies, so they must be open too.
OPEN_PATHS = frozenset(
    {
        "/login",
        "/auth/google/start",
        "/auth/google/callback",
        "/auth/logout",
        "/manifest.webmanifest",
        "/sw.js",
    }
)
OPEN_PREFIXES = ("/icons/",)

JOKES = [
    "Your workers are busy. Your login is not. Sign in.",
    "The lead has to check your ID before you see the clipboard.",
    "Workers are dispatched on the honour system. Access is not.",
    "This dashboard watches the workers. Who watches the dashboard? Sign in first.",
    "Clock in to see the clock. Sign in with Google.",
    "Unauthorised labour will be asked to log out and touch grass.",
]

LOGIN_CSS = """
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: #14110f; color: #f4ead8; font-family: system-ui, sans-serif; }
main { max-width: 28rem; padding: 2rem; text-align: center; }
h1 { font-size: 2rem; margin: 0 0 .5rem; }
p { line-height: 1.5; color: #c9b99a; }
a.button { display: inline-block; margin-top: 1.25rem; padding: .75rem 1.25rem;
  border-radius: .5rem; background: #f4ead8; color: #14110f; text-decoration: none;
  font-weight: 600; }
form { display: grid; gap: .6rem; margin-top: 1.25rem; }
input { padding: .7rem; border-radius: .5rem; border: 1px solid #4a3f30;
  background: #1e1915; color: #f4ead8; font-size: 1rem; }
button { padding: .75rem; border: 0; border-radius: .5rem; background: #f4ead8;
  color: #14110f; font-weight: 600; font-size: 1rem; cursor: pointer; }
.fine { margin-top: 2rem; font-size: .8rem; color: #8a7d68; }
"""


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title><style>{LOGIN_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


FORM_HTML = (
    '<form method="post" action="/login">'
    '<input name="username" placeholder="username" autocomplete="username" required>'
    '<input name="password" type="password" placeholder="password"'
    ' autocomplete="current-password" required>'
    '<button type="submit">Sign in</button></form>'
    '<p class="fine">or</p>'
)


def login_page(error: str | None = None, password_form: bool = False) -> str:
    joke = secrets.choice(JOKES)
    error_html = f"<p><strong>{error}</strong></p>" if error else ""
    body = (
        "<h1>Halt, mortal.</h1>"
        f"<p>{joke}</p>{error_html}"
        f"{FORM_HTML if password_form else ''}"
        '<a class="button" href="/auth/google/start">Sign in with Google</a>'
        '<p class="fine">Only approved accounts get past the velvet rope.</p>'
    )
    return _page("Station Dashboard - sign in", body)


def denied_page() -> str:
    body = (
        "<h1>Nice try.</h1>"
        "<p>That Google account is not on the guest list. The workers have been notified.</p>"
        '<a class="button" href="/login">Try another account</a>'
    )
    return _page("Station Dashboard - not on the list", body)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict[str, Any], secret: bytes) -> str:
    """Return `body.mac` where body is the base64 JSON payload and mac is HMAC-SHA256 of it."""
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def unsign(token: str, secret: bytes) -> dict[str, Any] | None:
    """The payload if the MAC checks out and the token has not expired, else None."""
    body, sep, mac = token.partition(".")
    if not sep:
        return None
    expected = _b64(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or float(payload.get("exp", 0)) < time.time():
        return None
    return payload


SCRYPT = {"n": 2**14, "r": 8, "p": 1}
MAX_FAILS = 5
MAX_FAILS_PER_USER = 20
MAX_BODY = 4096
FAIL_WINDOW_S = 900


def hash_password(password: str) -> str:
    """`scrypt$salt$hash`, the format of the users file."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), **SCRYPT)
        return hmac.compare_digest(actual, _unb64(digest))
    except ValueError:
        return False


DUMMY_HASH = hash_password("not-a-real-password")


def _read_secret(path: Path) -> bytes:
    text = path.expanduser().read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"secret file is empty: {path}")
    return text.encode("utf-8")


class GoogleLogin:
    """Google OAuth code flow, restricted to one verified email address."""

    def __init__(self, cfg: AuthConfig) -> None:
        self.client_id = cfg.google_client_id
        self.client_secret = _read_secret(cfg.google_client_secret_file).decode("utf-8")
        self.session_secret = _read_secret(cfg.session_secret_file)
        self.allowed_email = cfg.allowed_email.strip().lower()
        self.redirect_uri = cfg.public_base_url.rstrip("/") + "/auth/google/callback"
        self.session_s = cfg.session_hours * 3600
        self.users: dict[str, str] = {}
        if cfg.users_file is not None:
            raw = json.loads(cfg.users_file.expanduser().read_text(encoding="utf-8"))
            self.users = {str(k).lower(): str(v) for k, v in raw.items()}
        self.fails: dict[str, list[float]] = {}

    def throttled(self, key: str, limit: int) -> bool:
        now = time.time()
        recent = [t for t in self.fails.get(key, []) if now - t < FAIL_WINDOW_S]
        if recent:
            self.fails[key] = recent
        else:
            self.fails.pop(key, None)
        return len(recent) >= limit

    def password_ok(self, username: str, password: str) -> bool:
        stored = self.users.get(username.strip().lower())
        # Hash against a dummy when the user is unknown so timing does not reveal usernames.
        ok = check_password(password, stored or DUMMY_HASH)
        return stored is not None and ok

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTH_URL}?{query}"

    async def email_for_code(self, code: str) -> str | None:
        """Exchange the code for a token, then return the verified email, or None.

        Network errors and non-JSON replies count as a failed sign-in, not a server error.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                token = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": self.redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                if token.status_code != 200:
                    return None
                access = token.json().get("access_token")
                if not access:
                    return None
                info = await client.get(
                    GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access}"}
                )
            if info.status_code != 200:
                return None
            data = info.json()
        except (httpx.HTTPError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("email_verified") is not True:
            return None
        if not data.get("email"):
            return None
        return str(data["email"]).strip().lower()

    def session_email(self, request: Request) -> str | None:
        token = request.cookies.get(SESSION_COOKIE)
        payload = unsign(token, self.session_secret) if token else None
        if payload is None:
            return None
        if payload.get("email") == self.allowed_email:
            return self.allowed_email
        user = payload.get("user")
        if isinstance(user, str) and user in self.users:
            return user
        return None


def _is_public(request: Request) -> bool:
    return "cf-connecting-ip" in request.headers


def _is_open_path(path: str) -> bool:
    """Exact open paths, plus the icon folder. Dot segments are refused so `/icons/..` cannot
    slip past the gate."""
    if path in OPEN_PATHS:
        return True
    return path.startswith(OPEN_PREFIXES) and ".." not in path.split("/")


def install(app: FastAPI, cfg: Config) -> None:
    """Gate public requests and register the login routes. Local requests are never gated.

    When `[auth]` is missing, public requests get 503: the dashboard fails closed.
    """
    login = GoogleLogin(cfg.auth) if cfg.auth is not None else None

    @app.middleware("http")
    async def gate(request: Request, call_next: Any) -> Response:
        if not _is_public(request):
            return await call_next(request)
        if login is None:
            return PlainTextResponse("Public sign-in is not configured.", status_code=503)
        if _is_open_path(request.url.path) or login.session_email(request) is not None:
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "sign-in required"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    if login is None:
        return

    @app.get("/login", include_in_schema=False)
    def login_form() -> HTMLResponse:
        return HTMLResponse(login_page(password_form=bool(login.users)))

    @app.post("/login", include_in_schema=False, response_model=None)
    async def password_login(request: Request) -> Response:
        ip = "ip:" + request.headers.get("cf-connecting-ip", "")
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return HTMLResponse(login_page("That is a lot of password.", True), 413)
        raw = (await request.body())[:MAX_BODY].decode("utf-8", errors="replace")
        form = parse_qs(raw)
        username = form.get("username", [""])[0].strip().lower()
        password = form.get("password", [""])[0]
        user = "user:" + username
        if login.throttled(ip, MAX_FAILS) or login.throttled(user, MAX_FAILS_PER_USER):
            return HTMLResponse(
                login_page("Too many tries. Go stand in the corner for a bit.", True), 429
            )
        # Count the attempt before the slow hash so concurrent guesses cannot all slip through.
        now = time.time()
        login.fails.setdefault(ip, []).append(now)
        login.fails.setdefault(user, []).append(now)
        ok = bool(login.users) and await asyncio.to_thread(login.password_ok, username, password)
        if not ok:
            return HTMLResponse(login_page("Wrong name or password.", bool(login.users)), 401)
        login.fails.pop(ip, None)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            sign(
                {"user": username, "exp": time.time() + login.session_s},
                login.session_secret,
            ),
            max_age=int(login.session_s),
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/auth/google/start", include_in_schema=False)
    def start() -> RedirectResponse:
        state = secrets.token_urlsafe(24)
        response = RedirectResponse(login.authorize_url(state), status_code=302)
        response.set_cookie(
            STATE_COOKIE,
            sign({"state": state, "exp": time.time() + STATE_TTL_S}, login.session_secret),
            max_age=STATE_TTL_S,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/auth",
        )
        return response

    @app.get("/auth/google/callback", include_in_schema=False, response_model=None)
    async def callback(request: Request) -> Response:
        cookie = request.cookies.get(STATE_COOKIE)
        saved = unsign(cookie, login.session_secret) if cookie else None
        sent_state = request.query_params.get("state", "")
        code = request.query_params.get("code", "")
        if (
            saved is None
            or not code
            or not hmac.compare_digest(str(saved.get("state")), sent_state)
        ):
            return HTMLResponse(login_page("That sign-in did not check out. Try again."), 400)
        email = await login.email_for_code(code)
        if email is None:
            return HTMLResponse(login_page("Google did not confirm that account. Try again."), 400)
        if email != login.allowed_email:
            return HTMLResponse(denied_page(), status_code=403)
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            SESSION_COOKIE,
            sign({"email": email, "exp": time.time() + login.session_s}, login.session_secret),
            max_age=int(login.session_s),
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(
            STATE_COOKIE, path="/auth", secure=True, httponly=True, samesite="lax"
        )
        return response

    @app.get("/auth/logout", include_in_schema=False)
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response
