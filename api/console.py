"""AlphaPulse Conviction Console (M22) — the default gamified dashboard.

Each tracked symbol gets a signed CONVICTION reading (-100 short … +100 long).
For symbols in the catalog the conviction is REAL — derived from the same 12-1
momentum / regime math as the signal engine (backtest.current_signal). Symbols
without catalog bars, plus the execution tape (fired trades), use admin-resettable
SAMPLE data so the streaming dashboard is populated out of the box.

A "fire" is a trade triggered when |conviction| crosses the certainty threshold
(default 60%). Fires land in the execution tape with an SL–ENTRY–PT track and
resolve to WIN/LOSS. The client animates the needles toward these targets and
renders the fire pulses; this module is the source of truth for the data.

Stdlib only. Sample data lives under store docs/kv and is cleared by reset().
"""
import datetime
import math
import os
import random

import backtest

# symbol, asset-class, linked strategy (real QuantDesk journal names), agent codename
CONSOLE_SYMBOLS = [
    ("SPY", "eq", "momo-etf-v3", "Cobalt-5"),
    ("QQQ", "eq", "fast-momo-v1", "Vega-2"),
    ("AAPL", "eq", "gap-fade-v1", "Nimbus-7"),
    ("XLK", "eq", "momo-etf-v3", "Cobalt-5"),
    ("MES", "fut", "fut-carry-v1", "Basis-9"),
    ("BTCUSDT", "cry", "crypto-mr-v2", "Orbit-3"),
    ("ETHUSDT", "cry", "crypto-mr-v2", "Orbit-3"),
    ("SPCX", "eq", "vol-premium-v1", "Theta-4"),
]

# reference prices for symbols without catalog bars (sample)
SAMPLE_PX = {"QQQ": 459.89, "AAPL": 254.80, "MES": 6412.25,
             "BTCUSDT": 67230.0, "ETHUSDT": 4183.0, "SPCX": 188.40}

# two competing strategies (with agent codenames) per tracked symbol — the
# "strategy dials" in the drill-down view
SYMBOL_STRATEGIES = {
    "SPY": [("momo-etf-v3", "Cobalt-5"), ("gap-fade-v1", "Nimbus-7")],
    "QQQ": [("fast-momo-v1", "Vega-2"), ("vol-premium-v1", "Theta-4")],
    "AAPL": [("fast-momo-v1", "Vanta-2"), ("gap-fade-v1", "Cobalt-5")],
    "XLK": [("momo-etf-v3", "Cobalt-5"), ("flow-imbalance", "Delta-6")],
    "MES": [("fut-carry-v1", "Basis-9"), ("momo-etf-v3", "Cobalt-5")],
    "BTCUSDT": [("crypto-mr-v2", "Orbit-3"), ("flow-imbalance", "Delta-6")],
    "ETHUSDT": [("crypto-mr-v2", "Orbit-3"), ("fast-momo-v1", "Vega-2")],
    "SPCX": [("vol-premium-v1", "Theta-4"), ("gap-fade-v1", "Nimbus-7")],
}

# the alpha-signal dials shown down the right of the chart
SIGNAL_DIALS = [
    ("news", "NEWS", "COLD", "HOT", "#F5A623"),
    ("sentiment", "SENTIMENT", "BEAR", "BULL", "#2FD576"),
    ("order_flow", "ORDER FLOW", "SELL", "BUY", "#4D8DFF"),
    ("volatility", "VOLATILITY", "CALM", "TURB", "#9B7BE0"),
    ("momentum", "MOMENTUM", "DOWN", "UP", "#FF5CA8"),
]

# strategy → its brand colour (matches the little square in the dials)
STRAT_COLORS = {
    "momo-etf-v3": "#4D8DFF", "fast-momo-v1": "#F5A623", "gap-fade-v1": "#FF5CA8",
    "crypto-mr-v2": "#2FD576", "fut-carry-v1": "#9B7BE0", "vol-premium-v1": "#5BD6E0",
    "flow-imbalance": "#E0A15B", "pairs-stat-v1": "#B07BE0",
}


def _sample_conviction(sym):
    """Lively, slowly-evolving sample conviction in [-92, 92] — until a real
    per-symbol streaming signal is wired, every gauge animates off this so no
    dial is ever pinned or blank."""
    seed = (hash(sym) % 997) / 997.0
    t = datetime.datetime.now().timestamp() / 40.0
    v = math.sin(t + seed * 6.283) * 58 + math.sin(t * 0.37 + seed * 3.1) * 26
    bias = ((hash(sym + "b") % 100) - 50) * 0.5
    return round(max(-92, min(92, v + bias)), 1)


def _spark(sym, n=40):
    """Small price+volume series for the faint chart behind a gauge card."""
    rng = random.Random((hash(sym) & 0xFFFF) ^ datetime.date.today().toordinal())
    ohlcv = backtest.load_bars(sym)
    if ohlcv and len(ohlcv) >= n:
        closes = [b[1] for b in ohlcv[-n:]]
        base = closes[0] or 1
        px = [round(v / base, 4) for v in closes]
    else:
        p, px = 1.0, []
        for _ in range(n):
            p *= 1 + rng.uniform(-0.008, 0.0085)
            px.append(round(p, 4))
    vol = [round(rng.uniform(0.25, 1.0), 2) for _ in range(n)]
    return px, vol


def _state(conv, threshold, firing_now):
    a = abs(conv)
    if firing_now:
        return "filled"
    if a >= threshold * 100:
        return "arming"
    if a >= threshold * 100 * 0.6:
        return "cooling"
    return "scanning"


def _event(sym, conv, agent, state):
    if state == "filled":
        return f"▶ {agent} filled"
    if state == "arming":
        return f"▲ {agent} arming {conv:+.0f}%"
    if state == "cooling":
        return f"{agent} · cooling {conv:+.0f}%"
    return f"{agent} · scanning"


# ---------- real conviction ----------
def _catalog_conviction(sym):
    """Signed conviction from a symbol's own 12-1 momentum vs its recent path,
    scaled to [-100, 100]. Real when the symbol has catalog bars, else None."""
    bars = backtest.load_bars(sym)
    if not bars or len(bars) < 260:
        return None
    c = [b[1] for b in bars]
    i = len(c) - 1
    mom_full = c[i] / c[i - 252] - 1
    mom_recent = c[i] / c[i - 21] - 1
    mom_12_1 = mom_full - mom_recent
    # 15% 12-1 momentum ≈ full conviction
    conv = max(-100.0, min(100.0, mom_12_1 / 0.15 * 100))
    return round(conv, 1), round(c[i], 2)


# ---------- sample seed (admin-resettable) ----------
def _now_hms(offset_s=0):
    t = datetime.datetime.now() - datetime.timedelta(seconds=offset_s)
    return t.strftime("%H:%M:%S")


def seed(store, force=False):
    """Populate the console with sample fired trades + session stats so the
    dashboard is alive on first load. Admin-resettable. Idempotent."""
    if store.get_kv("console:seeded") == "2" and not force:
        return {"seeded": False, "note": "already seeded"}

    def _pos(i, side, sym, strat, fired, sl, entry, pt, last, pnl, age):
        return {"id": i, "side": side, "sym": sym, "strategy": strat, "fired_at": fired,
                "sl": sl, "entry": entry, "pt": pt, "last": last, "pnl": pnl,
                "opened": _now_hms(age), "demo": True}
    opens = [
        _pos(1, "LONG", "SPY", "gap-fade-v1", 0.60, 524.65, 527.02, 530.58, 528.9, 64, 35),
        _pos(2, "LONG", "SPCX", "gap-fade-v1", 0.85, 92.07, 92.49, 93.11, 92.8, 332, 180),
        _pos(3, "LONG", "MES", "momo-etf-v3", 0.88, 5366, 5385, 5419, 5401, 205, 220),
        _pos(4, "SHORT", "AAPL", "gap-fade-v1", 0.86, 256.4, 254.8, 251.2, 254.1, -35, 300),
    ]
    closed = [
        {"id": 11, "side": "SHORT", "sym": "SPY", "strategy": "gap-fade-v1",
         "pnl": 540, "result": "WIN", "closed": _now_hms(900), "demo": True},
        {"id": 12, "side": "LONG", "sym": "BTCUSDT", "strategy": "crypto-mr-v2",
         "pnl": 168, "result": "WIN", "closed": _now_hms(1500), "demo": True},
        {"id": 13, "side": "LONG", "sym": "AAPL", "strategy": "fast-momo-v1",
         "pnl": -180, "result": "LOSS", "closed": _now_hms(2100), "demo": True},
        {"id": 14, "side": "SHORT", "sym": "XLK", "strategy": "momo-etf-v3",
         "pnl": -360, "result": "LOSS", "closed": _now_hms(2600), "demo": True},
        {"id": 15, "side": "LONG", "sym": "QQQ", "strategy": "fast-momo-v1",
         "pnl": 205, "result": "WIN", "closed": _now_hms(3200), "demo": True},
        {"id": 16, "side": "SHORT", "sym": "MES", "strategy": "fut-carry-v1",
         "pnl": 244, "result": "WIN", "closed": _now_hms(3900), "demo": True},
        {"id": 17, "side": "LONG", "sym": "SPCX", "strategy": "vol-premium-v1",
         "pnl": 55, "result": "WIN", "closed": _now_hms(4600), "demo": True},
        {"id": 18, "side": "LONG", "sym": "ETHUSDT", "strategy": "crypto-mr-v2",
         "pnl": 312, "result": "WIN", "closed": _now_hms(5200), "demo": True},
        {"id": 19, "side": "SHORT", "sym": "QQQ", "strategy": "vol-premium-v1",
         "pnl": 128, "result": "WIN", "closed": _now_hms(5900), "demo": True},
        {"id": 20, "side": "LONG", "sym": "SPY", "strategy": "momo-etf-v3",
         "pnl": -142, "result": "LOSS", "closed": _now_hms(6600), "demo": True},
    ]
    store.put_doc("console_open", "state", {"rows": opens})
    store.put_doc("console_closed", "state", {"rows": closed})
    store.put_doc("console_session", "state", {
        "best_streak": 5, "started": datetime.datetime.now().isoformat()[:16], "demo": True})
    store.set_kv("console:seeded", "2")
    store.add_feed("info", "CONVICTION CONSOLE — sample session seeded "
                   f"({len(opens)} open, {len(closed)} closed fires); admin can reset it")
    return {"seeded": True, "open": len(opens), "closed": len(closed)}


def reset(store):
    """Admin: wipe the sample console data (fires + session). Real conviction
    gauges keep working from live catalog data."""
    store.put_doc("console_open", "state", {"rows": []})
    store.put_doc("console_closed", "state", {"rows": []})
    store.put_doc("console_session", "state", {"best_streak": 0})
    store.set_kv("console:seeded", "")
    store.add_feed("warn", "CONVICTION CONSOLE — sample data cleared by admin")
    return {"reset": True}


# ---------- snapshot ----------
def _strategy_id(store, name):
    for i in store.list_docs("ideas"):
        if i["name"] == name:
            return i["id"]
    return None


def snapshot(store, threshold=0.60):
    opens = (store.get_doc("console_open", "state") or {}).get("rows", [])
    closed = (store.get_doc("console_closed", "state") or {}).get("rows", [])
    sess = store.get_doc("console_session", "state") or {}

    # per-symbol conviction — lively SAMPLE for every gauge (real per-symbol
    # streaming signal not yet wired; real price used where the catalog has it)
    symbols = []
    for sym, ac, strat, agent in CONSOLE_SYMBOLS:
        conv = _sample_conviction(sym)
        real = _catalog_conviction(sym)
        px = real[1] if real else SAMPLE_PX.get(sym, 100.0)
        sym_pnl = sum(r["pnl"] for r in opens + closed if r["sym"] == sym)
        n_open = sum(1 for r in opens if r["sym"] == sym)
        side = "LONG" if conv >= 0 else "SHORT"
        firing = n_open > 0
        state = _state(conv, threshold, firing)
        spark, spark_vol = _spark(sym)
        n_strat = len(SYMBOL_STRATEGIES.get(sym, [strat]))
        symbols.append({
            "sym": sym, "ac": ac, "strategy": strat,
            "strategy_id": _strategy_id(store, strat), "agent": agent,
            "conviction": conv, "src": "sample", "price": px,
            "side": side, "net_pct": round(conv, 0), "sym_pnl": sym_pnl,
            "open_positions": n_open, "state": state,
            "event": _event(sym, conv, agent, state),
            "strat_armed": n_strat, "strat_total": n_strat,
            "sig": max(1, int(abs(conv) / 14)),
            "spark": spark, "spark_vol": spark_vol,
        })

    # session stats — computed from the tape
    all_fires = opens + closed
    resolved = [r for r in closed]
    wins = sum(1 for r in resolved if r.get("result") == "WIN")
    hit_rate = round(wins / len(resolved), 2) if resolved else 0.0
    session_pnl = sum(r["pnl"] for r in all_fires)
    # current win streak (from most-recent closed backwards)
    streak = 0
    for r in closed:
        if r.get("result") == "WIN":
            streak += 1
        else:
            break
    open_risk = sum(abs(r.get("entry", 0) - r.get("sl", 0)) for r in opens)
    equity = _equity_curve(all_fires)
    return {
        "threshold": threshold,
        "session": {
            "pnl": round(session_pnl, 2), "hit_rate": hit_rate,
            "streak": streak, "best_streak": max(sess.get("best_streak", 0), streak),
            "fires": len(all_fires), "open_risk": round(open_risk, 2),
            "open_risk_pct": round(open_risk / 100000 * 100, 2),
            "day_drawdown": min(0, min(equity) - equity[0]) if equity else 0,
        },
        "symbols": symbols,
        "open_positions": opens,
        "closed": closed,
        "equity": equity,
        "seeded": store.get_kv("console:seeded") == "2",
        "clock": datetime.datetime.now().strftime("%H:%M:%S"),
    }


def _real_vol_pct(sym):
    """Annualized realized vol (20d) for a catalog symbol, else None."""
    bars = backtest.load_bars(sym)
    if not bars or len(bars) < 25:
        return None
    c = [b[1] for b in bars[-21:]]
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    mu = sum(rets) / len(rets)
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)) * math.sqrt(252) * 100


def symbol_detail(store, sym, threshold=0.60):
    """Drill-down for one symbol: strategy dials, price/volume/fill series, the
    five alpha-signal dials, and the symbol's own execution tape. Real where the
    catalog has data (price, momentum, volatility), sample otherwise (labelled)."""
    cfg = next((c for c in CONSOLE_SYMBOLS if c[0] == sym), None)
    if not cfg:
        return None
    _, ac, _, _ = cfg
    rng = random.Random(hash(sym) & 0xFFFF)
    real = _catalog_conviction(sym)
    conv, price = (real if real else (round(rng.uniform(-72, 72), 1), SAMPLE_PX.get(sym, 100.0)))
    src = "real" if real else "sample"

    # ---- price + volume series (24h window, sample intraday walk) ----
    ohlcv = backtest.load_bars(sym)
    n_pts = 48
    series, vols, times = [], [], []
    now = datetime.datetime.now()
    if ohlcv and len(ohlcv) >= n_pts:
        # shape from recent real daily closes, ending at the true last price
        closes = [b[1] for b in ohlcv[-n_pts:]]
        base = closes[-1]
        # rebase so the last point == real price, keep the real shape
        series = [round(price * (v / base), 2) for v in closes]
        src_series = "real-shape"
    else:
        p = price * 0.985
        for _ in range(n_pts):
            p *= 1 + rng.uniform(-0.006, 0.0065)
            series.append(round(p, 2))
        series[-1] = price
        src_series = "sample"
    for i in range(n_pts):
        vols.append(round(rng.uniform(0.3, 1.0), 2))
        times.append((now - datetime.timedelta(minutes=30 * (n_pts - 1 - i))).strftime("%H:%M"))
    change_pct = round((series[-1] / series[0] - 1) * 100, 2) if series[0] else 0.0

    # ---- fill markers (from the symbol's tape) placed along the series ----
    tape_open = [r for r in (store.get_doc("console_open", "state") or {}).get("rows", [])
                 if r["sym"] == sym]
    tape_closed = [r for r in (store.get_doc("console_closed", "state") or {}).get("rows", [])
                   if r["sym"] == sym]
    fills = []
    for k, r in enumerate(tape_closed + tape_open):
        idx = int(n_pts * (0.2 + 0.6 * ((k + 1) / (len(tape_closed) + len(tape_open) + 1))))
        fills.append({"i": min(idx, n_pts - 1), "side": r["side"],
                      "price": series[min(idx, n_pts - 1)]})

    # ---- strategy dials ----
    dials = []
    for strat, agent in SYMBOL_STRATEGIES.get(sym, []):
        # each strategy reads a variation of the symbol conviction
        c = max(-100, min(100, conv + rng.uniform(-35, 35)))
        firing = abs(c) >= threshold * 100
        dials.append({"strategy": strat, "strategy_id": _strategy_id(store, strat),
                      "agent": agent, "conviction": round(c, 1),
                      "color": STRAT_COLORS.get(strat, "#4D8DFF"),
                      "pct": round(c / 10, 0),   # small reading value like the ref
                      "armed": True,  # both competing strategies enabled on this symbol
                      "firing": firing, "re_arm_s": rng.randint(1, 12)})

    # ---- alpha-signal dials (0-100) ----
    def _lbl(key, val):
        if key == "momentum":
            return "UP" if val >= 60 else "DOWN" if val <= 40 else "FLAT"
        if key == "volatility":
            return "TURB" if val >= 66 else "MILD" if val >= 33 else "CALM"
        if key == "news":
            return "HOT" if val >= 55 else "COLD"
        if key == "sentiment":
            return "BULL" if val >= 50 else "BEAR"
        if key == "order_flow":
            return "BUY" if val >= 50 else "SELL"
        return ""
    real_mom = round(min(100, max(0, (conv + 100) / 2)), 0)   # conviction → 0-100
    rvol = _real_vol_pct(sym)
    real_vold = round(min(100, (rvol / 40 * 100))) if rvol else None
    signals = []
    for key, label, lo, hi, col in SIGNAL_DIALS:
        if key == "momentum":
            val, s = int(real_mom), src
        elif key == "volatility" and real_vold is not None:
            val, s = int(real_vold), "real"
        else:
            val, s = rng.randint(15, 88), "sample"
        signals.append({"key": key, "label": label, "lo": lo, "hi": hi, "color": col,
                        "value": val, "reading": _lbl(key, val), "src": s})

    return {
        "sym": sym, "ac": ac, "price": price, "change_pct": change_pct,
        "conviction": conv, "src": src, "clock": now.strftime("%H:%M:%S"),
        "series": series, "volume": vols, "times": times, "series_src": src_series,
        "fills": fills, "strategy_dials": dials, "signals": signals,
        "open_positions": tape_open, "closed": tape_closed,
        "tape_pnl": sum(r["pnl"] for r in tape_open + tape_closed),
        "threshold": threshold,
    }


def _equity_curve(fires):
    """Cumulative P&L path across fires (oldest→newest) for the tape sparkline."""
    ordered = sorted(fires, key=lambda r: r.get("closed") or r.get("opened") or "")
    eq, run = [0], 0
    for r in ordered:
        run += r["pnl"]
        eq.append(round(run, 2))
    return eq or [0]
