"""nqq.signals — causal signal families with decay half-lives + honest IC.

Every family is point-in-time by construction (value at bar t uses only bars <= t).
Evaluation splits in-sample / out-of-sample, reports IC at multiple horizons with a
p-value (feeds FDR control), hit rate, turnover, and a decay half-life estimate.
"""
import math

import data as datamod
import stats as st

# decay half-life (days) per family — honesty over precision
DECAY_HL = {"momentum": 15, "mean_reversion": 3, "rsi": 3, "bollinger": 4,
            "breakout": 12, "volume_thrust": 2}

FAMILIES = {
    "momentum": {"label": "12-1 Momentum", "params": {"look": 252, "skip": 21}},
    "mean_reversion": {"label": "Mean Reversion (z)", "params": {"n": 20, "cap_z": 2.0}},
    "rsi": {"label": "RSI Reversal", "params": {"n": 14, "buy_below": 30, "sell_above": 70}},
    "bollinger": {"label": "Bollinger Z", "params": {"n": 20, "cap_z": 2.0}},
    "breakout": {"label": "Donchian Breakout", "params": {"n": 55}},
    "volume_thrust": {"label": "Volume Thrust", "params": {"n": 20, "mult": 2.0}},
}


def _merge(family, overrides):
    p = dict(FAMILIES[family]["params"])
    for k, v in (overrides or {}).items():
        if k in p:
            try:
                p[k] = type(p[k])(v)
            except (TypeError, ValueError):
                pass
    return p


def series(family, bars, params=None):
    """Causal signal value per bar (None during warmup). value>0 = long tilt."""
    P = _merge(family, params)
    c = [b["c"] for b in bars]
    n = len(bars)
    out = [None] * n
    if family == "momentum":
        lk, sk = int(P["look"]), int(P["skip"])
        for i in range(lk, n):
            out[i] = c[i - sk] / c[i - lk] - 1
    elif family == "mean_reversion":
        w = int(P["n"])
        for i in range(w - 1, n):
            win = c[i - w + 1:i + 1]
            m = sum(win) / w
            sd = (sum((x - m) ** 2 for x in win) / w) ** 0.5 or 1e-9
            out[i] = -(c[i] - m) / sd          # continuous fade of the trailing z-score
    elif family == "bollinger":
        # DISTINCT from mean_reversion: a Bollinger band-touch signal — silent INSIDE the
        # ±cap_z·σ bands, fading only when price breaches a band (this is what cap_z is for;
        # it was previously dead code and this branch was byte-identical to mean_reversion).
        w = int(P["n"]); k = float(P["cap_z"]) or 2.0
        for i in range(w - 1, n):
            win = c[i - w + 1:i + 1]
            m = sum(win) / w
            sd = (sum((x - m) ** 2 for x in win) / w) ** 0.5 or 1e-9
            z = (c[i] - m) / sd
            out[i] = -(z / k) if abs(z) >= k else 0.0   # fade only band breaches
    elif family == "rsi":
        # standard Wilder RSI: seed the average gain/loss with the SMA of the first w
        # changes, THEN apply Wilder's recursive smoothing (was seeding off a single bar).
        w = int(P["n"]); g = l = None
        gs, ls = 0.0, 0.0
        # buy_below / sell_above are the REVERSAL thresholds. They are advertised in
        # FAMILIES and parsed from plain English by ai_builder ("buy below 30 / sell above
        # 70"), so they MUST drive the signal — previously `series` returned a threshold-
        # agnostic (50 - rsi) fade and IGNORED both, so a user (or the AI parser) setting
        # "below 20 / above 80" got a byte-identical signal (advertised-but-dead knobs, the
        # same dishonesty class as the old bollinger cap_z). Now the signal is SILENT in
        # the neutral band and fades only threshold breaches: oversold (rsi<=buy_below) →
        # bullish (+), overbought (rsi>=sell_above) → bearish (−), normalized to ~[-1,1].
        lo = float(P.get("buy_below", 30)); hi = float(P.get("sell_above", 70))
        for i in range(1, n):
            ch = c[i] - c[i - 1]
            up, dn = max(ch, 0), max(-ch, 0)
            if i <= w:                          # accumulate the seed window
                gs += up; ls += dn
                if i == w:
                    g, l = gs / w, ls / w       # SMA seed at bar w
            else:
                g = (g * (w - 1) + up) / w       # Wilder smoothing thereafter
                l = (l * (w - 1) + dn) / w
            if i >= w:
                rsi = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
                if rsi <= lo:
                    out[i] = (lo - rsi) / max(lo, 1e-9)          # 0..1 bullish (oversold)
                elif rsi >= hi:
                    out[i] = (hi - rsi) / max(100 - hi, 1e-9)    # 0..-1 bearish (overbought)
                else:
                    out[i] = 0.0                                 # neutral band → flat
    elif family == "breakout":
        w = int(P["n"])
        for i in range(w, n):
            hh = max(c[i - w:i]); ll = min(c[i - w:i])
            out[i] = (c[i] - ll) / (hh - ll) * 2 - 1 if hh > ll else 0.0
    elif family == "volume_thrust":
        w = int(P["n"]); v = [b["v"] for b in bars]
        for i in range(w, n):
            av = sum(v[i - w:i]) / w or 1e-9
            day = 1.0 if c[i] >= bars[i]["o"] else -1.0
            out[i] = (v[i] / av - 1) * day
    return out


def assert_causal(family, params, bars, samples=6):
    """REAL lookahead enforcement: recompute the signal on truncated history bars[:i+1]
    and verify it equals the value computed on the full series at i. If a family ever
    peeks at future bars, the two differ and we raise — turning silent optimism into a
    loud error (the guard the Learn screen promises, now enforced on the hot path)."""
    import data as _d
    full = series(family, bars, params)
    n = len(bars)
    idxs = [int(n * f) for f in (0.55, 0.65, 0.75, 0.85, 0.92, 0.98)][:samples]
    for i in idxs:
        if i < 30 or i >= n or full[i] is None:
            continue
        trunc = series(family, bars[:i + 1], params)
        if trunc[i] is not None and abs((trunc[i] or 0) - (full[i] or 0)) > 1e-9:
            raise _d.LookaheadError(
                f"lookahead in {family}: value at bar {i} changes when future bars are "
                f"hidden ({trunc[i]} vs {full[i]}) — the signal is not point-in-time")
    return True


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


def _lag1_autocorr(a):
    """Lag-1 autocorrelation of a sequence (used to deflate the effective sample size)."""
    m = [x for x in a if x is not None]
    if len(m) < 3:
        return 0.0
    mu = sum(m) / len(m)
    num = sum((m[i] - mu) * (m[i - 1] - mu) for i in range(1, len(m)))
    den = sum((x - mu) ** 2 for x in m) or 1e-9
    return max(-0.99, min(0.99, num / den))


def _effective_n(n, horizon, signal_vals):
    """Overlapping forward returns (horizon h) and a persistent signal both make the raw
    daily count N overstate the number of INDEPENDENT observations. Deflate two ways and
    take the more conservative: (1) overlap → N/h; (2) AR(1) variance inflation →
    N·(1−ρ)/(1+ρ) using the signal's lag-1 autocorrelation ρ. This keeps the IC t-test
    (and the FDR 'survives' flags and 'rated' council voices it feeds) from overstating
    significance on autocorrelated data."""
    rho = _lag1_autocorr(signal_vals)
    n_ar = n * (1 - rho) / (1 + rho) if rho > 0 else n
    n_overlap = n / max(1, horizon)
    return max(5.0, min(float(n), n_ar, n_overlap))


def _ic_pvalue(ic, n_eff):
    if ic is None or n_eff < 5 or abs(ic) >= 1:
        return 1.0
    t = ic * math.sqrt((n_eff - 2) / (1 - ic * ic))
    return 2 * (1 - st.norm_cdf(abs(t)))     # normal approx to t, on the EFFECTIVE N


def evaluate(family, params, sym, oos_frac=0.3):
    """Honest IC diagnostics with an IS/OOS split. Returns dict or (None,err)."""
    bars = datamod.load_ohlcv(sym)
    if not bars or len(bars) < 200:
        return None, f"insufficient bars for {sym}"
    vals = series(family, bars, params)
    c = [b["c"] for b in bars]
    n = len(bars)
    fwd = {h: [c[i + h] / c[i] - 1 if i + h < n else None for i in range(n)]
           for h in (1, 5, 10, 21)}
    ic = {}
    pvals = {}
    for h in (1, 5, 10, 21):
        pairs = [(vals[i], fwd[h][i]) for i in range(n)
                 if vals[i] is not None and fwd[h][i] is not None]
        r = _rankcorr([p[0] for p in pairs], [p[1] for p in pairs])
        ic[h] = round(r or 0.0, 3)
        n_eff = _effective_n(len(pairs), h, [p[0] for p in pairs])
        pvals[h] = round(_ic_pvalue(r, n_eff), 4)
    # OOS split on horizon-1
    split = int(n * (1 - oos_frac))
    def _ic_range(a, b):
        pairs = [(vals[i], fwd[1][i]) for i in range(a, b)
                 if vals[i] is not None and fwd[1][i] is not None]
        return _rankcorr([p[0] for p in pairs], [p[1] for p in pairs]) or 0.0
    ic_is, ic_oos = _ic_range(0, split), _ic_range(split, n)
    # consistency = sign agreement (both halves point the same way) scaled by how much
    # of the in-sample IC magnitude survives out of sample. Honest but not annihilating.
    if abs(ic_is) < 1e-6:
        oos_consistency = 0.5
    elif (ic_is > 0) == (ic_oos > 0):
        oos_consistency = min(1.0, 0.5 + 0.5 * min(1.0, abs(ic_oos) / abs(ic_is)))
    else:
        oos_consistency = max(0.0, 0.35 - 0.35 * min(1.0, abs(ic_oos) / abs(ic_is)))
    # positions & turnover / hit
    pos = [1 if (v is not None and v > 0) else (-1 if v is not None else 0) for v in vals]
    turns = sum(abs(pos[i] - pos[i - 1]) for i in range(1, n)) / n
    act = [(pos[i], fwd[1][i]) for i in range(n) if pos[i] != 0 and fwd[1][i] is not None]
    hit = round(sum(1 for p, r in act if p * r > 0) / len(act), 3) if act else 0.0
    return {
        "family": family, "sym": sym, "params": _merge(family, params),
        "real_pit": datamod.is_real(sym), "n": n,
        "ic": ic, "ic_pvalue": pvals, "ic_is": round(ic_is, 3), "ic_oos": round(ic_oos, 3),
        "oos_consistency": round(oos_consistency, 3), "hit_rate": hit,
        "turnover": round(turns, 3), "decay_half_life_d": DECAY_HL.get(family, 5),
    }, None
