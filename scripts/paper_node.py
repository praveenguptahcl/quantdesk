#!/usr/bin/env python3
"""Paper trading node (M6): computes momo-etf-v3's CURRENT target portfolio from
the latest catalog bars and rebalances the Alpaca PAPER account to match.

Safety model:
  - DRY-RUN by default: prints intended orders, submits nothing.
  - `--execute` submits to Alpaca paper, but only if: keys configured, endpoint
    is paper, kill switch not engaged, and every order passes the risk layer
    (risk/limits.yaml) — same pre-route validation as everything else.
  - Automatic scheduling is opt-in via QD_PAPER_AUTOTRADE=1 in .env (server
    triggers Mondays after open). Default is manual.

Usage:
  python3 scripts/paper_node.py            # dry-run: show intended orders
  python3 scripts/paper_node.py --execute  # submit to Alpaca paper
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "api")))
import alpaca  # noqa: E402
import backtest  # noqa: E402
import logic  # noqa: E402


def compute_targets():
    """Current target weights from the identical decision chain, on the last bar."""
    spy = backtest.load_bars("SPY")
    data = {s: backtest.load_bars(s) for s in backtest.ETFS}
    if spy is None or any(v is None for v in data.values()):
        raise SystemExit("catalog data missing — run scripts/fetch_data.py")
    n = min(len(spy), *(len(v) for v in data.values()))
    spy_c = [spy[i][1] for i in range(n)]
    closes = {s: [data[s][i][1] for i in range(n)] for s in backtest.ETFS}
    i = n - 1
    sma = sum(spy_c[i - backtest.SMA_N + 1: i + 1]) / backtest.SMA_N
    risk_on = spy_c[i] > sma
    if not risk_on:
        return {s: 0.0 for s in backtest.ETFS}, False, spy[i][0]
    moms = {s: (closes[s][i] / closes[s][i - backtest.MOM] - 1)
            - (closes[s][i] / closes[s][i - backtest.SKIP] - 1) for s in backtest.ETFS}
    top = set(sorted(backtest.ETFS, key=lambda s: moms[s], reverse=True)[:backtest.TOP_N])
    targets = {}
    for s in backtest.ETFS:
        conf = backtest._sigmoid_conf(moms[s], risk_on)
        if s in top and conf >= backtest.ENTRY_CONF:
            c = closes[s]
            rets = [c[j] / c[j - 1] - 1 for j in range(i - backtest.VOLW + 1, i + 1)]
            mu = sum(rets) / backtest.VOLW
            vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / backtest.VOLW) * math.sqrt(252), 0.02)
            targets[s] = conf * min(backtest.VOL_TGT / vol, backtest.VOL_CAP) / backtest.TOP_N
        else:
            targets[s] = 0.0
    gross = sum(targets.values())
    if gross > 1.0:
        targets = {s: w / gross for s, w in targets.items()}
    return targets, True, spy[i][0]


def main():
    execute = "--execute" in sys.argv
    # load .env like the server does
    env_path = os.path.normpath(os.path.join(HERE, "..", ".env"))
    if os.path.exists(env_path):
        for line in open(env_path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    targets, risk_on, asof = compute_targets()
    print(f"signal date: {asof} | regime: {'RISK-ON' if risk_on else 'RISK-OFF (flat)'}")

    if not alpaca.configured():
        raise SystemExit("Alpaca paper keys not configured — dry math only")
    acct = alpaca.account()
    # Trade at INTENDED capital, not the paper account's inflated $1M:
    # QD_CAPITAL caps deployable equity (default 100k, matching risk/limits.yaml).
    equity = min(acct["equity"], float(os.environ.get("QD_CAPITAL", "100000")))
    held = {p["sym"]: float(p["qty"]) for p in alpaca.positions()}
    limits_path = os.path.join(HERE, "..", "risk", "limits.yaml")
    limits = logic.parse_simple_yaml(open(limits_path).read()) if os.path.exists(limits_path) else {}

    plan = []
    for s, w in targets.items():
        px = alpaca.last_price(s)
        if not px:
            print(f"{s}: no price — skipped")
            continue
        # clamp to risk limits: per-symbol cap and single-order notional cap
        eq_cap = limits.get("equities", {}).get("max_position_usd_per_symbol", 15000)
        ord_cap = limits.get("global", {}).get("max_single_order_notional_usd", 10000)
        tgt_val = min(equity * w, eq_cap)
        tgt_qty = int(tgt_val / px)
        delta = tgt_qty - int(held.get(s, 0))
        max_delta = int(ord_cap / px)
        if abs(delta) > max_delta:   # partial rebalance; converges over successive runs
            delta = max_delta if delta > 0 else -max_delta
        if delta == 0:
            continue
        order = {"sym": s, "ac": "eq", "side": "BUY" if delta > 0 else "SELL",
                 "qty": abs(delta), "price": px, "ref_price": px}
        ok, reasons = logic.validate_order(order, limits)
        plan.append((s, delta, px, ok, reasons))

    as_json = "--json" in sys.argv
    if not plan:
        if as_json:
            print(json.dumps({"asof": asof, "risk_on": risk_on, "equity": equity, "orders": [],
                              "executed": False, "note": "portfolio already at target"}))
        else:
            print("portfolio already at target — no orders")
        return
    if as_json:
        out = {"asof": asof, "risk_on": risk_on, "equity": equity, "executed": execute, "orders": []}
        for s, delta, px, ok, reasons in plan:
            o = {"sym": s, "side": "BUY" if delta > 0 else "SELL", "qty": abs(delta),
                 "px": px, "risk_ok": ok, "risk_reasons": reasons}
            if execute and ok:
                r = alpaca.submit_order(s, abs(delta), "buy" if delta > 0 else "sell",
                                        order_type="limit",
                                        limit_price=round(px * (1.002 if delta > 0 else 0.998), 2))
                o["order_id"] = r["id"][:8]
                o["status"] = r["status"]
            out["orders"].append(o)
        print(json.dumps(out))
        return
    print(f"{'EXECUTING' if execute else 'DRY-RUN'} — account equity ${equity:,.0f}:")
    for s, delta, px, ok, reasons in plan:
        line = f"  {'BUY' if delta > 0 else 'SELL':4} {abs(delta):5d} {s} @ ~${px:,.2f}  " \
               f"[risk: {'OK' if ok else 'REJECTED: ' + '; '.join(reasons)}]"
        print(line)
        if execute and ok:
            r = alpaca.submit_order(s, abs(delta), "buy" if delta > 0 else "sell",
                                    order_type="limit", limit_price=round(px * (1.002 if delta > 0 else 0.998), 2))
            print(f"        -> submitted {r['id'][:8]} ({r['status']})")
    if not execute:
        print("no orders submitted (dry-run). Re-run with --execute to rebalance the paper account.")


if __name__ == "__main__":
    main()
