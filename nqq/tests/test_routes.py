"""nqq end-to-end route smoke test — starts the real server in solo mode and hits every
GET route plus the safe POST routes, asserting NONE return a 5xx. This catches integration
regressions (a route wired to a renamed field, a module that raises on the hot path) that
the unit tests miss. Pure stdlib; no network required (data falls back to synthetic).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

API = os.path.join(os.path.dirname(__file__), "..", "api")
PORT = 8931
BASE = f"http://localhost:{PORT}"

GET_ROUTES = [
    "/api/health", "/api/me", "/api/console", "/api/leaderboard", "/api/feed",
    "/api/signal/families", "/api/console/symbol?sym=SPY", "/api/signal/eval?family=momentum&sym=SPY",
    "/api/scorecard?family=momentum", "/api/discovery", "/api/walkforward?family=momentum",
    "/api/monitor?family=momentum", "/api/signal/lab", "/api/evolve", "/api/journal",
    "/api/orders", "/api/voices", "/api/quotes?syms=SPY", "/api/providers", "/api/rooms",
    "/api/micro?sym=SPY", "/api/library", "/api/compare?a=momentum&b=rsi", "/api/brief",
    "/api/journey", "/api/data", "/api/workspace/export", "/api/ready", "/api/evidence",
    "/api/ledger",
]

# (path, body) — POST routes with no destructive side effects in solo mode
POST_ROUTES = [
    ("/api/council/review", {"sym": "SPY", "side": "LONG"}),
    ("/api/portfolio", {"strategies": [{"family": "momentum"}, {"family": "rsi"}]}),
    ("/api/ai/parse", {"text": "buy AAPL when 20-day momentum is strong"}),
    ("/api/orders/place", {"sym": "SPY", "side": "BUY", "qty": 5}),
]


def _get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=8) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class TestRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = tempfile.mktemp(suffix=".db")
        env = dict(os.environ, NQQ_DB=cls.db, NQQ_MULTIUSER="0", NQQ_PORT=str(PORT))
        cls.proc = subprocess.Popen([sys.executable, "server.py"], cwd=API, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):                       # wait for readiness
            try:
                if _get("/api/health")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        try:
            os.remove(cls.db)
        except OSError:
            pass

    def test_gui_served(self):
        st, body = _get("/")
        self.assertEqual(st, 200)
        self.assertIn(b"<html", body.lower())

    def test_all_get_routes_no_5xx(self):
        failures = []
        for path in GET_ROUTES:
            st, body = _get(path)
            if st >= 500:
                failures.append(f"{path} -> {st}: {body[:120]}")
        self.assertEqual(failures, [], "GET routes returned 5xx:\n" + "\n".join(failures))

    def test_safe_post_routes_no_5xx(self):
        failures = []
        for path, body in POST_ROUTES:
            st, resp = _post(path, body)
            if st >= 500:
                failures.append(f"{path} -> {st}: {resp[:120]}")
        self.assertEqual(failures, [], "POST routes returned 5xx:\n" + "\n".join(failures))

    def test_oversized_body_rejected(self):
        # The cap must REFUSE an oversized body without crashing the server. Refusal may
        # surface as a clean 413 or, because the server responds before draining the 5MB
        # upload, as a connection error on the client's write — both mean "rejected".
        rejected = False
        try:
            st, _ = _post("/api/orders/place", {"x": "A" * 5_000_000})
            rejected = st == 413
        except urllib.error.URLError:
            rejected = True
        self.assertTrue(rejected, "oversized body was not rejected")
        # and the server must still be alive afterwards (didn't crash the thread/process)
        self.assertEqual(_get("/api/health")[0], 200)


if __name__ == "__main__":
    unittest.main()
