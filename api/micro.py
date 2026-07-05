"""Microstructure service (M11): serves REAL recorded order-book data from
data/catalog/l2/*.jsonl (captured by scripts/record_l2.py) in the shape the
GUI's Microstructure screen renders. Stdlib only."""
import json
import os
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
L2_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "catalog", "l2"))

# GUI symbols -> Coinbase products
PRODUCT_MAP = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD", "BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD"}


def latest_recording(sym):
    product = PRODUCT_MAP.get(sym.upper())
    if not product or not os.path.isdir(L2_DIR):
        return None
    files = sorted(glob.glob(os.path.join(L2_DIR, f"{product}-*.jsonl")))
    if not files:
        return None
    rows = []
    with open(files[-1]) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return (files[-1], rows) if rows else None


def snapshot(sym):
    """Full microstructure payload from the latest recording, or None."""
    rec = latest_recording(sym)
    if not rec:
        return None
    path, rows = rec
    last = rows[-1]
    bids = [[float(p), float(s)] for p, s, *_ in last["bids"]][:10]
    asks = [[float(p), float(s)] for p, s, *_ in last["asks"]][:10]
    mid = (bids[0][0] + asks[0][0]) / 2
    spread = asks[0][0] - bids[0][0]

    # OFI + microprice series across snapshots
    ofi, mp_bps, spreads_bps = [], [], []
    prev = None
    for r in rows:
        try:
            b0p, b0s = float(r["bids"][0][0]), float(r["bids"][0][1])
            a0p, a0s = float(r["asks"][0][0]), float(r["asks"][0][1])
        except (IndexError, ValueError):
            continue
        m = (b0p + a0p) / 2
        micro = (a0p * b0s + b0p * a0s) / (b0s + a0s) if (b0s + a0s) else m
        mp_bps.append((micro - m) / m * 1e4)
        spreads_bps.append((a0p - b0p) / m * 1e4)
        if prev:
            d_bid = b0s - prev[1] if b0p >= prev[0] else -prev[1]
            d_ask = a0s - prev[3] if a0p <= prev[2] else -prev[3]
            ofi.append(d_bid - d_ask)
        prev = (b0p, b0s, a0p, a0s)

    tape = []
    for r in reversed(rows):
        for t in r.get("trades", []):
            tape.append({"px": t["px"], "sz": t["sz"], "side": t["side"], "t": t["t"][11:23]})
            if len(tape) >= 26:
                break
        if len(tape) >= 26:
            break

    n_trades = sum(len(r.get("trades", [])) for r in rows)
    duration = max(rows[-1]["ts"] - rows[0]["ts"], 1)
    return {
        "source": "real:coinbase", "file": os.path.basename(path),
        "snapshots": len(rows), "duration_s": round(duration, 1),
        "bids": bids, "asks": asks, "mid": mid,
        "spread": spread, "spread_bps": round(spread / mid * 1e4, 3),
        "avg_spread_bps": round(sum(spreads_bps) / max(len(spreads_bps), 1), 3),
        "top_depth": round(bids[0][1] + asks[0][1], 4),
        "trades_per_min": round(n_trades / duration * 60, 1),
        "ofi": [round(x, 4) for x in ofi][-140:],
        "microprice_bps": [round(x, 3) for x in mp_bps][-140:],
        "tape": tape,
    }
