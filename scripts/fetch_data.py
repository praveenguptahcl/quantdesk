#!/usr/bin/env python3
"""Fetch REAL daily bars from Stooq (free, no key) into data/catalog/.
Run on your machine:  python3 scripts/fetch_data.py
Overwrites the synthetic CSVs with real history; the backtester picks them up
automatically (same schema). Stdlib only.
"""
import os
import urllib.request

SYMBOLS = ["SPY", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]
DEST = os.path.join(os.path.dirname(__file__), "..", "data", "catalog")


def main():
    os.makedirs(DEST, exist_ok=True)
    for sym in SYMBOLS:
        url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d&d1=20220101&d2=20261231"
        try:
            csv = urllib.request.urlopen(url, timeout=30).read().decode()
        except Exception as e:
            print(f"{sym}: FAILED ({e}) — keeping existing file")
            continue
        if not csv.startswith("Date"):
            print(f"{sym}: unexpected response — keeping existing file")
            continue
        path = os.path.join(DEST, f"{sym}.csv")
        with open(path, "w") as f:
            f.write(csv)
        print(f"{sym}: {len(csv.splitlines()) - 1} bars -> {path}  [REAL DATA]")
    # marker file so the GUI/API can report data provenance
    open(os.path.join(DEST, ".provenance"), "w").write("real:stooq")
    print("done — restart the backend and re-run backtests from the GUI")


if __name__ == "__main__":
    main()
