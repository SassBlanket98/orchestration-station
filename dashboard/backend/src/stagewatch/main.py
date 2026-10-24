"""App factory with the built SPA, and the `stagewatch` CLI entry point."""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from stagewatch import api, auth
from stagewatch.config import Config, load_config, load_quota_service_secret
from stagewatch.contracts import StoreProtocol
from stagewatch.quota import QuotaClient

# backend/src/stagewatch/main.py -> repo root is parents[3]
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


# The PWA manifest must not go out as application/octet-stream.
mimetypes.add_type("application/manifest+json", ".webmanifest")


def create_app(store: StoreProtocol, cfg: Config) -> FastAPI:
    """The API, plus the built SPA at `/` when `frontend/dist` exists.

    Public (tunnel) requests go through the Google sign-in gate; see `auth.install`.
    `app.state.quota_client` is None when quota_service is not fully configured (offline).
    """
    app = api.create_app(store, cfg)
    auth.install(app, cfg)
    app.state.quota_client = _quota_client(cfg)
    if (FRONTEND_DIST / "index.html").is_file():
        _mount_spa(app, FRONTEND_DIST)
    return app


def _quota_client(cfg: Config) -> QuotaClient | None:
    """A client only when both the URL and a secret are configured."""
    if cfg.quota_service_url is None:
        return None
    secret = load_quota_service_secret(cfg)
    if secret is None:
        return None
    return QuotaClient(cfg.quota_service_url, secret.get_secret_value())


def _mount_spa(app: FastAPI, dist: Path) -> None:
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate, headers=_cache_headers(full_path))
        return FileResponse(index, headers=NO_CACHE)


# The page and the service worker must be revalidated on every load, or a phone keeps an old
# index.html whose hashed assets no longer exist (a black screen). Hashed assets never change.
NO_CACHE = {"Cache-Control": "no-cache"}
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _cache_headers(path: str) -> dict[str, str]:
    return IMMUTABLE if path.startswith("assets/") else NO_CACHE


def main() -> None:
    import uvicorn

    from stagewatch.snapshot import SnapshotStore  # lazy: only the real server needs it

    cfg = load_config()
    app = create_app(SnapshotStore(cfg), cfg)
    uvicorn.run(app, host="127.0.0.1", port=cfg.port, log_level="info")
