"""nqq.console — the gamified Conviction Console, backed by REAL EdgeCertainty.

The needle is a lively sample conviction (engagement). The card BADGE is the earned
EdgeCertainty for that symbol's primary signal family — real for catalog symbols,
hard-capped at 0.50 for sample symbols — with its veto verdict colour. A "fire" is
meant to open the Council, not blindly execute. Sample tape is admin-resettable.
"""
import datetime
import math
import random

import data as datamod
import signals as sigmod
import stats as st

# symbol, asset-class, primary family, agent codename
SYMBOLS = [
    ("SPY", "eq", "momentum", "Cobalt-5"),
    ("QQQ", "eq", "momentum", "Vega-2"),
    ("AAPL", "eq", "mean_reversion", "Nimbus-7"),
    ("XLK", "eq", "momentum", "Cobalt-5"),
    ("MES", "fut", "breakout", "Basis-9"),
    ("BTCUSDT", "cry", "mean_reversion", "Orbit-3"),
    ("ETHUSDT", "cry", "mean_reversion", "Orbit-3"),
    ("SPCX", "eq", "volume_thrust", "Theta-4"),
]
SAMPLE_PX = {"QQQ": 456.98, "AAPL": 254.80, "MES": 5407.0,
             "BTCUSDT": 110224.0, "ETHUSDT": 4183.0, "SPCX": 92.44}
_CERT_CACHE = {}


def _synth_spark(sym, n=40):
    rng = random.Random((hash(sym) & 0xFFFF))
    p, out = 1.0, []
    for _ in range(n):
        p *= 1 + rng.uniform(-0.008, 0.0085)
        out.append(round(p, 4))
    return out


def _sample_conviction(sym):
    seed = (hash(sym) % 997) / 997.0
    t = datetime.datetime.now().timestamp() / 40.0
    v = math.sin(t + seed * 6.283) * 58 + math.sin(t * 0.37 + seed * 3.1) * 26
    return round(max(-92, min(92, v + ((hash(sym + "b") % 100) - 50) * 0.5)), 1)


def symbol_certainty(sym, family):
    """Per-symbol EdgeCertainty from that symbol's signal (real IC + per-symbol signal
    PnL bootstrap). Cached daily. Sample symbols are hard-capped at 0.50."""
    key = (sym, family, datetime.date.today().isoformat())
    if key in _CERT_CACHE:
        return _CERT_CACHE[key]
    ev, err = sigmod.evaluate(family, {}, sym)
    if err:
        return {"certainty": 0.0, "verdict": "No data", "axes": {}}
    bars = datamod.load_ohlcv(sym)
    vals = sigmod.series(family, bars, {})
    c = [b["c"] for b in bars]
    rets = []
    for i in range(len(bars) - 1):
        p = 1 if (vals[i] is not None and vals[i] > 0) else (-1 if vals[i] is not None else 0)
        if p:
            rets.append(p * (c[i + 1] / c[i] - 1))
    dsr = st.deflated_sharpe(rets, 1) if len(rets) > 30 else 0.5
    boot = st.block_bootstrap_sharpe(rets) if len(rets) > 30 else {"p_positive": 0.5}
    cert = st.edge_certainty({
        "worst_regime_dsr": dsr, "bootstrap_p_pos": boot["p_positive"], "dsr": dsr,
        "fdr_survived": ev["ic_pvalue"][1] < 0.10, "oos_consistency": ev["oos_consistency"],
        "forward_days": 0, "is_real_pit": ev["real_pit"], "capacity_aum": 1e6,
        "decay_hazard": max(0.0, 1 - ev["decay_half_life_d"] / 20.0),
    })
    out = {"certainty": cert["certainty"], "verdict": cert["verdict"],
           "axes": cert["axes"], "ic1": ev["ic"][1], "real_pit": ev["real_pit"]}
    _CERT_CACHE[key] = out
    return out


# ---------------- sample tape (admin-resettable) ----------------
def _hms(age):
    return (datetime.datetime.now() - datetime.timedelta(seconds=age)).strftime("%H:%M:%S")


def seed(store, force=False):
    if store.get_kv("console:seeded") == "1" and not force:
        return {"seeded": False}
    opens = [
        {"id": 1, "side": "LONG", "sym": "SPY", "strategy": "momentum", "fired_at": 0.62,
         "sl": 524.65, "entry": 527.02, "pt": 530.58, "last": 528.9, "pnl": 64,
         "opened": _hms(35)},
        {"id": 2, "side": "LONG", "sym": "SPCX", "strategy": "volume_thrust", "fired_at": 0.71,
         "sl": 92.07, "entry": 92.49, "pt": 93.11, "last": 92.8, "pnl": 332, "opened": _hms(180)},
        {"id": 3, "side": "SHORT", "sym": "AAPL", "strategy": "mean_reversion", "fired_at": 0.66,
         "sl": 256.4, "entry": 254.8, "pt": 251.2, "last": 254.1, "pnl": -35, "opened": _hms(300)},
    ]
    closed = [
        {"id": 11, "side": "SHORT", "sym": "SPY", "strategy": "mean_reversion", "pnl": 540,
         "result": "WIN", "closed": _hms(900)},
        {"id": 12, "side": "LONG", "sym": "XLK", "strategy": "momentum", "pnl": -180,
         "result": "LOSS", "closed": _hms(1800)},
        {"id": 13, "side": "LONG", "sym": "QQQ", "strategy": "momentum", "pnl": 205,
         "result": "WIN", "closed": _hms(2600)},
        {"id": 14, "side": "SHORT", "sym": "MES", "strategy": "breakout", "pnl": 244,
         "result": "WIN", "closed": _hms(3400)},
        {"id": 15, "side": "LONG", "sym": "BTCUSDT", "strategy": "mean_reversion", "pnl": 168,
         "result": "WIN", "closed": _hms(4200)},
    ]
    store.put("console_open", "state", {"rows": opens})
    store.put("console_closed", "state", {"rows": closed})
    store.set_kv("console:seeded", "1")
    store.feed("info", "CONSOLE — sample session seeded")
    return {"seeded": True}


def reset(store):
    store.put("console_open", "state", {"rows": []})
    store.put("console_closed", "state", {"rows": []})
    store.set_kv("console:seeded", "")
    store.feed("warn", "CONSOLE — sample data cleared by admin")
    return {"reset": True}


def snapshot(store, threshold=0.60):
    opens = (store.get("console_open", "state") or {}).get("rows", [])
    closed = (store.get("console_closed", "state") or {}).get("rows", [])
    symbols = []
    for sym, ac, fam, agent in SYMBOLS:
        conv = _sample_conviction(sym)
        cert = symbol_certainty(sym, fam)
        real = datamod.is_real(sym)
        px = (datamod.load_ohlcv(sym)[-1]["c"] if real else SAMPLE_PX.get(sym, 100.0))
        sym_pnl = sum(r["pnl"] for r in opens + closed if r["sym"] == sym)
        n_open = sum(1 for r in opens if r["sym"] == sym)
        spark = ([b["c"] for b in datamod.load_ohlcv(sym)[-40:]] if real
                 else _synth_spark(sym))
        symbols.append({
            "sym": sym, "ac": ac, "family": fam, "agent": agent, "conviction": conv,
            "price": round(px, 2), "side": "LONG" if conv >= 0 else "SHORT",
            "net_pct": round(conv), "sym_pnl": sym_pnl, "open_positions": n_open,
            "certainty": cert["certainty"], "verdict": cert["verdict"],
            "real_pit": real, "sig": max(1, int(abs(conv) / 14)), "spark": spark,
            "state": "filled" if n_open else ("arming" if abs(conv) >= threshold * 100
                                              else "scanning"),
        })
    wins = sum(1 for c in closed if c["result"] == "WIN")
    hit = round(wins / len(closed), 2) if closed else 0.0
    pnl = sum(r["pnl"] for r in opens + closed)
    eq, run = [0], 0
    for r in sorted(opens + closed, key=lambda r: r.get("closed") or r.get("opened") or ""):
        run += r["pnl"]; eq.append(run)
    # HONESTY: the session P&L / hit-rate / equity here come from the SEEDED demo tape, not
    # from real fills. Flag it so the UI can badge these numbers as sample, never live.
    demo = store.get_kv("console:seeded") == "1" and bool(opens or closed)
    return {"threshold": threshold, "symbols": symbols, "open_positions": opens,
            "closed": closed, "equity": eq, "demo": demo,
            "session": {"pnl": pnl, "hit_rate": hit, "fires": len(opens) + len(closed),
                        "open_risk": round(sum(abs(r.get("entry", 0) - r.get("sl", 0))
                                              for r in opens), 2),
                        "demo": demo},
            "clock": datetime.datetime.now().strftime("%H:%M:%S")}
