"""nqq.monitoring — drift (recent vs full) + per-regime breakdown + narrative.

Drift asks the honest question a live strategy must keep asking: is the recent window
still behaving like the backtest, or has the edge decayed? Per-regime splits performance
so a strategy that only works in one regime can't hide behind a blended average. The
narrative turns the numbers into plain English a human can act on.
"""
import math

import backtest as bt
import stats as st


def drift(family, params=None, recent=60):
    """Compare the most-recent window's Sharpe to the full-period Sharpe."""
    res = bt.run(family, params)
    if not res:
        return None
    rets = res["returns"]
    if len(rets) < recent + 60:
        return None
    full = st.sharpe(rets)
    recent_s = st.sharpe(rets[-recent:])
    gap = recent_s - full
    if recent_s < 0 and full > 0.2:
        verdict, action = "DECAYED", "edge has decayed — re-fit or retire"
    elif abs(gap) < 0.4:
        verdict, action = "IN LINE", "recent behaviour matches the backtest"
    elif gap < 0:
        verdict, action = "WEAKENING", "recent Sharpe below backtest — watch closely"
    else:
        verdict, action = "OUTPERFORMING", "recent Sharpe above backtest — don't over-size on it"
    return {"full_sharpe": round(full, 2), "recent_sharpe": round(recent_s, 2),
            "gap": round(gap, 2), "recent_bars": recent, "verdict": verdict,
            "action": action,
            # HONESTY: this compares the recent WINDOW of the same historical backtest to
            # the full period. It is NOT forward/live data — there is no live feed here.
            "basis": "recent in-sample window vs full backtest (no forward data)"}


def per_regime(family, params=None):
    """Split returns by SPY regime (risk-on/off) and by volatility (hi/lo)."""
    res = bt.run(family, params)
    if not res:
        return None
    rets, flags = res["returns"], res["regime_flags"]
    # volatility regime: rolling 20d realized vol above/below its median
    vols = []
    for i in range(len(rets)):
        w = rets[max(0, i - 19):i + 1]
        m = sum(w) / len(w)
        vols.append((sum((x - m) ** 2 for x in w) / len(w)) ** 0.5)
    med = sorted(vols)[len(vols) // 2]
    buckets = {"risk_on": [], "risk_off": [], "high_vol": [], "low_vol": []}
    for i, r in enumerate(rets):
        (buckets["risk_on"] if flags[i] else buckets["risk_off"]).append(r)
        (buckets["high_vol"] if vols[i] >= med else buckets["low_vol"]).append(r)
    out = {}
    for k, b in buckets.items():
        if len(b) >= 30:
            out[k] = {"n": len(b), "sharpe": round(st.sharpe(b), 2),
                      "deflated": round(st.deflated_sharpe(b, 1), 3)}
    worst = min((v["deflated"] for v in out.values()), default=0.0)
    return {"regimes": out, "worst_regime_deflated": round(worst, 3),
            "note": ("An edge you can trust survives its WORST regime. The worst "
                     f"deflated Sharpe here is {worst:.2f}.")}


def narrative(card, drift_d=None, regime_d=None):
    """Plain-English report a human can act on — no jargon without a plain gloss."""
    c = card["certainty"]
    lines = []
    lines.append(f"**{card['family']}** scored a raw Sharpe of {card['sharpe']:.2f}, but the "
                 f"honest number — the *deflated* Sharpe, which accounts for the "
                 f"{card['n_trials']} attempts made — is {card['deflated_sharpe']}. ")
    lo, hi = card["bootstrap"]["lo"], card["bootstrap"]["hi"]
    lines.append(f"Resampling the history puts the Sharpe anywhere from {lo} to {hi} "
                 f"(90% interval){' — which straddles zero, so we cannot rule out luck' if lo < 0 else ''}. ")
    lines.append(f"It holds capital up to about "
                 f"${card['capacity_aum']/1e6:.1f}M before its own market impact eats the "
                 f"edge, and drew down {card['max_dd_pct']}% at worst. ")
    if not card["real_pit"]:
        lines.append("Because the data isn't real point-in-time, certainty is hard-capped "
                     "at 50%. ")
    if drift_d:
        lines.append(f"Recent-window drift (in-sample, not forward): recent Sharpe "
                     f"{drift_d['recent_sharpe']} vs full-period {drift_d['full_sharpe']} — "
                     f"{drift_d['verdict'].lower()} ({drift_d['action']}). ")
    if regime_d:
        lines.append(f"Its worst regime holds a deflated Sharpe of "
                     f"{regime_d['worst_regime_deflated']}. ")
    lines.append(f"**Verdict: {c['verdict']} ({int(c['certainty']*100)}% certainty).** "
                 + (" ".join(c["reasons"]) if c["reasons"] else
                    "No fatal weakness vetoed the score."))
    return "".join(lines)
