#!/usr/bin/env python3
"""Record REAL order-book snapshots + trades from Coinbase Exchange public API
(no key needed) into data/catalog/l2/. Blueprint M11's capture leg.

Usage:  python3 scripts/record_l2.py [PRODUCT] [SECONDS]   (default BTC-USD 60)
Output: data/catalog/l2/<PRODUCT>-<YYYYMMDD-HHMMSS>.jsonl
        one JSON line per second: {ts, bids:[[px,sz]..20], asks:[[px,sz]..20], trades:[..]}
"""
import json
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "quantdesk-recorder/1.0"}
BASE = "https://api.exchange.coinbase.com"


def get(path):
    req = urllib.request.Request(BASE + path, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def main():
    product = sys.argv[1] if len(sys.argv) > 1 else "BTC-USD"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    dest_dir = os.path.join(os.path.dirname(__file__), "..", "data", "catalog", "l2")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{product}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    n_ok = 0
    with open(path, "w") as f:
        t_end = time.time() + seconds
        last_trade = None
        while time.time() < t_end:
            t0 = time.time()
            try:
                book = get(f"/products/{product}/book?level=2")
                trades = get(f"/products/{product}/trades?limit=20")
                new_trades = []
                for tr in trades:
                    if last_trade is not None and tr["trade_id"] <= last_trade:
                        break
                    new_trades.append({"id": tr["trade_id"], "px": tr["price"],
                                       "sz": tr["size"], "side": tr["side"],
                                       "t": tr["time"]})
                if trades:
                    last_trade = max(last_trade or 0, trades[0]["trade_id"])
                row = {"ts": round(time.time(), 3),
                       "bids": book["bids"][:20], "asks": book["asks"][:20],
                       "trades": new_trades}
                f.write(json.dumps(row) + "\n")
                f.flush()
                n_ok += 1
            except Exception as e:
                print(f"snapshot failed: {e}", flush=True)
            time.sleep(max(0.0, 1.0 - (time.time() - t0)))
    print(f"recorded {n_ok} snapshots -> {path}")


if __name__ == "__main__":
    main()
