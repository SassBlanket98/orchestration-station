"""VAPID-backed push subscriptions and alert deduplication."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

log = logging.getLogger(__name__)
MAX_SUBSCRIPTIONS = 10
MAX_SUBSCRIPTION_BYTES = 4096


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_vapid_keys(path: Path | str) -> str:
    """Write a private P-256 PEM with owner-only permissions and return its public key."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    vapid = Vapid()
    vapid.generate_keys()
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(vapid.private_pem())
        os.replace(tmp_name, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    raw_public = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(raw_public)


def _read_vapid(path: Path | None) -> tuple[Vapid | None, str | None]:
    if path is None or not path.expanduser().is_file():
        return None, None
    try:
        vapid = Vapid.from_file(str(path.expanduser()))
        raw_public = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return vapid, _b64url(raw_public)
    except Exception:  # noqa: BLE001 - malformed PEMs have several library exception types
        return None, None


# Browser push services. Any other host is refused so the server never POSTs to an
# address a client picked (loopback, LAN, metadata endpoints).
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")


def _known_push_service(endpoint: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES)


def _remaining(resets_at: float | None, now_ms: float) -> str:
    if resets_at is None:
        return "resets soon"
    minutes = max(0, int((resets_at - now_ms) / 60_000))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"resets in {hours}h {minutes:02d}m"
    return f"resets in {minutes}m"


_LIVE_WORKER = {"running", "quiet", "stalled"}


def _has_live_worker(stage: dict[str, Any]) -> bool:
    workers = stage.get("workers")
    return isinstance(workers, list) and any(
        isinstance(worker, dict) and str(worker.get("state", "")).lower() in _LIVE_WORKER
        for worker in workers
    )


def waiting_sessions(stages: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
    """Live leads quiet for over two minutes whose own workers and spawned sessions are all
    stopped. A lead with no activity time at all is unknown, not waiting."""
    current = time.time() if now is None else now
    live_ids = {s.get("session_id") for s in stages if s.get("lead_state") == "live"}
    busy_parents: set[str] = set()
    for stage in stages:
        parent = stage.get("parent_session")
        if isinstance(parent, str) and parent and (
            _has_live_worker(stage) or stage.get("session_id") in live_ids
        ):
            busy_parents.add(parent)
    names: list[str] = []
    for stage in stages:
        if stage.get("lead_state") != "live" or _has_live_worker(stage):
            continue
        # Only sessions you can answer from the Claude app; bots and headless swarms never wait.
        app = stage.get("app")
        if not (isinstance(app, dict) and app.get("on") is True):
            continue
        if stage.get("session_id") in busy_parents:
            continue
        last_activity = stage.get("last_activity", stage.get("last_activity_at"))
        try:
            recent = current - float(last_activity) <= 120
            if not last_activity or float(last_activity) <= 0 or recent:
                continue
        except (TypeError, ValueError):
            continue
        name = stage.get("session_name") or stage.get("label") or stage.get("path")
        if isinstance(name, str):
            names.append(name)
    return {"waiting": len(names), "names": names}


class PushManager:
    """Owns the private key reference, subscription file and persistent alert state."""

    def __init__(self, store_file: Path, private_key_file: Path | None, subject: str) -> None:
        self.store_file = store_file.expanduser()
        self.private_key_file = private_key_file.expanduser() if private_key_file else None
        self.subject = subject
        self._lock = threading.RLock()

    @property
    def public_key(self) -> str | None:
        return _read_vapid(self.private_key_file)[1]

    @property
    def configured(self) -> bool:
        return self.public_key is not None

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.store_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("subscriptions", [])
        value.setdefault("dedupe", {})
        value.setdefault("live_stages", [])
        value.setdefault("finished_paths", [])
        value.setdefault("baseline_recorded", False)
        return value

    def _save(self, data: dict[str, Any]) -> None:
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.store_file.name}.", dir=self.store_file.parent
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp_name, self.store_file)
            os.chmod(self.store_file, 0o600)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _subscription_id(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def validate_subscription(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("invalid subscription")
        endpoint = payload.get("endpoint")
        keys = payload.get("keys")
        if not isinstance(endpoint, str) or not _known_push_service(endpoint):
            raise ValueError("endpoint must be a browser push service")
        if not isinstance(keys, dict) or any(
            not isinstance(keys.get(name), str) or not keys[name] for name in ("p256dh", "auth")
        ):
            raise ValueError("subscription keys are required")
        return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}

    def subscribe(self, payload: object) -> None:
        subscription = self.validate_subscription(payload)
        with self._lock:
            data = self._load()
            subscriptions = data["subscriptions"]
            if not isinstance(subscriptions, list):
                subscriptions = data["subscriptions"] = []
            endpoint = subscription["endpoint"]
            old = next((item for item in subscriptions if item.get("endpoint") == endpoint), None)
            if old is None and len(subscriptions) >= MAX_SUBSCRIPTIONS:
                raise ValueError("subscription limit reached")
            if old is not None:
                subscriptions.remove(old)
            subscription["id"] = self._subscription_id(endpoint)
            subscriptions.append(subscription)
            self._save(data)

    def unsubscribe(self, endpoint: str) -> None:
        with self._lock:
            data = self._load()
            data["subscriptions"] = [
                sub
                for sub in data["subscriptions"]
                if not isinstance(sub, dict) or sub.get("endpoint") != endpoint
            ]
            self._save(data)

    def usage_alerts(
        self, subscriptions: list[dict[str, Any]], now_ms: float | None = None
    ) -> list[dict[str, Any]]:
        now = time.time() * 1000 if now_ms is None else now_ms
        alerts: list[dict[str, Any]] = []
        with self._lock:
            data = self._load()
            dedupe = data["dedupe"]
            for subscription in subscriptions:
                account = subscription.get("id") or subscription.get("account") or "subscription"
                sid = subscription.get("id") or self._subscription_id(
                    str(subscription.get("endpoint", ""))
                )
                for limit in subscription.get("limits", []):
                    if not isinstance(limit, dict) or not isinstance(
                        limit.get("used_pct"), (int, float)
                    ):
                        continue
                    used = float(limit["used_pct"])
                    window = str(limit.get("name") or limit.get("window") or "window")
                    resets_at = limit.get("resets_at")
                    for threshold in (80, 95):
                        if used < threshold:
                            continue
                        reset = str(resets_at or "unknown")
                        key = f"usage:{sid}:{window}:{threshold}:{reset}"
                        if key in dedupe:
                            continue
                        dedupe[key] = True
                        pct = round(used)
                        alerts.append(
                            {
                                "title": f"Usage {pct}% · {account} {window}",
                                "body": _remaining(resets_at, now),
                                "tag": key,
                            }
                        )
            if alerts:
                self._save(data)
        return alerts

    def finished_alerts(self, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        with self._lock:
            data = self._load()
            previous_live = list(data["live_stages"])
            previous_finished = list(data["finished_paths"])
            previous_baseline = data["baseline_recorded"]
            live = set(data["live_stages"])
            finished = set(data["finished_paths"])
            current_live: set[str] = set()
            for stage in stages:
                path = stage.get("path")
                if not isinstance(path, str):
                    continue
                if stage.get("lead_state") == "live":
                    current_live.add(path)
                    continue
                if (
                    data["baseline_recorded"]
                    and path in live
                    and path not in finished
                    and stage.get("lead_state") == "gone"
                    and stage.get("outcome") == "success"
                ):
                    name = stage.get("session_name") or stage.get("label") or path
                    alerts.append(
                        {
                            "title": f"Finished · {name}",
                            "body": "Session finished",
                            "tag": f"finished:{path}",
                        }
                    )
                    finished.add(path)
            if not data["baseline_recorded"]:
                current_live |= {p for p in live if p not in {s.get("path") for s in stages}}
                data["baseline_recorded"] = True
            data["live_stages"] = sorted(current_live)
            data["finished_paths"] = sorted(finished)
            if (
                data["live_stages"] != previous_live
                or data["finished_paths"] != previous_finished
                or data["baseline_recorded"] != previous_baseline
            ):
                self._save(data)
        return alerts

    def _send(self, subscription: dict[str, Any], payload: dict[str, Any]) -> None:
        from pywebpush import webpush

        key, _public = _read_vapid(self.private_key_file)
        if key is None:
            return
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, separators=(",", ":")),
            vapid_private_key=str(self.private_key_file),
            vapid_claims={"sub": self.subject},
            ttl=3600,
        )

    @staticmethod
    def _status_code(error: Exception) -> int | None:
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None) or getattr(error, "status_code", None)

    def deliver(self, alerts: list[dict[str, Any]], badge: dict[str, Any]) -> bool:
        """Send every alert to every browser. True when at least one send succeeded."""
        if not alerts or not self.configured:
            return False
        sent = False
        with self._lock:
            data = self._load()
            subscriptions = list(data["subscriptions"])
        gone: set[str] = set()
        for subscription in subscriptions:
            for alert in alerts:
                payload = {**alert, "badge": badge["waiting"], "names": badge["names"]}
                try:
                    self._send(subscription, payload)
                    sent = True
                except Exception as exc:  # noqa: BLE001 - delivery must not stop the poll loop
                    if self._status_code(exc) in (404, 410):
                        gone.add(subscription["endpoint"])
                    else:
                        log.warning("push delivery failed (%s)", type(exc).__name__)
        if gone:
            with self._lock:
                data = self._load()
                data["subscriptions"] = [
                    s for s in data["subscriptions"] if s.get("endpoint") not in gone
                ]
                self._save(data)
        return sent

    def _forget(self, alerts: list[dict[str, Any]]) -> None:
        """Undo the dedupe record of alerts nobody received, so the next poll retries them."""
        tags = {alert["tag"] for alert in alerts}
        with self._lock:
            data = self._load()
            for tag in tags:
                data["dedupe"].pop(tag, None)
            data["finished_paths"] = [
                p for p in data["finished_paths"] if f"finished:{p}" not in tags
            ]
            self._save(data)

    def poll(
        self,
        subscriptions: list[dict[str, Any]],
        stages: list[dict[str, Any]] | None,
        now: float | None = None,
    ) -> None:
        """`stages` is None while Monitor is offline: no transitions are recorded then."""
        badge = waiting_sessions(stages or [], now)
        alerts = self.usage_alerts(subscriptions, None if now is None else now * 1000)
        if stages is not None:
            alerts.extend(self.finished_alerts(stages))
        if alerts and not self.deliver(alerts, badge):
            self._forget(alerts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a VAPID key pair")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="write a private key and print its public key")
    init.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        print(generate_vapid_keys(args.path))


if __name__ == "__main__":
    main()
