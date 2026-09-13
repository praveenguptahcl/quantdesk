"""nqq.portfolio — correlation-aware combination + portfolio-level capacity.

Combining strategies only helps if their returns aren't the same trade in a costume.
We compute the pairwise correlation, build an inverse-variance / low-correlation weighting,
and report the **diversification benefit** (portfolio vol vs the weighted average of the
legs) plus a **portfolio capacity** that respects overlap. Honest: a portfolio of highly
correlated legs shows near-zero diversification and its capacity barely grows.
"""
import math

import backtest as bt
import costs as costmod
import stats as st


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 20:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a) or 1e-9
    vb = sum((x - mb) ** 2 for x in b) or 1e-9
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def build(legs):
    """legs: list of {family, params}. Returns weights + diversification + capacity."""
    runs = []
    for lg in legs:
        r = bt.run(lg["family"], lg.get("params"))
        if r:
            runs.append((lg, r["returns"], r))
    if len(runs) < 1:
        return None, "no runnable legs"
    n = min(len(r[1]) for r in runs)
    rets = [r[1][-n:] for r in runs]
    # inverse-variance weights, shrunk by average correlation to the rest
    vols = [max(1e-9, (sum((x - sum(s) / n) ** 2 for x in s) / n) ** 0.5) for s in rets]
    avg_corr = []
    for i in range(len(rets)):
        cs = [abs(_corr(rets[i], rets[j])) for j in range(len(rets)) if j != i]
        avg_corr.append(sum(cs) / len(cs) if cs else 0.0)
    raw = [(1 / vols[i]) * (1 - 0.5 * avg_corr[i]) for i in range(len(rets))]
    tot = sum(raw) or 1
    w = [x / tot for x in raw]
    # portfolio returns
    port = [sum(w[i] * rets[i][t] for i in range(len(rets))) for t in range(n)]
    port_vol = (sum((x - sum(port) / n) ** 2 for x in port) / n) ** 0.5
    wavg_vol = sum(w[i] * vols[i] for i in range(len(rets)))
    diversification = round(max(0.0, 1 - port_vol / (wavg_vol + 1e-9)), 3)
    # capacity: weighted sum, but crowding grows with average pairwise correlation
    mean_corr = sum(avg_corr) / len(avg_corr)
    caps = []
    for i, (lg, s, run) in enumerate(runs):
        caps.append(costmod.capacity(run["edge_bps"], run["turnover_ann"],
                                     crowding=1 + mean_corr))
    port_cap = round(sum(caps), 0)
    return {
        "legs": [{"family": runs[i][0]["family"], "params": runs[i][0].get("params", {}),
                  "weight": round(w[i], 3), "avg_corr": round(avg_corr[i], 3),
                  "capacity_aum": caps[i]} for i in range(len(runs))],
        "portfolio_sharpe": st.sharpe(port), "portfolio_deflated": st.deflated_sharpe(port, 1),
        "portfolio_ci": st.block_bootstrap_sharpe(port),
        "diversification": diversification, "mean_correlation": round(mean_corr, 3),
        "portfolio_capacity_aum": port_cap,
        "equity": _equity(port),
    }, None


def _equity(rets):
    eq, out = 1.0, []
    for r in rets:
        eq *= 1 + r; out.append(round(eq, 4))
    step = max(1, len(out) // 260)
    return out[::step]
