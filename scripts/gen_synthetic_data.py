#!/usr/bin/env python3
"""Generate deterministic SYNTHETIC daily bars (2023-01-02 .. 2026-07-03) for
SPY + 9 sector ETFs into data/catalog/. One-factor model: each ETF = market
beta * SPY factor + idiosyncratic noise, with a bear regime in 2024-H1 so the
regime filter has something real to do. Same CSV schema as scripts/fetch_data.py
(Date,Open,High,Low,Close,Volume) — swap in real data any time.
"""
import datetime
import math
import os

DEST = os.path.join(os.path.dirname(__file__), "..", "data", "catalog")
ETFS = {  # beta, idio vol
    "XLK": (1.25, 0.008), "XLF": (1.05, 0.007), "XLE": (0.90, 0.012),
    "XLV": (0.70, 0.006), "XLI": (1.00, 0.007), "XLP": (0.55, 0.005),
    "XLY": (1.15, 0.008), "XLU": (0.50, 0.007), "XLB": (0.95, 0.008),
}
START = {"SPY": 380.0, "XLK": 125.0, "XLF": 34.0, "XLE": 87.0, "XLV": 132.0,
         "XLI": 98.0, "XLP": 74.0, "XLY": 128.0, "XLU": 70.0, "XLB": 77.0}


def rng(seed):
    s = seed * 7919 + 1

    def nxt():
        nonlocal s
        s = (s * 1103515245 + 12345) % 2147483648
        return s / 2147483648
    return nxt


def gauss(r):
    # Box-Muller from two uniforms
    import math as m
    u1, u2 = max(r(), 1e-9), r()
    return m.sqrt(-2 * m.log(u1)) * m.cos(2 * m.pi * u2)


def trading_days():
    d = datetime.date(2023, 1, 2)
    end = datetime.date(2026, 7, 3)
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += datetime.timedelta(days=1)


def main():
    os.makedirs(DEST, exist_ok=True)
    days = list(trading_days())
    rmkt = rng(42)
    # market factor with a 2024-H1 bear regime
    factor = []
    for d in days:
        bear = datetime.date(2024, 1, 15) <= d <= datetime.date(2024, 6, 20)
        drift = -0.0012 if bear else 0.0006
        vol = 0.014 if bear else 0.0085
        factor.append(drift + gauss(rmkt) * vol)

    series = {"SPY": (1.0, 0.0)}
    series.update(ETFS)
    for sym, (beta, idio) in series.items():
        r = rng(sum(ord(c) for c in sym) * 977)
        px = START[sym]
        rows = ["Date,Open,High,Low,Close,Volume"]
        for i, d in enumerate(days):
            ret = beta * factor[i] + (gauss(r) * idio if idio else 0)
            o = px
            px = max(px * (1 + ret), 1.0)
            hi = max(o, px) * (1 + abs(gauss(r)) * 0.003)
            lo = min(o, px) * (1 - abs(gauss(r)) * 0.003)
            vol_sh = int(4e7 * (1 + abs(gauss(r)) * 0.5))
            rows.append(f"{d.isoformat()},{o:.2f},{hi:.2f},{lo:.2f},{px:.2f},{vol_sh}")
        with open(os.path.join(DEST, f"{sym}.csv"), "w") as f:
            f.write("\n".join(rows) + "\n")
        print(f"{sym}: {len(days)} bars [SYNTHETIC]")
    open(os.path.join(DEST, ".provenance"), "w").write("synthetic:v1 (run scripts/fetch_data.py for real bars)")


if __name__ == "__main__":
    main()
