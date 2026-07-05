#!/usr/bin/env python3
"""Fetch REAL daily bars into data/catalog/ (stdlib only).
Tries Stooq first, falls back to Yahoo Finance's chart API per symbol.
Writes the `.provenance` marker ONLY if real data actually landed.
Run:  python3 scripts/fetch_data.py
"""
import datetime
import json
import os
import urllib.request

SYMBOLS = ["SPY", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]
DEST = os.path.join(os.path.dirname(__file__), "..", "data", "catalog")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


def get(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def from_stooq(sym):
    csv = get(f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d").decode()
    if not csv.startswith("Date"):
        raise ValueError("unexpected response")
    # keep 2022+ rows only
    lines = [l for l in csv.splitlines() if l.startswith("Date") or l >= "2022"]
    if len(lines) < 200:
        raise ValueError(f"too few rows ({len(lines)})")
    return "\n".join(lines) + "\n"


def from_yahoo(sym):
    raw = json.loads(get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        "?range=5y&interval=1d&events=history"))
    res = raw["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    rows = ["Date,Open,High,Low,Close,Volume"]
    for i, t in enumerate(ts):
        if None in (q["open"][i], q["close"][i]):
            continue
        d = datetime.datetime.utcfromtimestamp(t).date().isoformat()
        if d < "2022-01-01":
            continue
        rows.append(f"{d},{q['open'][i]:.2f},{q['high'][i]:.2f},"
                    f"{q['low'][i]:.2f},{q['close'][i]:.2f},{q['volume'][i] or 0}")
    if len(rows) < 200:
        raise ValueError(f"too few rows ({len(rows)})")
    return "\n".join(rows) + "\n"


def main():
    os.makedirs(DEST, exist_ok=True)
    ok, sources = 0, set()
    for sym in SYMBOLS:
        csv = None
        for name, fn in (("stooq", from_stooq), ("yahoo", from_yahoo)):
            try:
                csv = fn(sym)
                sources.add(name)
                print(f"{sym}: {len(csv.splitlines()) - 1} bars via {name}  [REAL DATA]")
                break
            except Exception as e:
                print(f"{sym}: {name} failed ({e})")
        if csv:
            with open(os.path.join(DEST, f"{sym}.csv"), "w") as f:
                f.write(csv)
            ok += 1
    if ok == len(SYMBOLS):
        open(os.path.join(DEST, ".provenance"), "w").write("real:" + "+".join(sorted(sources)))
        print(f"provenance: REAL ({'+'.join(sorted(sources))}) — all {ok} symbols")
    elif ok > 0:
        open(os.path.join(DEST, ".provenance"), "w").write(f"mixed:{ok}/{len(SYMBOLS)} real")
        print(f"provenance: MIXED — only {ok}/{len(SYMBOLS)} real; backtest window may be inconsistent")
    else:
        print("provenance: UNCHANGED — no real data fetched, existing files kept")


if __name__ == "__main__":
    main()
