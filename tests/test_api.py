"""QuantDesk gating tests — stdlib only (unittest + urllib against a live server thread).
Covers every hard gate from the blueprint DoDs: promotion 409s, go-live validation,
kill-switch confirmation, spec blockers, spine validation, and the AI parser.
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["QD_DB"] = ":memory:"
os.environ["QD_PORT"] = "8790"
os.environ["QD_MULTIUSER"] = "0"   # tests run solo regardless of .env
os.environ["QD_NO_DEMO"] = "1"     # no demo seeding in fixtures

import logic  # noqa: E402
import server  # noqa: E402

BASE = "http://127.0.0.1:8790"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestQuantDesk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 8790), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    # ---- M1 DoD: promotion gates ----
    def test_promote_blocked_when_gate_block(self):
        ideas = req("GET", "/api/ideas")[1]
        blocked = next(i for i in ideas if i["gate"] == "block")
        code, body = req("POST", f"/api/ideas/{blocked['id']}/promote")
        self.assertEqual(code, 409)
        self.assertIn("gate blocked", body["error"])

    def test_promote_stage5_requires_wizard(self):
        ideas = req("GET", "/api/ideas")[1]
        paper = next(i for i in ideas if i["stage"] == 5)
        code, body = req("POST", f"/api/ideas/{paper['id']}/promote")
        self.assertEqual(code, 409)
        self.assertIn("Wizard", body["error"])

    def test_promote_pass_gate_succeeds_then_blocks(self):
        code, doc = req("POST", "/api/ideas", {
            "name": "test-idea-v1", "hyp": "a perfectly valid test hypothesis with detail",
            "uni": "SPY", "kill": "test kill criterion", "ac": "eq"})
        self.assertEqual(code, 201)
        # force gate pass via direct store access, promote, then next promote must 409
        idea = server.store.get_doc("ideas", doc["id"])
        idea["gate"] = "pass"
        server.store.put_doc("ideas", doc["id"], idea)
        code, promoted = req("POST", f"/api/ideas/{doc['id']}/promote")
        self.assertEqual(code, 200)
        self.assertEqual(promoted["stage"], 2)
        self.assertEqual(promoted["gate"], "block")  # gate resets every stage
        code, body = req("POST", f"/api/ideas/{doc['id']}/promote")
        self.assertEqual(code, 409)

    # ---- M1 DoD: go-live validation ----
    def test_golive_rejects_missing_checklist(self):
        code, body = req("POST", "/api/golive", {
            "venue": "IBKR", "strategy": "momo-etf-v3",
            "confirm": "IBKR GO LIVE", "checklist": [True] * 9})
        self.assertEqual(code, 409)

    def test_golive_rejects_wrong_phrase(self):
        code, body = req("POST", "/api/golive", {
            "venue": "IBKR", "strategy": "momo-etf-v3",
            "confirm": "ibkr go live", "checklist": [True] * 10})
        self.assertEqual(code, 403)

    def test_golive_rejects_failed_parity(self):
        code, body = req("POST", "/api/golive", {
            "venue": "IBKR", "strategy": "pairs-stat-v1",
            "confirm": "IBKR GO LIVE", "checklist": [True] * 10})
        self.assertEqual(code, 409)
        self.assertIn("parity", body["error"])

    def test_golive_accepts_valid(self):
        code, body = req("POST", "/api/golive", {
            "venue": "IBKR", "strategy": "momo-etf-v3",
            "confirm": "IBKR GO LIVE", "checklist": [True] * 10})
        self.assertEqual(code, 200)
        self.assertTrue(body["staged"])
        audit = req("GET", "/api/audit")[1]
        self.assertTrue(any(a["kind"] == "golive" for a in audit))
        server.store.set_kv("mode", "paper")  # reset for other tests

    # ---- M1 DoD: kill switch ----
    def test_kill_rejects_wrong_confirm(self):
        code, _ = req("POST", "/api/kill", {"confirm": "flatten"})
        self.assertEqual(code, 400)

    def test_kill_accepts_and_audits(self):
        code, body = req("POST", "/api/kill", {"confirm": "FLATTEN"})
        self.assertEqual(code, 200)
        self.assertTrue(body["halted"])
        server.store.set_kv("halted", "0")

    # ---- M12 DoD: spec blockers ----
    def test_spec_without_kill_criterion_422(self):
        spec, _ = logic.ai_parse("Buy AAPL on momentum breakout over 20 minutes.")
        code, body = req("POST", "/api/ideas/from-spec", {"spec": spec})
        self.assertEqual(code, 422)
        self.assertIn("kill", body["error"])

    def test_full_spec_creates_idea(self):
        text = ("Buy NVDA when order flow imbalance stays above 0.7 for 300ms; "
                "exit at +5 bps or -3 bps stop, or after 20 seconds. Size 3% of equity. "
                "Kill the strategy if hit rate drops below 51% over a week.")
        code, parsed = req("POST", "/api/ai/parse", {"text": text})
        self.assertEqual(code, 200)
        self.assertEqual(parsed["spec"]["universe"], ["NVDA"])
        self.assertEqual(parsed["spec"]["exit"]["stop_bps"], -3)
        code, idea = req("POST", "/api/ideas/from-spec", {"spec": parsed["spec"], "text": text})
        self.assertEqual(code, 201)
        self.assertEqual(idea["stage"], 1)

    # ---- M10 DoD: spine ----
    def test_spine_add_and_duplicate(self):
        code, doc = req("POST", "/api/spine", {"symbol": "nvda", "ac": "eq", "tiers": ["ref", "daily", "l2"]})
        self.assertEqual(code, 201)
        self.assertEqual(doc["s"], "NVDA")
        self.assertEqual(doc["spine"], "full")
        code, _ = req("POST", "/api/spine", {"symbol": "NVDA"})
        self.assertEqual(code, 409)

    # ---- M13 DoD: library ----
    def test_library_has_30_with_specs(self):
        code, lib = req("GET", "/api/library")
        self.assertEqual(code, 200)
        self.assertEqual(lib["count"], 30)
        self.assertEqual(len(lib["spec_files"]), 30)

    # ---- M14: notes/alerts/pnl ----
    def test_note_and_alert_crud(self):
        code, _ = req("POST", "/api/notes", {"txt": "test note", "tag": "observation"})
        self.assertEqual(code, 201)
        code, _ = req("POST", "/api/notes", {"txt": ""})
        self.assertEqual(code, 422)
        code, al = req("POST", "/api/alerts", {"r": "SPY < 600", "ch": "email"})
        self.assertEqual(code, 201)
        code, toggled = req("POST", f"/api/alerts/{al['id']}/toggle")
        self.assertEqual(code, 200)
        self.assertFalse(toggled["on"])

    def test_daily_pnl_weekends_null(self):
        code, pnl = req("GET", "/api/pnl/daily")
        self.assertEqual(code, 200)
        import datetime
        for d, v in pnl.items():
            wd = datetime.date(2026, 7, int(d)).weekday()
            if wd >= 5:
                self.assertIsNone(v)

    def test_wash_sale_detection(self):
        flags = logic.wash_sale_flags([
            {"date": "2026-06-12", "sym": "QQQ", "side": "SELL", "qty": 10, "pnl": -120},
            {"date": "2026-06-30", "sym": "QQQ", "side": "BUY", "qty": 10, "pnl": 0},
            {"date": "2026-06-12", "sym": "SPY", "side": "SELL", "qty": 10, "pnl": 50},
        ])
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["sym"], "QQQ")

    # ---- bootstrap serves everything the GUI merges ----
    def test_bootstrap_shape(self):
        code, b = req("GET", "/api/bootstrap")
        self.assertEqual(code, 200)
        for k in ("ideas", "symbols", "venues", "positions", "strategies", "feed",
                  "openOrders", "orderHist", "accounts", "costs", "econ", "setup",
                  "notes", "alerts", "mode"):
            self.assertIn(k, b)


if __name__ == "__main__":
    unittest.main()
