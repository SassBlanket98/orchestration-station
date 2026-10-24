import io
import json
import unittest
from unittest.mock import patch

import web
import webdata
from model import GlobalSnapshot, Snapshot, Stage


def _serve(payload_fn, detail, port=4850, interval=0.1, stage=None, control=None):
    """Run run_serve with a fake server, returning (rc, request(path, request_headers=None, body=None) -> (status, headers, body)).
    A body makes it a POST to that path; without one it is a GET."""
    servers = []

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address
            self.handler = handler
            servers.append(self)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            pass

    with patch.object(web, "ThreadingHTTPServer", FakeServer):
        rc = web.run_serve(payload_fn, detail, stage, port=port, interval=interval, control_fn=control)
    handler = servers[0].handler

    def request(path, request_headers=None, body=None):
        instance = object.__new__(handler)
        instance.path = path
        instance.headers = request_headers or {}
        instance.rfile = io.BytesIO(body or b"")
        instance.wfile = io.BytesIO()
        headers = []
        instance.send_response = lambda code: setattr(instance, "status", code)
        instance.send_header = lambda key, value: headers.append((key, value))
        instance.end_headers = lambda: None
        instance.address_string = lambda: "127.0.0.1"
        if body is None: instance.do_GET()
        else: instance.do_POST()
        return instance.status, dict(headers), instance.wfile.getvalue()

    return rc, request


class WebServerTests(unittest.TestCase):
    def test_routes(self):
        payload = {"mode": "stage", "stages": [{"path": "/tmp", "workers": []}]}

        def detail(stage, name, since=None):
            return {"stage": stage, "name": name}

        rc, request = _serve(lambda: payload, detail, stage=lambda path: {"workers_full": [{"name": "worker"}]})
        self.assertEqual(rc, 0)

        status, headers, body = request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(request("/api/state?x=1")[0], 404)

        status, _, body = request("/api/worker?stage=%2Ftmp&name=worker")
        self.assertEqual((status, json.loads(body)), (200, {"stage": "/tmp", "name": "worker"}))
        self.assertEqual(request("/api/worker?stage=%2Ftmp&name=a%2Fb")[0], 400)   # unsafe name
        self.assertEqual(request("/api/worker?stage=%2Ftmp&name=worker&since=abc")[0], 400)
        self.assertEqual(request("/api/worker?stage=%2Ftmp&name=missing")[0], 404)  # unknown worker
        self.assertEqual(request("/api/worker?stage=%2Fnope&name=worker")[0], 404)  # unknown stage
        self.assertEqual(request("/api/worker?stage")[0], 404)
        self.assertEqual(request("/unknown")[0], 404)

    def test_solo_station_stage_is_empty_and_worker_404s(self):
        solo = Stage(path="/p/.stage#7", label="p", snap=Snapshot(), live=0, lead_state="live", lead_pid=7, solo=True)
        gs = GlobalSnapshot(stages=[solo])
        payload = webdata.global_payload(gs)

        rc, request = _serve(lambda: payload, webdata.worker_detail,
                             stage=lambda path: webdata.stage_payload(gs, path))
        status, _, body = request("/api/stage?path=%2Fp%2F.stage%237")
        self.assertEqual((status, json.loads(body)["workers_full"]), (200, []))
        self.assertEqual(request("/api/worker?stage=%2Fp%2F.stage%237&name=w")[0], 404)
        with self.assertRaises(ValueError):   # the detail reader itself refuses a path with no directory
            webdata.worker_detail("/p/.stage#7", "w")

    def test_web_ui_paths_are_gone(self):
        _, request = _serve(lambda: {"mode": "global", "stages": []}, lambda s, n: {})
        for path in ("/", "/sw.js", "/assets/x", "/assets/manifest.webmanifest", "/icon-192.png"):
            status, headers, body = request(path)
            self.assertEqual(status, 404, path)
            self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8", path)
            self.assertEqual(body, b"Not found", path)
        status, headers, body = request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"mode": "global", "stages": []})

    def test_generated_does_not_change_etag(self):
        _, first = web._canonical({"mode": "global", "generated": 1, "stages": []})
        _, second = web._canonical({"mode": "global", "generated": 2, "stages": []})
        self.assertEqual(first, second)

    def test_state_503_when_payload_fails(self):
        def boom():
            raise RuntimeError("boom")

        rc, request = _serve(boom, lambda s, n: {}, interval=0.05)
        self.assertEqual(request("/api/state")[0], 503)

    def test_bind_error_returns_1(self):
        class Boom:
            def __init__(self, *args, **kwargs):
                raise OSError(98, "Address already in use")

        with patch.object(web, "ThreadingHTTPServer", Boom):
            self.assertEqual(web.run_serve(lambda: {"stages": []}, lambda s, n: {}, port=4852), 1)

    def test_periodic_cache_returns_copy(self):
        cache = web.StateCache(lambda: {"items": [1]}, 10)
        try:
            result = cache.get()
            result["items"].append(2)
            self.assertEqual(cache.get(), {"items": [1]})
        finally:
            cache.close()

    def test_cache_failure_returns_empty(self):
        def boom():
            raise RuntimeError("boom")

        cache = web.StateCache(boom, 10)
        try:
            self.assertEqual(cache.get(), {})
        finally:
            cache.close()

    def test_control_post_pins_idle_and_get_shows_it(self):
        state = {"mode": "global", "stages": [{"path": "/p", "state": "closed"}]}
        calls = []

        def control(path, action):
            calls.append((path, action))
            state["stages"][0]["state"] = "idle"
            return {"pinned": "idle", "dismissed": False}

        _, request = _serve(lambda: state, lambda s, n: {}, control=control)
        body = json.dumps({"path": "/p", "action": "idle"}).encode()
        status, _, raw = request("/api/control", {"Content-Length": str(len(body)), "Content-Type": "application/json"}, body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {"ok": True, "pinned": "idle", "dismissed": False})
        self.assertEqual(calls, [("/p", "idle")])
        self.assertEqual(json.loads(request("/api/state")[2])["stages"][0]["state"], "idle")

    def test_control_post_rejects_bad_requests(self):
        calls = []

        def control(path, action):
            calls.append((path, action))
            return {"pinned": "", "dismissed": False}

        _, request = _serve(lambda: {"mode": "global", "stages": [{"path": "/p"}]}, lambda s, n: {}, control=control)

        def post(raw, path="/api/control"):
            return request(path, {"Content-Length": str(len(raw)), "Content-Type": "application/json"}, raw)[0]

        self.assertEqual(request("/api/control", {"Content-Type": "application/json"}, b'{"path": "/p", "action": "idle"}')[0], 400)   # no Content-Length
        self.assertEqual(request("/api/control", {"Content-Length": "32", "Content-Type": "text/plain"}, b'{"path": "/p", "action": "idle"}')[0], 415)   # a cross-site form post cannot send json
        self.assertEqual(post(b"not json"), 400)
        self.assertEqual(post(b"[]"), 400)
        self.assertEqual(post(b'{"path": 3, "action": "idle"}'), 400)
        self.assertEqual(post(b'{"path": "/p", "action": "boom"}'), 400)
        self.assertEqual(post(b'{"path": "/p/../x", "action": "idle"}'), 400)
        self.assertEqual(post(b"{" + b" " * 4096 + b"}"), 413)
        self.assertEqual(post(b'{"path": "/nope", "action": "idle"}'), 404)
        self.assertEqual(post(b'{"path": "/p", "action": "idle"}', path="/api/state"), 404)
        self.assertEqual(request("/api/control")[0], 404)   # GET on the control route is not a route
        self.assertEqual(calls, [])

    def test_control_post_without_a_control_callable_is_404(self):
        _, request = _serve(lambda: {"stages": [{"path": "/p"}]}, lambda s, n: {})
        raw = b'{"path": "/p", "action": "idle"}'
        self.assertEqual(request("/api/control", {"Content-Length": str(len(raw)), "Content-Type": "application/json"}, raw)[0], 404)


if __name__ == "__main__":
    unittest.main()


class EtagVolatileTest(unittest.TestCase):
    def test_quiet_s_does_not_change_the_etag(self) -> None:
        one = {"stages": [{"workers": [{"name": "w", "quiet_s": 1.5}]}]}
        two = {"stages": [{"workers": [{"name": "w", "quiet_s": 9.5}]}]}
        self.assertEqual(web._canonical(one)[1], web._canonical(two)[1])
