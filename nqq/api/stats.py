"""nqq.stats — the honesty core (pure Python, no numpy).

Implements the statistics that make a competition trustworthy:
  * Probabilistic & Deflated Sharpe (Bailey & Lopez de Prado) — a probability the
    true Sharpe exceeds a multiplicity-adjusted benchmark, given sample size, skew,
    kurtosis, and the NUMBER OF TRIALS. Deflated Sharpe <= raw Sharpe, always.
  * Block bootstrap confidence interval for the Sharpe (robustness to resampling).
  * Benjamini-Hochberg FDR — controls false discoveries across many tested signals.
  * EdgeCertainty — an earned probability fused from six independent axes, with HARD
    ceilings (synthetic data <= 0.50; no forward track record <= 0.70) that the soft
    axes cannot buy back.
  * Composite verdict — veto-gated: any single fatal axis caps the verdict at
    "Inconclusive". A strong average must never hide a fatal weakness.

Invariants (tested): DSR <= PSR(0) <= 1; DSR falls as trials rise; certainty caps hold.
"""
import math

GAMMA = 0.5772156649015329   # Euler-Mascheroni
_E = math.e


# ---------------- normal CDF / inverse ----------------
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Inverse standard-normal CDF (Acklam's rational approximation). Arguments are
    clamped just inside (0,1) so a boundary p (e.g. 1 - 1/(N·e) rounding to 1.0 at huge N)
    returns a large-but-finite, MONOTONE quantile instead of a misleading sentinel."""
    p = min(1 - 1e-15, max(1e-15, p))
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


# ---------------- moments ----------------
def _moments(returns):
    n = len(returns)
    if n < 2:
        return 0.0, 0.0, 0.0, 3.0, n
    mu = sum(returns) / n
    var = sum((r - mu) ** 2 for r in returns) / n
    sd = math.sqrt(var)
    # degenerate (constant / numerically-zero-variance) series: variance is zero to float
    # precision, so the Sharpe is undefined. Return honest sd=0 (callers guard sd<=0 →
    # Sharpe 0) instead of a 1e-9 floor that would leak a meaningless huge mu/sd Sharpe.
    # Threshold is relative to the return scale so real (sd~1e-2) series are unaffected.
    if sd <= 1e-12 * (abs(mu) + 1.0):
        return mu, 0.0, 0.0, 3.0, n
    m3 = sum((r - mu) ** 3 for r in returns) / n
    m4 = sum((r - mu) ** 4 for r in returns) / n
    skew = m3 / (sd ** 3)
    kurt = m4 / (sd ** 4)   # Pearson (normal = 3)
    return mu, sd, skew, kurt, n


def sharpe(returns, periods=252):
    mu, sd, _, _, n = _moments(returns)
    if n < 2 or sd <= 0:
        return 0.0
    return (mu / sd) * math.sqrt(periods)


def _sharpe_pp(returns):
    """Per-period (non-annualized) Sharpe."""
    mu, sd, _, _, n = _moments(returns)
    return (mu / sd) if (n >= 2 and sd > 0) else 0.0


def parity_sharpe(returns, periods=252):
    """An INDEPENDENT recomputation of the SAME annualized (arithmetic) Sharpe that
    `sharpe()` returns — but via Welford's one-pass streaming moments instead of the
    two-pass `_moments`. Because it estimates the identical quantity through a different
    code path, correct implementations agree to ~1e-9; a scaling/sign/accumulation bug in
    either path makes them diverge, so the parity gate can genuinely FAIL. This is a
    reconciliation check (same estimand, two engines), NOT a second estimator."""
    n = 0
    mean = 0.0
    m2 = 0.0
    for r in returns:            # Welford online mean + sum-of-squared-deviations
        n += 1
        delta = r - mean
        mean += delta / n
        m2 += delta * (r - mean)
    if n < 2:
        return 0.0
    sd = math.sqrt(m2 / n)           # population variance — matches _moments (÷n)
    # degenerate zero-variance series: the Sharpe is undefined. Return an honest 0.0 to
    # match _moments()/sharpe() (same fix as the two-pass path) instead of leaking a huge
    # value via a 1e-9 floor. Otherwise this reconciliation engine DISAGREES with engine A
    # on a constant series (0.0 vs ~1e16), and the promotion parity gate would flag a
    # "numerical bug" that is actually right here in the parity engine. Same relative
    # threshold as _moments so real (sd~1e-2) series are untouched.
    if sd <= 1e-12 * (abs(mean) + 1.0):
        return 0.0
    return (mean / sd) * math.sqrt(periods)


# ---------------- Probabilistic & Deflated Sharpe ----------------
def probabilistic_sharpe(returns, sr_benchmark_pp=0.0):
    """PSR: P(true per-period Sharpe > benchmark). Bailey & Lopez de Prado."""
    _, _, skew, kurt, n = _moments(returns)
    if n < 3:
        return 0.5
    sr = _sharpe_pp(returns)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr * sr))
    z = (sr - sr_benchmark_pp) * math.sqrt(n - 1) / denom
    return norm_cdf(z)


def _se_sharpe_pp(returns):
    _, _, skew, kurt, n = _moments(returns)
    if n < 3:
        return 1.0
    sr = _sharpe_pp(returns)
    return math.sqrt(max(1e-12, (1 - skew * sr + (kurt - 1) / 4.0 * sr * sr) / (n - 1)))


def expected_max_sharpe_pp(returns, n_trials, sr_var=None):
    """Benchmark Sharpe an operator would reach by luck across n_trials attempts.
    Bailey–López de Prado: SR*₀ = √Var({SRₙ})·E[max Z]. The dispersion of the trial
    Sharpes, √Var({SRₙ}), is best estimated from the ACTUAL recorded trial Sharpes
    (pass sr_var). When that isn't available we fall back to THIS strategy's sampling SE
    — a conservative-leaning approximation that assumes trials are dispersed only at the
    sampling scale (real trial dispersion is usually wider, so the fallback can be mildly
    generous; the empirical sr_var path fixes that)."""
    if n_trials <= 1:
        return 0.0
    disp = math.sqrt(sr_var) if (sr_var is not None and sr_var > 0) else _se_sharpe_pp(returns)
    t = float(n_trials)
    return disp * ((1 - GAMMA) * norm_ppf(1 - 1.0 / t) + GAMMA * norm_ppf(1 - 1.0 / (t * _E)))


def deflated_sharpe(returns, n_trials=1, sr_var=None):
    """DSR: PSR against the multiplicity-adjusted benchmark. In [0,1].
    Falls as n_trials rises — this is the multiple-testing / variance-farming defence.
    Pass sr_var (variance of the operator's recorded trial Sharpes) to deflate against
    the real cross-trial dispersion rather than a single strategy's sampling SE."""
    sr0 = expected_max_sharpe_pp(returns, max(1, n_trials), sr_var=sr_var)
    return probabilistic_sharpe(returns, sr0)


# ---------------- block bootstrap Sharpe CI ----------------
def block_bootstrap_sharpe(returns, n_boot=500, block=10, periods=252, seed=7):
    """Stationary-ish block bootstrap of the annualized Sharpe.
    Returns {lo, hi, median, p_positive} (90% CI)."""
    import random
    n = len(returns)
    if n < 20:
        s = sharpe(returns, periods)
        return {"lo": s, "hi": s, "median": s, "p_positive": 1.0 if s > 0 else 0.0}
    rng = random.Random(seed)
    stats = []
    nblocks = -(-n // block)                 # ceil, so the resample is at least n long
    for _ in range(n_boot):
        sample = []
        for _b in range(nblocks):
            start = rng.randint(0, n - block)
            sample.extend(returns[start:start + block])
        stats.append(sharpe(sample[:n], periods))   # truncate to n: same length as original
    stats.sort()
    m = len(stats)
    lo = stats[int(0.05 * (m - 1))]          # symmetric 5th/95th percentiles
    hi = stats[int(round(0.95 * (m - 1)))]
    med = stats[m // 2]
    p_pos = sum(1 for s in stats if s > 0) / len(stats)
    return {"lo": round(lo, 2), "hi": round(hi, 2), "median": round(med, 2),
            "p_positive": round(p_pos, 3)}


# ---------------- FDR (Benjamini-Hochberg) ----------------
def benjamini_hochberg(pvalues, alpha=0.10):
    """Returns (survivors_mask, qvalues). Controls the false discovery rate at alpha."""
    m = len(pvalues)
    if m == 0:
        return [], []
    order = sorted(range(m), key=lambda i: pvalues[i])
    q = [0.0] * m
    thresh = [0.0] * m
    survive = [False] * m
    # BH q-values (monotone from the top)
    prev_q = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        prev_q = min(prev_q, pvalues[i] * m / (rank + 1))
        q[i] = min(1.0, prev_q)
    # largest k with p_(k) <= k/m*alpha
    kmax = -1
    for rank in range(m):
        i = order[rank]
        if pvalues[i] <= (rank + 1) / m * alpha:
            kmax = rank
    for rank in range(m):
        i = order[rank]
        survive[i] = rank <= kmax
        thresh[i] = (rank + 1) / m * alpha
    return survive, [round(x, 4) for x in q]


# ---------------- EdgeCertainty ----------------
def _clip01(x):
    return max(0.0, min(1.0, x))


def edge_certainty(inp):
    """Fuse six independent axes into an earned probability, with HARD ceilings.
    inp keys (all optional, sensible defaults):
      worst_regime_dsr : DSR in the worst regime bucket [0,1]
      bootstrap_p_pos  : bootstrap P(Sharpe>0) [0,1]
      dsr              : overall deflated Sharpe [0,1] (the luck-gap axis)
      fdr_survived     : bool — survived multiplicity control among peers
      oos_consistency  : IS/OOS agreement [0,1]
      forward_days     : live-paper track length (days)
      forward_sharpe   : live-paper annualized Sharpe
      is_real_pit      : bool — data is real point-in-time (else synthetic/sample)
    Returns {certainty, axes, caps, verdict, reasons}.
    """
    wr = _clip01(inp.get("worst_regime_dsr", inp.get("dsr", 0.5)))
    bp = _clip01(inp.get("bootstrap_p_pos", 0.5))
    dsr = _clip01(inp.get("dsr", 0.5))
    fdr = 1.0 if inp.get("fdr_survived", False) else 0.35
    oos = _clip01(inp.get("oos_consistency", 0.5))
    fdays = float(inp.get("forward_days", 0))
    fsharpe = float(inp.get("forward_sharpe", 0.0))
    live = _clip01((min(fdays, 60) / 60.0) * (0.5 + 0.5 * _clip01(fsharpe)))
    axes = {"worst_regime_dsr": round(wr, 3), "bootstrap_p_pos": round(bp, 3),
            "luck_gap_dsr": round(dsr, 3), "fdr_survival": round(fdr, 3),
            "oos_consistency": round(oos, 3), "live_track": round(live, 3)}
    # geometric-ish fusion — a low axis drags the whole thing (no averaging away
    # weakness) but each axis is floored so one zero doesn't annihilate the result.
    fl = lambda a: max(a, 0.05)
    fused = (fl(wr) ** 0.28) * (fl(bp) ** 0.18) * (fl(dsr) ** 0.20) * (fl(fdr) ** 0.12) * \
            (fl(oos) ** 0.12) * (max(live, 0.15) ** 0.10)
    caps, reasons = [], []
    if not inp.get("is_real_pit", False):
        fused = min(fused, 0.50)
        caps.append("synthetic/sample data → certainty capped at 0.50")
    if fdays < 10:
        fused = min(fused, 0.70)
        caps.append("no sufficient forward track record → capped at 0.70")
    certainty = round(_clip01(fused), 3)
    verdict, vreasons = composite_verdict(inp, certainty)
    return {"certainty": certainty, "axes": axes, "caps": caps,
            "verdict": verdict, "reasons": caps + vreasons}


def composite_verdict(inp, certainty):
    """Veto-gated verdict. Any fatal axis caps the label at 'Inconclusive'."""
    reasons = []
    wr = inp.get("worst_regime_dsr", inp.get("dsr", 0.5))
    cap_aum = inp.get("capacity_aum", 1e9)
    decay = inp.get("decay_hazard", 0.0)   # 0 good, 1 bad
    veto = False
    if wr < 0.55:
        veto = True; reasons.append("worst-regime deflated Sharpe below floor (fragile edge)")
    if cap_aum < 50_000:
        veto = True; reasons.append(f"capacity ceiling ~${cap_aum:,.0f} — edge only exists at tiny size")
    if decay > 0.7:
        veto = True; reasons.append("signal decay hazard too high (edge dies fast)")
    if veto:
        return "Inconclusive", reasons
    if certainty >= 0.80:
        return "Strong edge", reasons
    if certainty >= 0.60:
        return "Probable edge", reasons
    if certainty >= 0.40:
        return "Weak-positive", reasons
    return "No edge detected", reasons
