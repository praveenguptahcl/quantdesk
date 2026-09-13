"""nqq.backtest — signal -> strategy engine with real costs, capacity, regime, OOS,
and an EdgeCertainty scorecard. Cross-sectional top-N over a universe, gated by the
SPY>SMA200 regime, executed with square-root impact + an execution delay (decide@t,
fill@t+1), then scored honestly (deflated Sharpe with trial-count multiplicity).
"""
import math

import costs as costmod
import data as datamod
import signals as sigmod
import stats as st

UNIVERSE = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]
REGIME_SYM = "SPY"
SMA_N = 200


def run(family, params, universe=None, top_n=3, vol_tgt=0.10, spread_bps=2.0):
    """Returns {returns, equity, dates, regime_returns, turnover_ann, edge_bps}."""
    universe = universe or UNIVERSE
    data = {s: datamod.load_ohlcv(s) for s in universe}
    data = {s: b for s, b in data.items() if b}
    spy = datamod.load_ohlcv(REGIME_SYM)
    if not spy or len(data) < 3:
        return None
    idx = {s: {b["d"]: i for i, b in enumerate(bars)} for s, bars in data.items()}
    sigs = {s: sigmod.series(family, bars, params) for s, bars in data.items()}
    sc = [b["c"] for b in spy]
    eq, equity, rets, dates = 1.0, [], [], []
    prev_w, run_sma = {}, 0.0
    regime_flags = []
    gross_traded = 0.0
    for i, b in enumerate(spy):
        run_sma += sc[i]
        if i >= SMA_N:
            run_sma -= sc[i - SMA_N]
        risk_on = i >= SMA_N - 1 and sc[i] > run_sma / SMA_N
        # today's causal scores
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
        # execution delay: weights decided at t apply to t->t+1 return
        if i + 1 < len(spy):
            nd = spy[i + 1]["d"]
            r = 0.0
            for s, x in w.items():
                j0, j1 = idx[s].get(b["d"]), idx[s].get(nd)
                if j0 is not None and j1 is not None:
                    r += x * (data[s][j1]["c"] / data[s][j0]["c"] - 1)
            turn = sum(abs(w.get(s, 0) - prev_w.get(s, 0)) for s in set(w) | set(prev_w))
            gross_traded += turn
            # `turn` is total (both-legs) traded notional, so charge a ONE-WAY cost per unit
            # traded: half-spread crossed + square-root impact. (Multiplying one-way turnover
            # by a round-trip cost double-charged the spread ~2×.)
            cost = turn * (spread_bps / 2 + costmod.impact_bps(0.002)) / 1e4
            r -= cost
            eq *= 1 + r
            rets.append(r); equity.append(eq); dates.append(nd)
            regime_flags.append(risk_on); prev_w = w
    if len(rets) < 30:
        return None
    n_years = len(rets) / 252.0
    turnover_ann = gross_traded / max(n_years, 1e-6)
    # edge_bps is the per-TRADE edge (bps): annual return / annual turnover. Numerator and
    # denominator must share a horizon. The prior code divided a DAILY mean return (mu·1e4)
    # by ANNUAL turnover — a ~252× dimensional mismatch that understated the edge so far it
    # pinned edge_bps at its old 1.0 floor for every realistic strategy, leaving capacity a
    # function of turnover alone (the edge magnitude — the whole point of a capacity model —
    # never entered). Annualize the return so the ratio is a coherent per-trade edge.
    # (It is still NOT the Sharpe: `_sharpe_pp·1e4` was unit-nonsense that blew edge up by
    # ~1/sd; a real per-trade edge here is single-to-low-double-digit bps, not 1e4-scale.)
    mu = sum(rets) / len(rets) if rets else 0.0
    ann_return_bps = mu * 252.0 * 1e4            # annualized mean return, in basis points
    return {"returns": rets, "equity": equity, "dates": dates,
            "regime_flags": regime_flags, "turnover_ann": turnover_ann,
            "edge_bps": abs(ann_return_bps) / max(turnover_ann, 1.0)}


def _worst_regime_dsr(rets, flags, n_trials):
    on = [r for r, f in zip(rets, flags) if f]
    off = [r for r, f in zip(rets, flags) if not f]
    dsrs = []
    for bucket in (on, off):
        if len(bucket) >= 30:
            dsrs.append(st.deflated_sharpe(bucket, n_trials))
    return min(dsrs) if dsrs else st.deflated_sharpe(rets, n_trials)


def scorecard(store, family, params, sym_for_pit="SPY", operator="solo",
              universe=None, record_trial=True):
    """Full honest verdict for a strategy: run -> DSR (multiplicity) -> bootstrap ->
    capacity -> worst-regime -> EdgeCertainty. Records a trial (multiplicity substrate)."""
    # enforce point-in-time: the signal must not peek at future bars (raises if it does)
    try:
        sigmod.assert_causal(family, params, datamod.load_ohlcv(sym_for_pit))
    except datamod.LookaheadError as e:
        return None, f"lookahead guard tripped: {e}"
    res = run(family, params, universe)
    if not res:
        return None, "backtest could not run"
    rets, flags = res["returns"], res["regime_flags"]
    n_trials = 1
    sr_var = None
    if store is not None:
        n_trials = store.trial_count(operator=operator) + 1
        if record_trial:
            store.record_trial(operator, family, sym_for_pit, st.sharpe(rets),
                               {"n": len(rets)})
        # deflate against the operator's EMPIRICAL cross-trial Sharpe dispersion when we
        # have enough recorded trials (Bailey–López de Prado); else fall back to sampling SE
        srs = store.trial_sharpes(operator=operator)
        if len(srs) >= 5:
            m = sum(srs) / len(srs)
            sr_var = sum((s - m) ** 2 for s in srs) / (len(srs) - 1)
    dsr = st.deflated_sharpe(rets, n_trials, sr_var=sr_var)
    boot = st.block_bootstrap_sharpe(rets)
    wr_dsr = _worst_regime_dsr(rets, flags, n_trials)
    cap = costmod.capacity(res["edge_bps"], res["turnover_ann"])
    real_pit = datamod.is_real(sym_for_pit) and all(datamod.is_real(s)
                                                    for s in (universe or UNIVERSE))
    # OOS consistency from a signal-level eval on the pit symbol
    ev, _ = sigmod.evaluate(family, params, sym_for_pit)
    oos = ev["oos_consistency"] if ev else 0.5
    decay_hazard = max(0.0, 1 - sigmod.DECAY_HL.get(family, 5) / 20.0)
    cert = st.edge_certainty({
        "worst_regime_dsr": wr_dsr, "bootstrap_p_pos": boot["p_positive"],
        "dsr": dsr, "fdr_survived": (ev["ic_pvalue"][1] < 0.10) if ev else False,
        "oos_consistency": oos, "forward_days": 0, "forward_sharpe": 0.0,
        "is_real_pit": real_pit, "capacity_aum": cap, "decay_hazard": decay_hazard,
    })
    return {
        "family": family, "params": sigmod._merge(family, params),
        "sharpe": st.sharpe(rets), "deflated_sharpe": round(dsr, 3),
        "n_trials": n_trials, "bootstrap": boot, "worst_regime_dsr": round(wr_dsr, 3),
        "capacity_aum": cap, "turnover_ann": round(res["turnover_ann"], 2),
        "max_dd_pct": _maxdd(res["equity"]), "n_obs": len(rets),
        "real_pit": real_pit, "certainty": cert, "oos_consistency": round(oos, 3),
        "equity": [round(e, 4) for e in res["equity"][::max(1, len(res["equity"]) // 260)]],
    }, None


def walk_forward(family, params, universe=None, segments=5):
    """Rolling out-of-sample segments — reports each segment's Sharpe so you can see
    whether the edge is stable or was one lucky window."""
    res = run(family, params, universe)
    if not res:
        return None
    rets = res["returns"]
    seg = len(rets) // segments
    out = []
    for k in range(segments):
        chunk = rets[k * seg:(k + 1) * seg] if k < segments - 1 else rets[k * seg:]
        if len(chunk) >= 20:
            out.append({"segment": k + 1, "n": len(chunk),
                        "sharpe": st.sharpe(chunk),
                        "deflated": round(st.deflated_sharpe(chunk, 1), 3)})
    positive = sum(1 for s in out if s["sharpe"] > 0)
    return {"segments": out, "stable": positive >= max(1, len(out) - 1),
            "positive_segments": positive, "total_segments": len(out)}


def permutation_test(family, params, universe=None, n_perm=200):
    """Sign-flip test of a NON-ZERO mean Sharpe. Each replicate flips the sign of each
    daily return independently (a symmetric null with mean 0) and recomputes the Sharpe;
    the p-value is the fraction that beat the real Sharpe. NOTE: this tests whether the
    strategy's mean return is reliably positive — it does NOT test whether the return
    *ordering/timing* carried the edge (that would require shuffling the signal↔return
    pairing). Labelled honestly so it isn't mistaken for a sequence-permutation test."""
    import random
    res = run(family, params, universe)
    if not res:
        return None
    rets = res["returns"]
    real = st.sharpe(rets)
    rng = random.Random(11)
    beat = 0
    for _ in range(n_perm):
        perm = [r * (1 if rng.random() < 0.5 else -1) for r in rets]
        if st.sharpe(perm) >= real:
            beat += 1
    return {"real_sharpe": round(real, 2), "n_perm": n_perm,
            "p_value": round((beat + 1) / (n_perm + 1), 4),
            "test": "sign-flip (H0: mean Sharpe = 0)",
            "note": "p = fraction of sign-flipped histories that beat the real Sharpe; "
                    "tests a non-zero mean, not return ordering"}


def _maxdd(equity):
    if not equity:
        return 0.0
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    return round(mdd * 100, 2)
