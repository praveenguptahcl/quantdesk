"""nqq.micro — order-book depth + microstructure metrics (honestly labelled).

Without a real L2 feed these are SYNTHETIC — clearly flagged, never passed off as live.
The point is to teach the shape: an order book, the imbalance signal, spread and depth,
and the microstructure metrics an HFT strategy would trade. A real recorder can replace
the synthetic generator later; the honesty label makes the difference visible.
"""
import random

import marketdata as mdmod


def book(sym, levels=8):
    q = mdmod.quote(sym)
    mid = q["price"] or 100.0
    rng = random.Random((hash(sym) & 0xFFFF) ^ int(mid))
    tick = max(0.01, round(mid * 0.0002, 2))
    bids, asks = [], []
    for i in range(levels):
        bids.append({"px": round(mid - (i + 1) * tick, 2),
                     "sz": round(rng.uniform(50, 500) * (levels - i))})
        asks.append({"px": round(mid + (i + 1) * tick, 2),
                     "sz": round(rng.uniform(50, 500) * (levels - i))})
    bid_sz = sum(b["sz"] for b in bids)
    ask_sz = sum(a["sz"] for a in asks)
    imb = round((bid_sz - ask_sz) / (bid_sz + ask_sz), 3)
    spread_bps = round((asks[0]["px"] - bids[0]["px"]) / mid * 1e4, 1)
    mid_live = "live" in q["source"]
    return {
        "sym": sym, "mid": round(mid, 2), "source": q["source"],
        # The DEPTH is always synthetic (no L2 feed); only the mid can be live. Never let a
        # live mid make the fabricated ladder look real.
        "synthetic": True, "mid_live": mid_live,
        "depth_source": ("live·mid / synthetic·depth" if mid_live else "synthetic"),
        "bids": bids, "asks": asks,
        "metrics": {
            "spread_bps": spread_bps, "imbalance": imb,
            "imbalance_signal": "BUY pressure" if imb > 0.1 else
                                ("SELL pressure" if imb < -0.1 else "balanced"),
            "top_depth_usd": round((bids[0]["sz"] * bids[0]["px"] +
                                    asks[0]["sz"] * asks[0]["px"])),
            "microprice": round((bids[0]["px"] * ask_sz + asks[0]["px"] * bid_sz) /
                                (bid_sz + ask_sz), 3),
        },
        "note": ("Synthetic book — teaches the shape; certainty of any edge derived from "
                 "it is capped like any non-real data. Wire an L2 recorder for live depth."),
    }
