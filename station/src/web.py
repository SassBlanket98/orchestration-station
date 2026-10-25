from __future__ import annotations

import copy
import gzip
import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class StateCache:
    def __init__(self, payload_fn, interval: float, idle_s: float = 30.0) -> None:
        self._payload_fn, self._interval, self._idle_s = payload_fn, max(.01, interval), max(.01, idle_s)
        self._lock = threading.Lock(); self._value = None; self._updated = 0.0
    def get(self):
        with self._lock:
            now = time.monotonic()
            if self._value is None or now - self._updated >= self._interval or now - self._updated >= self._idle_s:
                try:
                    value = self._payload_fn()
                    if not isinstance(value, dict): raise TypeError("payload_fn must return a dict")
                    self._value = copy.deepcopy(value); self._updated = time.monotonic()
                except Exception as exc:  # noqa: BLE001 - the state cache serves the last good value on any payload error
                    print(f"station: state refresh failed: {exc}", file=sys.stderr)
                    if self._value is None: return {}
                    errors = self._value.setdefault("errors", [])
                    message = f"collector: {type(exc).__name__}: {exc}"
                    if message not in errors: errors.append(message)
            return copy.deepcopy(self._value or {})
    def expire(self):
        with self._lock: self._updated = float("-inf")   # the next get() reads the payload again, so a POST shows at once
    def close(self): pass


_VOLATILE = frozenset(("now", "generated", "quiet_s"))   # tick on every collection; not content
_CONTROL_ACTIONS = frozenset(("idle", "active", "clear", "dismiss"))   # POST /api/control: pin idle, pin active, clear the pin, hide


def _strip_volatile(value):
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _canonical(value):
    clean = _strip_volatile(value)
    body = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return body, '"' + hashlib.sha1(body).hexdigest()[:16] + '"'


def run_serve(payload_fn, detail_fn, stage_fn=None, *, host="127.0.0.1", port=8743, interval=2.0, idle_s=30.0, control_fn=None) -> int:
    cache = StateCache(payload_fn, interval, idle_s)
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type, cache_control="no-store", extra=()):
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", cache_control)
            for k, v in extra: self.send_header(k, v)
            self.end_headers()
            if body: self.wfile.write(body)
        def _json(self, status, value):
            raw = json.dumps(value, ensure_ascii=False).encode(); self._send(status, raw, "application/json")
        def _api_json(self, value):
            _body, etag = _canonical(value)
            headers = [("ETag", etag), ("Vary", "Accept-Encoding")]
            request_headers = getattr(self, "headers", {})
            if request_headers.get("If-None-Match") == etag:
                return self._send(304, b"", "application/json", "no-cache", headers)
            raw = json.dumps(value, ensure_ascii=False).encode(); extra = headers
            if len(raw) >= 1024 and "gzip" in request_headers.get("Accept-Encoding", ""):
                raw = gzip.compress(raw); extra = headers + [("Content-Encoding", "gzip")]
            self._send(200, raw, "application/json", "no-cache", extra)
        def do_GET(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.path == "/api/state" and not parsed.query:
                    value = cache.get()
                    if not value: return self._send(503, b"state unavailable", "text/plain; charset=utf-8")
                    return self._api_json(value)
                if parsed.path in ("/api/stage", "/api/worker"):
                    try: q = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
                    except ValueError: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                    if parsed.path == "/api/stage":
                        if set(q) != {"path"} or len(q["path"]) != 1: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                        path = q["path"][0]; value = cache.get()
                        if "\\" in path or ".." in path.split("/"): return self._json(400, {"error": "invalid stage path"})
                        if path not in {s.get("path") for s in value.get("stages", [])}: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                        if stage_fn is None: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                        result = stage_fn(path)
                        if result is None: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                        return self._api_json(result)
                    if set(q) - {"stage", "name", "since"} or not {"stage", "name"} <= set(q) or any(len(v) != 1 for v in q.values()): return self._send(404, b"Not found", "text/plain; charset=utf-8")
                    since = q.get("since", [None])[0]
                    if since is not None and (not since.isdecimal() or str(int(since)) != since):
                        return self._json(400, {"error": "invalid since"})
                    stage, name = q["stage"][0], q["name"][0]
                    if "/" in name or "\\" in name or ".." in name: return self._json(400, {"error": "invalid worker name"})
                    state = cache.get(); stages = {s.get("path"): s for s in state.get("stages", [])}
                    if stage not in stages: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                    if stage_fn is not None:
                        full_stage = stage_fn(stage)
                        rows = (full_stage or {}).get("workers_full", [])
                        if name not in {worker.get("name") for worker in rows}:
                            return self._send(404, b"Not found", "text/plain; charset=utf-8")
                    try:
                        result = detail_fn(stage, name, since)
                    except (ValueError, KeyError, FileNotFoundError):
                        return self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return self._api_json(result)
                self._send(404, b"Not found", "text/plain; charset=utf-8")
            except Exception as exc:  # noqa: BLE001 - a request must get a 500, never kill the server
                print(f"station: request failed: {exc}", file=sys.stderr); self._send(500, b"Internal server error", "text/plain; charset=utf-8")
        def do_POST(self):
            try:
                if urlsplit(self.path).path != "/api/control" or control_fn is None: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                if "application/json" not in self.headers.get("Content-Type", "").lower(): return self._json(415, {"ok": False, "error": "content-type must be application/json"})   # a cross-site form post cannot send it
                try: length = int(self.headers.get("Content-Length", ""))
                except ValueError: length = -1
                if length < 0: return self._json(400, {"ok": False, "error": "content-length required"})
                if length > 4096: return self._json(413, {"ok": False, "error": "body too large"})
                try: body = json.loads(self.rfile.read(length))
                except ValueError: return self._json(400, {"ok": False, "error": "invalid json"})
                path = body.get("path") if isinstance(body, dict) else None
                action = body.get("action") if isinstance(body, dict) else None
                if not isinstance(path, str) or action not in _CONTROL_ACTIONS: return self._json(400, {"ok": False, "error": "need path and action"})
                if "\\" in path or ".." in path.split("/"): return self._json(400, {"ok": False, "error": "invalid stage path"})
                if path not in {s.get("path") for s in cache.get().get("stages", [])}: return self._send(404, b"Not found", "text/plain; charset=utf-8")
                result = control_fn(path, action); cache.expire()
                return self._json(200, {"ok": True, **result})
            except Exception as exc:  # noqa: BLE001 - a request must get a 500, never kill the server
                print(f"station: request failed: {exc}", file=sys.stderr); self._send(500, b"Internal server error", "text/plain; charset=utf-8")
        def log_message(self, fmt, *args): print(f"station: {self.address_string()} - {fmt % args}", file=sys.stderr)
    try: server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"station: cannot serve on {host}:{port}: {exc}", file=sys.stderr); return 1
    print(f"station serving on http://{host}:{port}", file=sys.stderr, flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: return 0
    finally: server.server_close(); cache.close()
    return 0
