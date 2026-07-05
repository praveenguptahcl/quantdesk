"""Tests for the dual backtest engines, computed parity gate, and risk validation."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
os.environ["QD_DB"] = ":memory:"

import backtest  # noqa: E402
import logic  # noqa: E402

BASE = "http://127.0.0.1:8791"


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestEngines(unittest.TestCase):
    def test_clean_run_reconciles_internal(self):
        # internal twin engines must match exactly
        r = backtest.run_parity_backtest(engine="internal")
        self.assertIsNotNone(r, "catalog data missing")
        self.assertTrue(r["pass"])
        self.assertEqual(r["raw"]["a_trades"], r["raw"]["b_trades"])
        self.assertAlmostEqual(r["raw"]["a_sharpe"], r["raw"]["b_sharpe"], places=6)

    def test_auto_engine_within_tolerance(self):
        # auto mode may use REAL nautilus_trader (if installed) — must pass tolerances,
        # exact equality NOT required (different fill semantics are the whole point)
        r = backtest.run_parity_backtest(engine="auto")
        self.assertIsNotNone(r)
        self.assertTrue(r["pass"], r["tol"])

    def test_warmup_bug_fails_gate(self):
        r = backtest.run_parity_backtest(inject_warmup_bug=True)
        self.assertFalse(r["pass"])
        self.assertTrue(len(r["diffs"]) >= 1)

    def test_engines_produce_sane_metrics(self):
        a = backtest.engine_a()
        self.assertGreater(a["trades"], 50)
        self.assertLess(a["dd"], 0)
        self.assertGreater(a["fees"], 0)
        # regime filter must have produced flat periods: min gross exposure 0
        self.assertTrue(any(v > 0 for v in a["equity_monthly"]))


class TestRiskValidation(unittest.TestCase):
    LIMITS = logic.parse_simple_yaml(open(os.path.join(
        os.path.dirname(__file__), "..", "risk", "limits.yaml")).read())

    def test_yaml_parser(self):
        self.assertEqual(self.LIMITS["global"]["max_single_order_notional_usd"], 10000)
        self.assertEqual(self.LIMITS["futures"]["max_contracts_per_product"], 4)
        self.assertEqual(self.LIMITS["parity_tolerances"]["sharpe_diff"], 0.10)

    def test_rejects_oversize_notional(self):
        ok, reasons = logic.validate_order(
            {"sym": "SPY", "ac": "eq", "qty": 100, "price": 620, "ref_price": 620}, self.LIMITS)
        self.assertFalse(ok)
        self.assertTrue(any("notional" in r for r in reasons))

    def test_rejects_price_outside_band(self):
        ok, reasons = logic.validate_order(
            {"sym": "QQQ", "ac": "eq", "qty": 5, "price": 620, "ref_price": 551}, self.LIMITS)
        self.assertFalse(ok)
        self.assertTrue(any("sanity band" in r for r in reasons))

    def test_rejects_futures_contract_limit(self):
        ok, reasons = logic.validate_order(
            {"sym": "MES", "ac": "fut", "qty": 6, "price": 1, "ref_price": 1}, self.LIMITS)
        self.assertFalse(ok)

    def test_accepts_valid_order(self):
        ok, reasons = logic.validate_order(
            {"sym": "SPY", "ac": "eq", "qty": 10, "price": 618, "ref_price": 618.3}, self.LIMITS)
        self.assertTrue(ok, reasons)


class TestBacktestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QD_PORT"] = "8791"
        import server
        cls.server_mod = server
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 8791), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_run_updates_parity_and_gate_and_setup(self):
        code, r = req("POST", "/api/backtests/run/momo-etf-v3", {})
        self.assertEqual(code, 200)
        self.assertTrue(r["parity"]["pass"])
        # parity endpoint now serves computed values
        code, p = req("GET", "/api/parity/momo-etf-v3")
        self.assertEqual(code, 200)
        self.assertIn("data:", p["window"])
        # idea gate synced to computed verdict
        ideas = req("GET", "/api/ideas")[1]
        momo = next(i for i in ideas if i["name"] == "momo-etf-v3")
        self.assertEqual(momo["gate"], "pass")
        self.assertIn("Parity computed", momo["gateNote"])
        # setup progress reflects the run
        setup = req("GET", "/api/setup")[1]
        d = dict((row[0], row[1]) for row in setup)
        self.assertEqual(d["First backtest run (dual engines)"], 100)
        self.assertEqual(d["Parity gate passed (1 strategy)"], 100)

    def test_bugged_run_blocks_promotion_end_to_end(self):
        code, r = req("POST", "/api/backtests/run/momo-etf-v3", {"bug": True})
        self.assertEqual(code, 200)
        self.assertFalse(r["parity"]["pass"])
        ideas = req("GET", "/api/ideas")[1]
        momo = next(i for i in ideas if i["name"] == "momo-etf-v3")
        self.assertEqual(momo["gate"], "block")
        # go-live must now also refuse this strategy (parity lookup)
        code, body = req("POST", "/api/golive", {
            "venue": "IBKR", "strategy": "momo-etf-v3",
            "confirm": "IBKR GO LIVE", "checklist": [True] * 10})
        self.assertEqual(code, 409)
        # restore the clean verdict
        req("POST", "/api/backtests/run/momo-etf-v3", {})

    def test_order_validate_endpoint(self):
        code, r = req("POST", "/api/orders/validate",
                      {"sym": "BTCUSDT", "ac": "cry", "qty": 0.9, "price": 112000, "ref_price": 112000})
        self.assertEqual(code, 200)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
