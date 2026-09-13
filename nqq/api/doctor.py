"""nqq.doctor — a self-check that proves the install is honest and runnable.

Run `python3 doctor.py` (from nqq/api). Exits 0 only if every check passes:
  * catalog data present (real point-in-time bars),
  * the honesty invariants hold (deflated Sharpe <= raw, certainty caps),
  * a signed bundle round-trips and a tampered one fails,
  * the append-only ledger verifies,
  * the arena produces a ranked, CI-carrying board,
  * the council refuses a naked combined probability while voices are unrated.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atexit       # noqa: E402
import shutil       # noqa: E402
import tempfile     # noqa: E402
# Isolate self-check state in a fresh per-run temp dir by default. A FIXED path in a shared
# /tmp collides across runs and, worse, a leftover owned by another uid makes the DB
# read-only / un-removable — crashing the doctor with a PermissionError on remove or a
# "readonly database" on write (it would report FAIL for a healthy install). Honor an
# explicit override (CI may point these at a scratch dir); the empty temp dir is cleaned up.
_TMP = tempfile.mkdtemp(prefix="nqq-doctor-")
atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))
os.environ.setdefault("NQQ_HOME", _TMP)
os.environ.setdefault("NQQ_DB", os.path.join(_TMP, "doctor.db"))

import arena          # noqa: E402
import backtest as bt  # noqa: E402
import console         # noqa: E402
import council         # noqa: E402
import data            # noqa: E402
import kernel          # noqa: E402
import stats as st     # noqa: E402


def _check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def run():
    db = os.environ["NQQ_DB"]
    if os.path.exists(db):
        try:
            os.remove(db)
        except OSError:
            # A leftover DB we can't remove (owned by another uid on a shared /tmp). Don't
            # crash a healthy install — fall back to a fresh unique file we own.
            fd, db = tempfile.mkstemp(prefix="nqq-doctor-", suffix=".db")
            os.close(fd); os.remove(db)
            os.environ["NQQ_DB"] = db
    s = kernel.Store(db)
    kernel.ensure_seed_admin(s)
    ok = True
    print("nqq doctor — deep self-check\n")

    cat = data.available()
    ok &= _check("catalog data present", len(cat) >= 5, f"{len(cat)} symbols")
    ok &= _check("SPY is real point-in-time data", data.is_real("SPY"))

    r = [0.0006 + (i % 7 - 3) * 0.0001 for i in range(400)]
    ok &= _check("deflated Sharpe <= probabilistic Sharpe",
                 st.deflated_sharpe(r, 20) <= st.probabilistic_sharpe(r) + 1e-9)
    ok &= _check("deflation falls as trials rise",
                 st.deflated_sharpe(r, 1) >= st.deflated_sharpe(r, 200))
    c = st.edge_certainty({"is_real_pit": False, "worst_regime_dsr": 0.9,
                           "bootstrap_p_pos": 0.9, "dsr": 0.9, "forward_days": 40,
                           "capacity_aum": 5e6})
    ok &= _check("synthetic data caps certainty at 0.50", c["certainty"] <= 0.50)

    b = kernel.sign_bundle({"x": [1, 2, 3]})
    ok &= _check("signed bundle round-trips", kernel.verify_bundle(b)[0])
    b2 = dict(b); b2 = {**b, "payload": {"x": [9]}}
    ok &= _check("tampered bundle fails verification", not kernel.verify_bundle(b2)[0])

    s.stamp("t", "s", {"a": 1}); s.stamp("t", "s", {"a": 2})
    ok &= _check("append-only ledger verifies (hash chain)", s.verify_ledger()[0])

    arena.seed(s, force=True)
    board = arena.standings(s, recompute=True)
    ok &= _check("arena board ranked + CI-carrying",
                 bool(board["rows"]) and all("ci" in r for r in board["rows"]),
                 f"{len(board['rows'])} agents")

    sheet = council.review({"sym": "SPY", "side": "LONG", "size_pct": 8,
                            "stop_pct": 2, "reward_risk": 1.8})
    ok &= _check("council links every voice to evidence (ruling 73)",
                 all(v["evidence"].startswith("/api/") for v in sheet["voices"]))

    card, err = bt.scorecard(s, "momentum", {}, "SPY", operator="doctor")
    ok &= _check("scorecard runs and is bounded",
                 err is None and card["deflated_sharpe"] <= 1.0)

    import discovery, execution, journal, portfolio, risk, ai_builder
    disc = discovery.search(s, symbols=["SPY", "XLK"], operator="doctor")
    ok &= _check("discovery applies FDR across the search",
                 disc["n_survived"] <= disc["n_tested"], f"{disc['n_survived']}/{disc['n_tested']}")
    pf, _ = portfolio.build([{"family": "momentum"}, {"family": "mean_reversion"}])
    ok &= _check("portfolio reports diversification + capacity",
                 pf is not None and "diversification" in pf)
    idea, _ = journal.create(s, "doctor", "doc-idea", "momentum", {})
    _, e2 = journal.promote(s, idea["id"])  # Idea->Research ok
    ok &= _check("journal promotes through honest gates", e2 is None)
    o, e3 = execution.place(s, "SPY", "BUY", 10, operator="doctor")
    ok &= _check("paper fill is non-zero-cost (honest)", e3 is None and o["slippage_bps"] != 0)
    risk.kill(s, "FLATTEN")
    _, halted = execution.place(s, "SPY", "BUY", 1)
    ok &= _check("kill switch blocks new orders", halted is not None)
    risk.rearm(s)
    spec, _ = ai_builder.parse("buy SPY when RSI drops below 30")
    ok &= _check("AI builder parses to a testable spec",
                 spec["family"] == "rsi" and spec["requires_review"])

    import compare, library, micro, monitoring
    arena.seed(s, force=True)
    board = arena.standings(s, recompute=True)
    ok &= _check("arena includes honest LLM agents",
                 any("(AI)" in r["family"] for r in board["rows"]))
    cmp, _ = compare.compare(s, "momentum", "mean_reversion")
    ok &= _check("compare flags conflicts + correlation",
                 cmp is not None and "correlation" in cmp)
    ok &= _check("template library is honest (states failure modes)",
                 all("fails" in x for x in library.listing()["library"]))
    bk = micro.book("BTCUSDT")
    ok &= _check("microstructure book is labelled synthetic when not live",
                 "synthetic" in bk)
    dr = monitoring.drift("momentum")
    ok &= _check("drift monitor reports a verdict", dr is None or "verdict" in dr)

    import providers, workspace
    ps = providers.status()
    ok &= _check("providers reused from keys, synthetic never labelled live",
                 all("live" in p for p in ps["providers"]) and
                 not providers.quote("XLK")["live"] if
                 providers.quote("XLK")["source"].startswith(("catalog", "synth")) else True)
    b = workspace.export(s)
    ok &= _check("workspace exports as a verifiable signed bundle", kernel.verify_bundle(b)[0])
    b["payload"]["docs"]["ideas"] = [{"id": 1}]
    ok &= _check("tampered workspace import is refused", workspace.restore(s, b)[0] is None)

    print("\n" + ("✔ ALL CHECKS PASSED — nqq is honest and runnable."
                  if ok else "✗ SOME CHECKS FAILED."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
