"""Dual backtest engines + real parity computation (blueprint M4/M5, stdlib only).

Two INDEPENDENT implementations of the momo-etf-v3 decision chain from
STRATEGY_GUIDE.md run over the same data/catalog CSVs:

  - engine_a ("lean-style"):     array-precomputed indicators, batch loop
  - engine_b ("nautilus-style"): event-driven bar feed, incremental indicators

Identical rules, different code paths — exactly what a LEAN->Nautilus port is.
The parity gate compares their outputs against risk/limits.yaml tolerances.
`inject_warmup_bug=True` shortens engine B's momentum warm-up by 20 bars to
reproduce the classic porting bug (used by tests and the GUI demo of a FAIL).

When LEAN CLI / nautilus_trader are installed (blueprint M4/M5 on the user's
machine), their runners replace engine_a/engine_b behind the same interface.
"""
import csv
import math
import os
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "data", "catalog"))

# strategy params — STRATEGY_GUIDE.md §3 (plateau center, frozen)
MOM, SKIP, SMA_N, VOLW = 252, 21, 200, 20
VOL_TGT, VOL_CAP, TOP_N = 0.10, 1.5, 3
ENTRY_CONF, EXIT_CONF = 0.50, 0.25
FEE_BPS = 1.0
START_CASH = 100_000.0
ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]


def provenance():
    p = os.path.join(CATALOG, ".provenance")
    return open(p).read().strip() if os.path.exists(p) else "unknown"


def load_bars(sym):
    path = os.path.join(CATALOG, f"{sym}.csv")
    if not os.path.exists(path):
        return None
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out.append((row["Date"], float(row["Close"])))
            except (KeyError, ValueError):
                continue
    return out


def _sigmoid_conf(mom_12_1, risk_on):
    if not risk_on:
        return 0.0
    return 1.0 / (1.0 + math.exp(-3.0 * (mom_12_1 / 0.08)))


def _metrics(dates, equity, trades, traded_notional, fees):
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    n = len(rets)
    mu = sum(rets) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / n) or 1e-12
    downs = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in downs) / n) or 1e-12
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    years = n / 252.0
    total_ret = equity[-1] / equity[0] - 1
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 else 0
    wins = sum(1 for r in rets if r > 0)
    avg_eq = sum(equity) / len(equity)
    return {
        "ret": total_ret, "cagr": cagr,
        "sharpe": mu / sd * math.sqrt(252),
        "sortino": mu / dsd * math.sqrt(252),
        "dd": mdd, "win": wins / n,
        "trades": len(trades), "fees": fees,
        "turn": traded_notional / avg_eq / years if years > 0 else 0,
        "window": f"{dates[0]} → {dates[-1]}",
        "equity_monthly": [round(equity[i], 2) for i in range(0, len(equity), 21)],
        "equity_daily": [round(v, 2) for v in equity],
        "dates_daily": dates,
    }


def _run_portfolio(dates, closes, decide):
    """Shared execution: `decide(i) -> {sym: weight} or None` (None = no rebalance).
    Fills at same-day close, FEE_BPS on traded notional."""
    cash, shares = START_CASH, {s: 0.0 for s in ETFS}
    equity, trades, traded, fees = [], [], 0.0, 0.0
    for i, d in enumerate(dates):
        px = {s: closes[s][i] for s in ETFS}
        eq = cash + sum(shares[s] * px[s] for s in ETFS)
        targets = decide(i, eq)
        if targets is not None:
            for s in ETFS:
                tgt_val = eq * targets.get(s, 0.0)
                cur_val = shares[s] * px[s]
                delta = tgt_val - cur_val
                if abs(delta) < max(eq * 1e-4, 1.0):
                    continue
                fee = abs(delta) * FEE_BPS / 1e4
                shares[s] += delta / px[s]
                cash -= delta + fee
                fees += fee
                traded += abs(delta)
                trades.append((d, s, "BUY" if delta > 0 else "SELL", round(abs(delta) / px[s], 2)))
        equity.append(cash + sum(shares[s] * px[s] for s in ETFS))
    m = _metrics(dates, equity, trades, traded, fees)
    m["trade_log"] = trades
    return m


# ------------------------------------------------------------------ engine A
def _params(overrides=None):
    P = {"mom": MOM, "skip": SKIP, "sma_n": SMA_N, "volw": VOLW,
         "vol_tgt": VOL_TGT, "vol_cap": VOL_CAP, "top_n": TOP_N,
         "entry": ENTRY_CONF, "exit": EXIT_CONF}
    if overrides:
        for k, v in overrides.items():
            if k in P:
                P[k] = v
    for k in ("mom", "skip", "sma_n", "volw", "top_n"):
        P[k] = max(int(P[k]), 2)
    return P


def engine_a(overrides=None):
    """'LEAN-style': indicators precomputed from full arrays. Optional param overrides
    power the REAL optimizer grid (Optimize & WF screen)."""
    P = _params(overrides)
    spy = load_bars("SPY")
    data = {s: load_bars(s) for s in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    dates = [spy[i][0] for i in range(n)]
    spy_c = [spy[i][1] for i in range(n)]
    closes = {s: [data[s][i][1] for i in range(n)] for s in ETFS}

    sma = [None] * n
    run = 0.0
    for i in range(n):
        run += spy_c[i]
        if i >= P['sma_n']:
            run -= spy_c[i - P['sma_n']]
        if i >= P['sma_n'] - 1:
            sma[i] = run / P['sma_n']

    entered = set()

    def decide(i, eq):
        if i < P['mom'] or sma[i] is None or _wd(dates[i]) != 0:  # Monday rebalance
            return None
        risk_on = spy_c[i] > sma[i]
        if not risk_on:
            entered.clear()
            return {s: 0.0 for s in ETFS}
        moms = {}
        for s in ETFS:
            c = closes[s]
            moms[s] = (c[i] / c[i - P['mom']] - 1) - (c[i] / c[i - P['skip']] - 1)
        top = set(sorted(ETFS, key=lambda s: moms[s], reverse=True)[:P['top_n']])
        targets = {}
        for s in ETFS:
            conf = _sigmoid_conf(moms[s], risk_on)
            thr = P['exit'] if s in entered else P['entry']
            if s in top and conf >= thr:
                c = closes[s]
                rets = [c[j] / c[j - 1] - 1 for j in range(i - P['volw'] + 1, i + 1)]
                mu = sum(rets) / P['volw']
                vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / P['volw']) * math.sqrt(252), 0.02)
                targets[s] = conf * min(P['vol_tgt'] / vol, P['vol_cap']) / P['top_n']
                entered.add(s)
            else:
                targets[s] = 0.0
                entered.discard(s)
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}
        return targets

    return _run_portfolio(dates, closes, decide)


# ------------------------------------------------------------------ engine B
class _EventStrategy:
    """'Nautilus-style': incremental state fed one bar at a time."""

    def __init__(self, warmup=MOM):
        self.warmup = warmup
        self.spy = deque(maxlen=SMA_N + 1)
        self.px = {s: deque(maxlen=MOM + 1) for s in ETFS}
        self.entered = set()
        self.bars_seen = 0

    def on_bar(self, spy_close, etf_closes):
        self.spy.append(spy_close)
        for s in ETFS:
            self.px[s].append(etf_closes[s])
        self.bars_seen += 1

    def rebalance(self):
        if self.bars_seen < max(self.warmup, SMA_N) or len(self.spy) < SMA_N:
            return None
        sma = sum(list(self.spy)[-SMA_N:]) / SMA_N
        risk_on = self.spy[-1] > sma
        if not risk_on:
            self.entered.clear()
            return {s: 0.0 for s in ETFS}
        moms = {}
        for s in ETFS:
            p = self.px[s]
            back = min(MOM, len(p) - 1, self.warmup)
            moms[s] = (p[-1] / p[-1 - back] - 1) - (p[-1] / p[-1 - SKIP] - 1)
        top = set(sorted(ETFS, key=lambda s: moms[s], reverse=True)[:TOP_N])
        targets = {}
        for s in ETFS:
            conf = _sigmoid_conf(moms[s], risk_on)
            thr = EXIT_CONF if s in self.entered else ENTRY_CONF
            if s in top and conf >= thr:
                p = list(self.px[s])[-(VOLW + 1):]
                rets = [p[j] / p[j - 1] - 1 for j in range(1, len(p))]
                mu = sum(rets) / len(rets)
                vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)) * math.sqrt(252), 0.02)
                targets[s] = conf * min(VOL_TGT / vol, VOL_CAP) / TOP_N
                self.entered.add(s)
            else:
                targets[s] = 0.0
                self.entered.discard(s)
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}
        return targets


def engine_b(inject_warmup_bug=False):
    spy = load_bars("SPY")
    data = {s: load_bars(s) for s in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    dates = [spy[i][0] for i in range(n)]
    closes = {s: [data[s][i][1] for i in range(n)] for s in ETFS}
    strat = _EventStrategy(warmup=MOM - 20 if inject_warmup_bug else MOM)

    def decide(i, eq):
        strat.on_bar(spy[i][1], {s: closes[s][i] for s in ETFS})
        if _wd(dates[i]) != 0:
            return None
        return strat.rebalance()

    return _run_portfolio(dates, closes, decide)


# ------------------------------------------------------------------ REAL optimizer + walk-forward
def optimize_grid():
    """Run the ACTUAL strategy across a momentum-lookback x vol-target grid.
    30 real backtests on the catalog data (~2s total). Returns sharpes + plateau info."""
    import time as _t
    t0 = _t.time()
    looks = [126, 168, 210, 252, 294, 336]
    vts = [0.06, 0.08, 0.10, 0.12, 0.14]
    cells = []
    for vt in vts:
        row = []
        for lk in looks:
            r = engine_a({"mom": lk, "vol_tgt": vt})
            row.append(round(r["sharpe"], 2) if r else None)
        cells.append(row)
    flat = [(cells[vi][li], vts[vi], looks[li]) for vi in range(len(vts)) for li in range(len(looks))
            if cells[vi][li] is not None]
    best = max(flat) if flat else (0, 0, 0)
    # plateau score of the FROZEN cell: mean of its 3x3 neighborhood
    fi, fj = vts.index(0.10), looks.index(252)
    neigh = [cells[i2][j2] for i2 in range(max(0, fi - 1), min(len(vts), fi + 2))
             for j2 in range(max(0, fj - 1), min(len(looks), fj + 2)) if cells[i2][j2] is not None]
    return {"looks": looks, "vts": vts, "cells": cells,
            "best": {"sharpe": best[0], "vol_tgt": best[1], "look": best[2]},
            "frozen": {"look": 252, "vol_tgt": 0.10, "sharpe": cells[fi][fj],
                       "plateau_mean": round(sum(neigh) / len(neigh), 2) if neigh else None},
            "runtime_s": round(_t.time() - t0, 1), "n_backtests": len(flat),
            "data_provenance": provenance()}


def walkforward():
    """REAL out-of-sample stability: one frozen-params backtest, equity split into
    ~6-month segments, Sharpe per segment. (Params are fixed, so this is an OOS
    stability check — the honest version of walk-forward for a no-fit strategy.)"""
    r = engine_a()
    if not r:
        return None
    eq, dts = r["equity_daily"], r["dates_daily"]
    start = max(MOM, SMA_N)          # skip warm-up (flat, zero-variance)
    seg = 126                        # ~6 months
    rows = []
    i = start
    while i + 40 < len(eq):          # require ≥40 trading days per segment
        j = min(i + seg, len(eq) - 1)
        rets = [eq[k] / eq[k - 1] - 1 for k in range(i + 1, j + 1)]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets)) or 1e-12
        sr = mu / sd * math.sqrt(252)
        rows.append({"from": dts[i][:7], "to": dts[j][:7],
                     "sharpe": round(sr, 2), "ret_pct": round((eq[j] / eq[i] - 1) * 100, 1),
                     "pass": sr > 0})
        i = j
    n_pass = sum(1 for w in rows if w["pass"])
    return {"windows": rows, "n_pass": n_pass, "n_total": len(rows),
            "verdict": f"{n_pass} of {len(rows)} out-of-sample segments profitable (Sharpe > 0)",
            "overall_sharpe": round(r["sharpe"], 2), "data_provenance": provenance()}


# ------------------------------------------------------------------ live signal (transparency)
def current_signal():
    """Today's signal with EVERY intermediate value exposed — this is the
    authoritative answer to 'how are signals generated'. Same math as the
    backtest engines and the paper node; docs/SIGNALS.md walks through it."""
    spy = load_bars("SPY")
    data = {s: load_bars(s) for s in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    i = n - 1
    spy_c = [spy[j][1] for j in range(n)]
    sma200 = sum(spy_c[i - SMA_N + 1: i + 1]) / SMA_N
    risk_on = spy_c[i] > sma200
    rows = []
    for sym in ETFS:
        c = [data[sym][j][1] for j in range(n)]
        mom_full = c[i] / c[i - MOM] - 1
        mom_recent = c[i] / c[i - SKIP] - 1
        mom_12_1 = mom_full - mom_recent
        rets = [c[j] / c[j - 1] - 1 for j in range(i - VOLW + 1, i + 1)]
        mu = sum(rets) / VOLW
        vol_ann = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / VOLW) * math.sqrt(252), 0.02)
        conf = 0.0 if not risk_on else 1.0 / (1.0 + math.exp(-3.0 * (mom_12_1 / 0.08)))
        rows.append({"sym": sym, "close": round(c[i], 2),
                     "mom_12m_pct": round(mom_full * 100, 2),
                     "mom_1m_pct": round(mom_recent * 100, 2),
                     "mom_12_1_pct": round(mom_12_1 * 100, 2),
                     "vol_ann_pct": round(vol_ann * 100, 1),
                     "confidence": round(conf, 3),
                     "vol_scalar": round(min(VOL_TGT / vol_ann, VOL_CAP), 2)})
    rows.sort(key=lambda r: r["mom_12_1_pct"], reverse=True)
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
        r["in_top3"] = rank <= TOP_N
        r["passes_entry"] = r["in_top3"] and r["confidence"] >= ENTRY_CONF
        r["target_weight_pct"] = round(
            (r["confidence"] * r["vol_scalar"] / TOP_N) * 100, 1) if r["passes_entry"] and risk_on else 0.0
    gross = sum(r["target_weight_pct"] for r in rows)
    if gross > 100:
        for r in rows:
            r["target_weight_pct"] = round(r["target_weight_pct"] * 100 / gross, 1)
    return {
        "strategy": "momo-etf-v3", "as_of": spy[i][0],
        "data_provenance": provenance(),
        "pipeline": ["1. REGIME: SPY close vs 200-day SMA",
                     "2. RANK: 12-1 momentum across 9 sector ETFs",
                     "3. FILTER: top-3 AND sigmoid confidence >= 0.50",
                     "4. SIZE: confidence x min(10%/vol, 1.5x) / 3",
                     "5. RISK: pre-route validation (risk/limits.yaml)",
                     "6. ORDER: paper node submits deltas to Alpaca"],
        "regime": {"spy_close": round(spy_c[i], 2), "sma200": round(sma200, 2),
                   "risk_on": risk_on,
                   "rule": "risk_on = SPY_close > SMA200; if false -> liquidate everything"},
        "params": {"momentum_lookback_days": MOM, "momentum_skip_days": SKIP,
                   "regime_sma_days": SMA_N, "vol_window_days": VOLW,
                   "vol_target": VOL_TGT, "entry_confidence": ENTRY_CONF,
                   "exit_confidence": EXIT_CONF, "top_n": TOP_N},
        "table": rows,
        "decision": ("FLAT — regime risk-off" if not risk_on else
                     "HOLD/REBALANCE to targets: " + ", ".join(
                         f"{r['sym']} {r['target_weight_pct']}%" for r in rows if r["target_weight_pct"] > 0)),
    }


# ------------------------------------------------------------------ signal history (Strategy Lab)
def signal_history(sym="XLK", params=None):
    """Full historical signal trace on REAL catalog data for one ETF:
    dates, SPY+SMA+regime, the ETF's price/momentum/confidence/weight series,
    every trigger event WITH its reason, and regime impact statistics.
    `params` overrides run a WHAT-IF (never mutates the frozen strategy).
    Regime supports an optional anti-whipsaw band (docs/REGIME.md):
      ON  when SPY > SMA*(1+band);  OFF when SPY < SMA*(1-band);  else hold state."""
    P = {"mom": MOM, "skip": SKIP, "sma_n": SMA_N, "volw": VOLW,
         "vol_tgt": VOL_TGT, "vol_cap": VOL_CAP, "top_n": TOP_N,
         "entry": ENTRY_CONF, "exit": EXIT_CONF, "regime_band_pct": 0.0}
    if params:
        for k, v in params.items():
            if k in P:
                try:
                    P[k] = float(v)
                except (TypeError, ValueError):
                    pass
    for k in ("mom", "skip", "sma_n", "volw", "top_n"):
        P[k] = max(int(P[k]), 2)
    if sym not in ETFS:
        sym = ETFS[0]

    spy = load_bars("SPY")
    data = {s2: load_bars(s2) for s2 in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    dates = [spy[i][0] for i in range(n)]
    spy_c = [spy[i][1] for i in range(n)]
    closes = {s2: [data[s2][i][1] for i in range(n)] for s2 in ETFS}

    sma_n, band = P["sma_n"], P["regime_band_pct"] / 100.0
    sma = [None] * n
    run = 0.0
    for i in range(n):
        run += spy_c[i]
        if i >= sma_n:
            run -= spy_c[i - sma_n]
        if i >= sma_n - 1:
            sma[i] = run / sma_n

    # regime with hysteresis band
    regime, state = [], False
    for i in range(n):
        if sma[i] is None:
            regime.append(0)
            continue
        if spy_c[i] > sma[i] * (1 + band):
            state = True
        elif spy_c[i] < sma[i] * (1 - band):
            state = False
        regime.append(1 if state else 0)

    def mom_12_1(s2, i):
        c = closes[s2]
        if i < P["mom"]:
            return None
        return (c[i] / c[i - P["mom"]] - 1) - (c[i] / c[i - P["skip"]] - 1)

    conf_series, wt_series, events = [], [], []
    entered = set()
    cur_wt = {s2: 0.0 for s2 in ETFS}
    warm = max(P["mom"], sma_n)
    for i in range(n):
        m_sel = mom_12_1(sym, i)
        c_sel = 0.0 if (m_sel is None or not regime[i]) else 1.0 / (1.0 + math.exp(-3.0 * (m_sel / 0.08)))
        conf_series.append(round(c_sel, 3))
        if i >= warm and _wd(dates[i]) == 0:  # Monday rebalance
            if not regime[i]:
                if any(cur_wt[s2] > 0 for s2 in ETFS) and cur_wt[sym] > 0:
                    events.append({"date": dates[i], "action": "EXIT",
                                   "reason": "regime flipped RISK-OFF (SPY < SMA%d%s) — liquidate all"
                                   % (sma_n, f"×(1−{P['regime_band_pct']}%)" if band else "")})
                entered.clear()
                cur_wt = {s2: 0.0 for s2 in ETFS}
            else:
                moms = {s2: mom_12_1(s2, i) for s2 in ETFS}
                ranked = sorted(ETFS, key=lambda s2: moms[s2] or -9, reverse=True)
                rank = ranked.index(sym) + 1
                top = set(ranked[:int(P["top_n"])])
                thr = P["exit"] if sym in entered else P["entry"]
                was_in = cur_wt[sym] > 0
                if sym in top and c_sel >= thr:
                    c = closes[sym]
                    rets = [c[j] / c[j - 1] - 1 for j in range(i - P["volw"] + 1, i + 1)]
                    mu = sum(rets) / P["volw"]
                    vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / P["volw"]) * math.sqrt(252), 0.02)
                    w = c_sel * min(P["vol_tgt"] / vol, P["vol_cap"]) / P["top_n"]
                    if not was_in:
                        events.append({"date": dates[i], "action": "ENTRY",
                                       "reason": f"rank #{rank} of 9 (12-1 mom {moms[sym]*100:.1f}%), "
                                                 f"confidence {c_sel:.2f} ≥ {P['entry']:.2f}, regime ON, "
                                                 f"vol {vol*100:.0f}% → weight {w*100:.1f}%"})
                    entered.add(sym)
                    cur_wt[sym] = w
                else:
                    if was_in:
                        why = (f"dropped to rank #{rank} (out of top {int(P['top_n'])})" if sym not in top
                               else f"confidence {c_sel:.2f} < exit threshold {P['exit']:.2f}")
                        events.append({"date": dates[i], "action": "EXIT", "reason": why})
                    entered.discard(sym)
                    cur_wt[sym] = 0.0
        wt_series.append(round(cur_wt[sym] * 100, 1))

    # regime impact statistics
    flips = []
    for i in range(1, n):
        if regime[i] != regime[i - 1] and sma[i] is not None:
            flips.append({"date": dates[i], "to": "RISK-ON" if regime[i] else "RISK-OFF"})
    days_on = sum(regime)
    off_ret = 1.0
    for i in range(1, n):
        if not regime[i - 1]:
            off_ret *= spy_c[i] / spy_c[i - 1]
    peak, spy_dd = spy_c[0], 0.0
    for v in spy_c:
        peak = max(peak, v)
        spy_dd = min(spy_dd, v / peak - 1)
    return {
        "sym": sym, "dates": dates, "spy": [round(v, 2) for v in spy_c],
        "sma": [round(v, 2) if v else None for v in sma], "regime": regime,
        "etf_close": [round(v, 2) for v in closes[sym]],
        "confidence": conf_series, "weight_pct": wt_series,
        "events": events[-60:],
        "regime_stats": {
            "pct_risk_on": round(days_on / n * 100, 1),
            "n_flips": len(flips), "flips": flips[-12:],
            "spy_return_during_off_pct": round((off_ret - 1) * 100, 1),
            "spy_max_dd_pct": round(spy_dd * 100, 1),
            "band_pct": P["regime_band_pct"],
            "explain": ("While OFF the strategy holds cash; SPY moved "
                        f"{(off_ret-1)*100:+.1f}% in those periods — negative means the filter dodged losses."),
        },
        "params_used": P, "is_whatif": bool(params),
        "data_provenance": provenance(),
        "timing": "Signals use daily closes; rebalance decision on Monday's close, "
                  "paper node executes ~09:35 AM ET next session.",
    }


# ------------------------------------------------------------------ real nautilus (M5)
NAUT_PY = os.path.expanduser("~/.quantdesk/venv/bin/python")
NAUT_SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "nautilus_backtest.py"))


def engine_nautilus():
    """Run the REAL nautilus_trader backtest via the venv. Returns engine-metric
    dict (same shape as engine_a/engine_b) or None if unavailable/failed."""
    import subprocess
    if not os.path.exists(NAUT_PY):
        return None
    try:
        p = subprocess.run([NAUT_PY, NAUT_SCRIPT], capture_output=True, text=True, timeout=180)
        line = p.stdout.strip().splitlines()[-1]
        d = __import__("json").loads(line)
        if "error" in d:
            return None
        d["trade_log"] = [tuple(t) for t in d.get("trade_log", [])]
        d.setdefault("equity_monthly", [])
        d.setdefault("window", "")
        return d
    except Exception:
        return None


# ------------------------------------------------------------------ parity
def compute_parity(a, b, tol=None):
    tol = tol or {"total_return_diff_pp": 1.0, "sharpe_diff": 0.10,
                  "trade_count_diff_pct": 2.0, "max_dd_diff_pp": 1.0}
    checks = [
        ("Total return diff", abs(a["ret"] - b["ret"]) * 100, tol["total_return_diff_pp"], "pp"),
        ("Sharpe diff", abs(a["sharpe"] - b["sharpe"]), tol["sharpe_diff"], ""),
        ("Trade count diff", abs(a["trades"] - b["trades"]) / max(a["trades"], 1) * 100,
         tol["trade_count_diff_pct"], "%"),
        ("Max DD diff", abs(a["dd"] - b["dd"]) * 100, tol["max_dd_diff_pp"], "pp"),
    ]
    rows = [{"m": name, "v": f"{val:.2f}{unit}", "lim": f"≤ {lim:.2f}{unit}", "ok": val <= lim}
            for name, val, lim, unit in checks]
    diffs = _first_trade_divergences(a.get("trade_log", []), b.get("trade_log", []))
    return {"tol": rows, "pass": all(r["ok"] for r in rows), "diffs": diffs}


def _first_trade_divergences(la, lb, limit=3):
    out = []
    for i in range(min(len(la), len(lb))):
        da, db = la[i], lb[i]
        if da[:3] != db[:3]:
            out.append({"t": da[0], "sym": da[1],
                        "lean": f"{da[2]} {da[3]} {da[1]}",
                        "naut": f"{db[2]} {db[3]} {db[1]} @ {db[0]}",
                        "d": "⚠ order sequence diverges here"})
            if len(out) >= limit:
                break
    if not out and len(la) != len(lb):
        out.append({"t": "-", "sym": "-", "lean": f"{len(la)} trades total",
                    "naut": f"{len(lb)} trades total", "d": "⚠ trade count differs"})
    return out


def _wd(date_str):
    import datetime
    return datetime.date.fromisoformat(date_str).weekday()


def fmt_stats(m):
    """Format engine metrics into the GUI parity-card shape."""
    return {
        "ret": f"{m['ret'] * 100:+.1f}%", "cagr": f"{m['cagr'] * 100:.1f}%",
        "sharpe": round(m["sharpe"], 2), "sortino": round(m["sortino"], 2),
        "dd": f"{m['dd'] * 100:.1f}%", "win": f"{m['win'] * 100:.1f}%",
        "trades": m["trades"], "turn": f"{m['turn']:.1f}×",
        "fees": f"${m['fees']:,.0f}",
    }


def run_parity_backtest(inject_warmup_bug=False, tolerances=None, engine="auto"):
    """engine: 'auto' tries REAL nautilus_trader first, falls back to internal engine B."""
    a = engine_a()
    b = None
    b_name = "internal-event-engine"
    if engine in ("auto", "nautilus") and not inject_warmup_bug:
        b = engine_nautilus()
        if b is not None:
            b_name = "nautilus_trader " + str(b.get("version", ""))
    if b is None:
        if engine == "nautilus":
            return None
        b = engine_b(inject_warmup_bug=inject_warmup_bug)
    if a is None or b is None:
        return None
    par = compute_parity(a, b, tolerances)
    note = ("Engines reconciled — trade sequences match within tolerance."
            if par["pass"] else
            "Divergence detected — first suspect: indicator warm-up length in the event-driven port.")
    return {
        "eq_a": a.get("equity_monthly") or [],
        "eq_b": b.get("equity_monthly") or [],
        "naut_engine": b_name,
        "window": a["window"] + f" · daily bars · fees {FEE_BPS:.0f}bp · data: {provenance()} · naut leg: {b_name}",
        "lean": fmt_stats(a), "naut": fmt_stats(b),
        "tol": par["tol"], "pass": par["pass"],
        "diffs": par["diffs"], "diffNote": note,
        "seed": 11, "div": 0 if par["pass"] else 1,
        "raw": {"a_sharpe": a["sharpe"], "b_sharpe": b["sharpe"],
                "a_trades": a["trades"], "b_trades": b["trades"]},
    }
