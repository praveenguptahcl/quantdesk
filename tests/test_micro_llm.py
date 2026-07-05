"""Tests for the M11 microstructure service and M12 LLM plumbing."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))
import llm  # noqa: E402
import micro  # noqa: E402


def _fixture_dir():
    d = tempfile.mkdtemp()
    rows = []
    bid, ask = 62000.0, 62000.5
    for i in range(10):
        rows.append({"ts": 1000.0 + i,
                     "bids": [[str(bid + j * -0.5), str(1.0 + i * 0.1), 1] for j in range(20)],
                     "asks": [[str(ask + j * 0.5), str(2.0 - i * 0.05), 1] for j in range(20)],
                     "trades": [{"id": i * 2, "px": str(bid), "sz": "0.01",
                                 "side": "buy", "t": "2026-07-05T10:00:00.000Z"}]})
    with open(os.path.join(d, "BTC-USD-test.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


class TestMicro(unittest.TestCase):
    def setUp(self):
        self._orig = micro.L2_DIR
        micro.L2_DIR = _fixture_dir()

    def tearDown(self):
        micro.L2_DIR = self._orig

    def test_snapshot_shape_and_math(self):
        d = micro.snapshot("BTCUSDT")
        self.assertEqual(d["source"], "real:coinbase")
        self.assertEqual(d["snapshots"], 10)
        self.assertEqual(len(d["bids"]), 10)
        self.assertAlmostEqual(d["mid"], 62000.25)
        self.assertEqual(len(d["ofi"]), 9)          # n-1 deltas
        self.assertTrue(all(o > 0 for o in d["ofi"]))  # bids growing, asks shrinking => buy pressure
        self.assertEqual(len(d["tape"]), 10)

    def test_unknown_symbol_none(self):
        self.assertIsNone(micro.snapshot("MES"))


class TestLLM(unittest.TestCase):
    def test_unconfigured_provider_raises(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(RuntimeError):
            llm.parse("anthropic", "buy SPY momentum")

    def test_extract_json_validates_schema(self):
        good = json.dumps({"schema": "quantdesk.strategy.v1", "name": "x", "class": "momentum",
                           "universe": ["SPY"], "entry": {}, "exit": {}, "kill_criterion": "y"})
        spec = llm._extract_json(f"Here you go:\n{good}\nDone.")
        self.assertEqual(spec["universe"], ["SPY"])
        with self.assertRaises(ValueError):
            llm._extract_json('{"schema":"quantdesk.strategy.v1","name":"x"}')  # missing keys
        with self.assertRaises(ValueError):
            llm._extract_json("no json here")


if __name__ == "__main__":
    unittest.main()
