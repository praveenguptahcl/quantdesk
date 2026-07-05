"""Signal layer (M19): parameterized variants of popular signals, evaluated with
REAL alpha diagnostics on the catalog's daily OHLCV bars — IC (rank correlation
vs forward returns), IC decay, hit rate, turnover, a cost-adjusted signal-only
backtest, and a regime split. Signals are shared in a community library and can
be promoted to cross-sectional strategies (top-N over the ETF universe with the
same SPY-SMA200 regime gate as momo-etf-v3). Stdlib only.
"""
import csv
import datetime
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "data", "catalog"))
ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]
COST_BPS = 5.0   # per unit turnover, both ways

TEMPLATES = {
    "mom_12_1":  {"label": "12-1 Momentum", "family": "momentum",
                  "desc": "Return over `look` days, skipping the most recent `skip` (reversal zone).",
                  "params": {"look": 252, "skip": 21}},
    "sma_cross": {"label": "SMA Cross", "family": "trend",
                  "desc": "+1 when price is above its `n`-day moving average, -1 below.",
                  "params": {"n": 200}},
    "rsi":       {"label": "RSI Mean-Reversion", "family": "mean_reversion",
                  "desc": "Buy when RSI(`n`) < `buy_below`, sell when > `sell_above`.",
                  "params": {"n": 14, "buy_below": 30, "sell_above": 70}},
    "boll_z":    {"label": "Bollinger Z-Score", "family": "mean_reversion",
                  "desc": "Fade the z-score of price vs its `n`-day mean (capped at `cap_z`).",
                  "params": {"n": 20, "cap_z": 2.0}},
    "macd":      {"label": "MACD Histogram", "family": "momentum",
                  "desc": "EMA(`fast`)-EMA(`slow`) minus its EMA(`sig`) signal line.",
                  "params": {"fast": 12, "slow": 26, "sig": 9}},
    "donchian":  {"label": "Donchian Breakout", "family": "breakout",
                  "desc": "+1 on a new `n`-day high, -1 on a new `n`-day low, hold in between.",
                  "params": {"n": 55}},
    "gap_rev":   {"label": "Gap Reversal", "family": "mean_reversion",
                  "desc": "Fade overnight gaps larger than `min_gap_pct`% of prior close.",
                  "params": {"min_gap_pct": 0.5}},
    "vol_surge": {"label": "Volume Surge", "family": "volume",
                  "desc": "When volume > `mult`× its `n`-day average, follow that day's direction.",
                  "params": {"n": 20, "mult": 2.0}},
    "hi52_prox": {"label": "52-Week-High Proximity", "family": "momentum",
                  "desc": "Long when price is near its `n`-day high (anchoring effect).",
                  "params": {"n": 252, "min_prox": 0.9}},
    "vol_regime": {"label": "Volatility Regime", "family": "volatility",
                   "desc": "+1 when `n`-day realized vol is below its `pct` percentile, else -1.",
                   "params": {"n": 20, "pct": 0.8}},
}


def load_ohlcv(sym):
    path = os.path.join(CATALOG, f"{sym}.csv")
    if not os.path.exists(path):
        return None
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out.append({"d": row["Date"], "o": float(row["Open"]), "h": float(row["High"]),
                            "l": float(row["Low"]), "c": float(row["Close"]),
                            "v": float(row["Volume"])})
            except (KeyError, ValueError):
                continue
    return out or None


def available_assets():
    return sorted(f[:-4] for f in os.listdir(CATALOG)
                  if f.endswith(".csv") and load_ohlcv(f[:-4]))


def merged_params(template, overrides):
    base = dict(TEMPLATES[template]["params"])
    for k, v in (overrides or {}).items():
        if k in base:
            try:
                base[k] = type(base[k])(v)
            except (TypeError, ValueError):
                pass
    return base


def _ema(vals, n):
    k, out, e = 2.0 / (n + 1), [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def series(template, bars, params):
    """Returns (values, positions): raw signal value and position in [-1,1] per bar
    (None during warmup). Positions are applied to the NEXT day's return."""
    P = merged_params(template, params)
    c = [b["c"] for b in bars]
    n_ = len(bars)
    vals, pos = [None] * n_, [None] * n_
    if template == "mom_12_1":
        lk, sk = int(P["look"]), int(P["skip"])
        for i in range(lk, n_):
            v = c[i - sk] / c[i - lk] - 1
            vals[i], pos[i] = v, max(-1.0, min(1.0, v / 0.15))
    elif template == "sma_cross":
        n = int(P["n"]); run = 0.0
        for i in range(n_):
            run += c[i]
            if i >= n:
                run -= c[i - n]
            if i >= n - 1:
                sma = run / n
                vals[i] = c[i] / sma - 1
                pos[i] = 1.0 if c[i] > sma else -1.0
    elif template == "rsi":
        n = int(P["n"]); g = l = None
        for i in range(1, n_):
            ch = c[i] - c[i - 1]
            up, dn = max(ch, 0), max(-ch, 0)
            g = up if g is None else (g * (n - 1) + up) / n
            l = dn if l is None else (l * (n - 1) + dn) / n
            if i >= n:
                rsi = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
                vals[i] = 50 - rsi   # higher = more oversold = more bullish
                pos[i] = 1.0 if rsi < P["buy_below"] else (-1.0 if rsi > P["sell_above"] else 0.0)
    elif template == "boll_z":
        n = int(P["n"])
        for i in range(n - 1, n_):
            w = c[i - n + 1:i + 1]
            m = sum(w) / n
            sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5 or 1e-9
            z = (c[i] - m) / sd
            vals[i] = -z
            pos[i] = max(-1.0, min(1.0, -z / float(P["cap_z"])))
    elif template == "macd":
        ef, es = _ema(c, int(P["fast"])), _ema(c, int(P["slow"]))
        line = [f - s2 for f, s2 in zip(ef, es)]
        sig = _ema(line, int(P["sig"]))
        start = int(P["slow"]) + int(P["sig"])
        for i in range(start, n_):
            h = (line[i] - sig[i]) / c[i] * 100
            vals[i], pos[i] = h, max(-1.0, min(1.0, h / 0.8))
    elif template == "donchian":
        n = int(P["n"]); cur = 0.0
        for i in range(n, n_):
            hh = max(b["h"] for b in bars[i - n:i])
            ll = min(b["l"] for b in bars[i - n:i])
            if c[i] > hh:
                cur = 1.0
            elif c[i] < ll:
                cur = -1.0
            vals[i] = (c[i] - ll) / (hh - ll) * 2 - 1 if hh > ll else 0.0
            pos[i] = cur
    elif template == "gap_rev":
        thr = float(P["min_gap_pct"]) / 100
        for i in range(1, n_):
            gap = bars[i]["o"] / c[i - 1] - 1
            vals[i] = -gap * 100
            pos[i] = -1.0 if gap > thr else (1.0 if gap < -thr else 0.0)
    elif template == "vol_surge":
        n = int(P["n"])
        v = [b["v"] for b in bars]
        for i in range(n, n_):
            av = sum(v[i - n:i]) / n or 1e-9
            ratio = v[i] / av
            day = 1.0 if c[i] >= bars[i]["o"] else -1.0
            vals[i] = (ratio - 1) * day
            pos[i] = day if ratio > float(P["mult"]) else 0.0
    elif template == "hi52_prox":
        n = int(P["n"])
        for i in range(n, n_):
            hi = max(c[i - n:i + 1])
            prox = c[i] / hi
            vals[i] = prox - 0.5
            pos[i] = 1.0 if prox >= float(P["min_prox"]) else 0.0
    elif template == "vol_regime":
        n = int(P["n"]); rvs = []
        rets = [None] + [c[i] / c[i - 1] - 1 for i in range(1, n_)]
        for i in range(n + 1, n_):
            w = rets[i - n + 1:i + 1]
            m = sum(w) / n
            rv = (sum((x - m) ** 2 for x in w) / n) ** 0.5 * math.sqrt(252)
            rvs.append(rv)
            hist = sorted(rvs[-252:])
            pct = hist[int(float(P["pct"]) * (len(hist) - 1))]
            vals[i] = pct - rv
            pos[i] = 1.0 if rv < pct else -1.0
    return vals, pos


def _rankcorr(xs, ys):
    n = len(xs)
    if n < 20:
        return None
    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        for rank, i in enumerate(order):
            r[i] = rank
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx = my = (n - 1) / 2.0
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5 or 1e-9
    return num / den


_EVAL_CACHE = {}


def evaluate(template, params, asset):
    """Full alpha diagnostics for one signal variant on one asset. Cached per day."""
    if template not in TEMPLATES:
        return None, "unknown template"
    key = json.dumps([template, merged_params(template, params), asset], sort_keys=True)
    today = datetime.date.today().isoformat()
    hit = _EVAL_CACHE.get(key)
    if hit and hit[0] == today:
        return hit[1], None
    bars = load_ohlcv(asset)
    if not bars or len(bars) < 300:
        return None, f"no catalog bars for {asset} — add it on Data & Artifacts"
    vals, pos = series(template, bars, params)
    c = [b["c"] for b in bars]
    n = len(bars)
    fwd = {h: [c[i + h] / c[i] - 1 if i + h < n else None for i in range(n)] for h in (1, 5, 10, 21)}
    ic = {}
    for h in (1, 5, 10, 21):
        pairs = [(vals[i], fwd[h][i]) for i in range(n) if vals[i] is not None and fwd[h][i] is not None]
        ic[h] = round(_rankcorr([p[0] for p in pairs], [p[1] for p in pairs]) or 0, 3)
    act = [(pos[i], fwd[1][i]) for i in range(n) if pos[i] not in (None, 0.0) and fwd[1][i] is not None]
    hitrate = round(sum(1 for p, r in act if p * r > 0) / len(act), 3) if act else 0.0
    # signal-only backtest: position earns next-day return, minus turnover cost
    eq, equity, dates, prev = 1.0, [], [], 0.0
    turns = []
    for i in range(n - 1):
        p = pos[i] if pos[i] is not None else 0.0
        turns.append(abs(p - prev))
        eq *= 1 + p * fwd[1][i] - abs(p - prev) * COST_BPS / 10000
        prev = p
        equity.append(eq)
        dates.append(bars[i + 1]["d"])
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 or 1e-9
    sharpe = round(mu / sd * math.sqrt(252), 2)
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    # regime split (SPY > SMA200)
    spy = load_ohlcv("SPY")
    ric = {"risk_on": None, "risk_off": None}
    if spy:
        sc = [b["c"] for b in spy]
        spymap = {}
        run = 0.0
        for i, b in enumerate(spy):
            run += sc[i]
            if i >= 200:
                run -= sc[i - 200]
            if i >= 199:
                spymap[b["d"]] = sc[i] > run / 200
        on, off = [], []
        for i in range(n):
            if vals[i] is None or fwd[1][i] is None:
                continue
            flag = spymap.get(bars[i]["d"])
            (on if flag else off).append((vals[i], fwd[1][i]))
        if len(on) > 30:
            ric["risk_on"] = round(_rankcorr([p[0] for p in on], [p[1] for p in on]) or 0, 3)
        if len(off) > 30:
            ric["risk_off"] = round(_rankcorr([p[0] for p in off], [p[1] for p in off]) or 0, 3)
    yearly = {}
    ystart = {}
    for i, dt in enumerate(dates):
        y = dt[:4]
        ystart.setdefault(y, equity[i - 1] if i else 1.0)
        yearly[y] = round((equity[i] / ystart[y] - 1) * 100, 2)
    dd_series, pk = [], equity[0]
    for e in equity:
        pk = max(pk, e)
        dd_series.append(round((e / pk - 1) * 100, 2))
    step = max(1, len(equity) // 260)
    out = {"template": template, "asset": asset, "params": merged_params(template, params),
           "yearly": yearly, "dd_series": dd_series[::step],
           "n_days": len(equity), "ic": ic, "hit_rate": hitrate,
           "turnover": round(sum(turns) / len(turns), 3),
           "exposure": round(sum(1 for p in pos if p not in (None, 0.0)) / n, 3),
           "sharpe": sharpe, "max_dd_pct": round(mdd * 100, 2),
           "total_return_pct": round((equity[-1] - 1) * 100, 2),
           "regime_ic": ric, "cost_bps": COST_BPS,
           "equity": [round(e, 4) for e in equity[::step]],
           "dates": dates[::step],
           "verdict": _verdict(ic[1], hitrate, sharpe)}
    _EVAL_CACHE[key] = (today, out)
    return out, None


def _verdict(ic1, hitrate, sharpe):
    if ic1 >= 0.03 and sharpe >= 0.5:
        return "promising — IC and Sharpe both clear typical single-alpha bars"
    if ic1 >= 0.02 or sharpe >= 0.3:
        return "weak-positive — usable inside a multi-signal strategy, not alone"
    if ic1 <= -0.02:
        return "inverted — the OPPOSITE of this rule has predictive power here"
    return "no edge detected on this asset/parameterization — try other params or assets"


def strategy_engine(template, params, top_n=3, vol_tgt=0.10):
    """Promote a signal to a cross-sectional strategy: rank the ETF universe by the
    signal's raw value daily, hold the top `top_n` (long-only, gated by the same
    SPY>SMA200 regime filter as momo-etf-v3), vol-targeted. Real backtest."""
    data = {s: load_ohlcv(s) for s in ETFS}
    data = {s: b for s, b in data.items() if b}
    spy = load_ohlcv("SPY")
    if not spy or len(data) < 4:
        return None
    dates = [b["d"] for b in spy]
    idx = {s: {b["d"]: i for i, b in enumerate(bars)} for s, bars in data.items()}
    sigs = {s: series(template, bars, params)[0] for s, bars in data.items()}
    sc = [b["c"] for b in spy]
    eq, equity, prev_w = 1.0, [], {}
    run = 0.0
    rets_hist = []
    for i, b in enumerate(spy):
        run += sc[i]
        if i >= 200:
            run -= sc[i - 200]
        risk_on = i >= 199 and sc[i] > run / 200
        # today's scores
        scores = {}
        for s in data:
            j = idx[s].get(b["d"])
            if j is not None and sigs[s][j] is not None:
                scores[s] = sigs[s][j]
        w = {}
        if risk_on and len(scores) >= top_n:
            top = sorted(scores, key=scores.get, reverse=True)[:top_n]
            for s in top:
                w[s] = 1.0 / top_n
        # vol targeting on realized portfolio vol
        if len(rets_hist) >= 20:
            m = sum(rets_hist[-20:]) / 20
            rv = (sum((r - m) ** 2 for r in rets_hist[-20:]) / 20) ** 0.5 * math.sqrt(252)
            scale = min(1.5, vol_tgt / rv) if rv > 0 else 1.0
            w = {s: x * scale for s, x in w.items()}
        # next-day portfolio return
        if i + 1 < len(spy):
            nd = spy[i + 1]["d"]
            r = 0.0
            for s, x in w.items():
                j0, j1 = idx[s].get(b["d"]), idx[s].get(nd)
                if j0 is not None and j1 is not None:
                    r += x * (data[s][j1]["c"] / data[s][j0]["c"] - 1)
            turn = sum(abs(w.get(s, 0) - prev_w.get(s, 0)) for s in set(w) | set(prev_w))
            r -= turn * COST_BPS / 10000
            eq *= 1 + r
            rets_hist.append(r)
            equity.append(eq)
            prev_w = w
    if len(equity) < 30:
        return None
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 or 1e-9
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return {"equity_daily": equity, "sharpe": round(mu / sd * math.sqrt(252), 2),
            "max_dd_pct": round(mdd * 100, 2),
            "total_return_pct": round((equity[-1] - 1) * 100, 2),
            "dates": dates[1:len(equity) + 1]}


def combo_engine(sig_defs, top_n=3, vol_tgt=0.10):
    """Compose a strategy from MULTIPLE library signals: each day, rank the ETF
    universe by each signal's raw value (rank in [0,1], scale-free), combine as
    the weighted sum of ranks, hold the top `top_n` — same SPY>SMA200 regime gate,
    vol targeting and costs as everything else. Real backtest."""
    data = {s: load_ohlcv(s) for s in ETFS}
    data = {s: b for s, b in data.items() if b}
    spy = load_ohlcv("SPY")
    if not spy or len(data) < 4 or not sig_defs:
        return None
    idx = {s: {b["d"]: i for i, b in enumerate(bars)} for s, bars in data.items()}
    allsigs = []   # per sig_def: {etf: values[]}
    wsum = sum(abs(float(d.get("weight", 1))) for d in sig_defs) or 1.0
    for d in sig_defs:
        allsigs.append({s: series(d["template"], bars, d.get("params"))[0]
                        for s, bars in data.items()})
    sc = [b["c"] for b in spy]
    eq, equity, prev_w, run, rets_hist = 1.0, [], {}, 0.0, []
    for i, b in enumerate(spy):
        run += sc[i]
        if i >= 200:
            run -= sc[i - 200]
        risk_on = i >= 199 and sc[i] > run / 200
        combined = {}
        for k, d in enumerate(sig_defs):
            vals = {}
            for s in data:
                j = idx[s].get(b["d"])
                if j is not None and allsigs[k][s][j] is not None:
                    vals[s] = allsigs[k][s][j]
            if len(vals) < top_n:
                continue
            order = sorted(vals, key=vals.get)
            n_v = len(order) - 1 or 1
            for r_, s in enumerate(order):
                combined[s] = combined.get(s, 0.0) + \
                    float(d.get("weight", 1)) / wsum * (r_ / n_v)
        w = {}
        if risk_on and len(combined) >= top_n:
            top = sorted(combined, key=combined.get, reverse=True)[:top_n]
            for s in top:
                w[s] = 1.0 / top_n
        if len(rets_hist) >= 20:
            m = sum(rets_hist[-20:]) / 20
            rv = (sum((r - m) ** 2 for r in rets_hist[-20:]) / 20) ** 0.5 * math.sqrt(252)
            scale = min(1.5, vol_tgt / rv) if rv > 0 else 1.0
            w = {s: x * scale for s, x in w.items()}
        if i + 1 < len(spy):
            nd = spy[i + 1]["d"]
            r = 0.0
            for s, x in w.items():
                j0, j1 = idx[s].get(b["d"]), idx[s].get(nd)
                if j0 is not None and j1 is not None:
                    r += x * (data[s][j1]["c"] / data[s][j0]["c"] - 1)
            turn = sum(abs(w.get(s, 0) - prev_w.get(s, 0)) for s in set(w) | set(prev_w))
            r -= turn * COST_BPS / 10000
            eq *= 1 + r
            rets_hist.append(r)
            equity.append(eq)
            prev_w = w
    if len(equity) < 30:
        return None
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5 or 1e-9
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    step = max(1, len(equity) // 260)
    return {"equity_daily": equity, "equity": [round(e, 4) for e in equity[::step]],
            "sharpe": round(mu / sd * math.sqrt(252), 2),
            "max_dd_pct": round(mdd * 100, 2),
            "total_return_pct": round((equity[-1] - 1) * 100, 2)}


_WK_CACHE = {}


def strategy_week_return(spec):
    """Real last-5-trading-day return of a promoted signal strategy. Cached per day."""
    key = json.dumps(spec, sort_keys=True)
    today = datetime.date.today().isoformat()
    hit = _WK_CACHE.get(key)
    if hit and hit[0] == today:
        return hit[1]
    if spec.get("signals"):
        r = combo_engine(spec["signals"], int(spec.get("top_n", 3)),
                         float(spec.get("vol_tgt", 0.10)))
    else:
        r = strategy_engine(spec.get("template"), spec.get("params"),
                            int(spec.get("top_n", 3)), float(spec.get("vol_tgt", 0.10)))
    val = 0.0
    if r and len(r["equity_daily"]) >= 6:
        eq = r["equity_daily"]
        val = eq[-1] / eq[-6] - 1
    _WK_CACHE[key] = (today, val)
    return val


# ---------------- verbal -> signal spec (builtin parser) ----------------
_KEYS = [("rsi", "rsi"), ("bollinger", "boll_z"), ("z-score", "boll_z"), ("zscore", "boll_z"),
         ("macd", "macd"), ("breakout", "donchian"), ("donchian", "donchian"),
         ("gap", "gap_rev"), ("volume", "vol_surge"),
         ("52", "hi52_prox"), ("high proximity", "hi52_prox"),
         ("moving average", "sma_cross"), ("sma", "sma_cross"),
         ("volatility", "vol_regime"), ("momentum", "mom_12_1")]


def ai_parse_signal(text, assets):
    import re
    t = text.lower()
    template = next((v for k, v in _KEYS if k in t), "mom_12_1")
    params = dict(TEMPLATES[template]["params"])
    m = re.search(r"(\d+)[\s-]*day", t)
    if m:
        n = int(m.group(1))
        for k in ("n", "look"):
            if k in params:
                params[k] = n
    if template == "rsi":
        m = re.search(r"below\s+(\d+)", t)
        if m:
            params["buy_below"] = int(m.group(1))
        m = re.search(r"above\s+(\d+)", t)
        if m:
            params["sell_above"] = int(m.group(1))
    if template == "gap_rev":
        m = re.search(r"([\d.]+)\s*%", t)
        if m:
            params["min_gap_pct"] = float(m.group(1))
    if template == "vol_surge":
        m = re.search(r"(\d+(?:\.\d+)?)\s*[x×]", t)
        if m:
            params["mult"] = float(m.group(1))
    asset = next((a for a in re.findall(r"\b[A-Z]{2,5}\b", text) if a in assets), "SPY")
    warns = []
    if template == "mom_12_1" and "momentum" not in t:
        warns.append("No known signal keyword found — defaulted to 12-1 momentum. "
                     "Keywords: RSI, SMA, MACD, Bollinger, breakout, gap, volume, 52-week, volatility.")
    return {"template": template, "params": params, "asset": asset}, warns
