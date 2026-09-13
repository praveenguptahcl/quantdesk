"""nqq.compare — put two strategies side by side + detect conflicts.

Two strong strategies can be a bad pair: highly correlated (you're doubling one bet),
or fighting for the same capacity, or taking opposite sides of the same trade. Compare
shows the honest scorecards next to each other and flags these conflicts.
"""
import backtest as bt
import portfolio as portfoliomod


def _flag_conflicts(corr, small_cap):
    """Build the conflict list from a correlation and the smaller leg's capacity.

    Correlation flags are directional and mutually exclusive: only a strongly
    POSITIVE correlation is "nearly the same bet"; a strongly NEGATIVE one is the
    opposite (OPPOSED). The prior code gated HIGH CORRELATION on abs(corr) > 0.7,
    so a corr of e.g. -0.85 wrongly printed "nearly the same bet" *and* "OPPOSED"
    at once — a flag whose text contradicted its own number.
    """
    conflicts = []
    if corr > 0.7:
        conflicts.append(f"HIGH CORRELATION ({corr:.2f}) — these are nearly the same bet; "
                         "combining them adds risk, not diversification.")
    if corr < -0.5:
        conflicts.append(f"OPPOSED ({corr:.2f}) — they take opposite sides; held together "
                         "they mostly cancel (and pay double costs).")
    if small_cap < 100_000:
        conflicts.append(f"TIGHT CAPACITY — the smaller strategy holds only "
                         f"${small_cap:,.0f}; scaling the pair is limited by it.")
    if not conflicts:
        conflicts.append(f"No conflict flagged (correlation {corr:.2f}) — a plausible pair "
                         "for a portfolio.")
    return conflicts


def compare(store, family_a, family_b, params_a=None, params_b=None):
    ca, ea = bt.scorecard(store, family_a, params_a or {}, "SPY", record_trial=False)
    cb, eb = bt.scorecard(store, family_b, params_b or {}, "SPY", record_trial=False)
    if ea or eb:
        return None, ea or eb
    ra = bt.run(family_a, params_a); rb = bt.run(family_b, params_b)
    n = min(len(ra["returns"]), len(rb["returns"]))
    corr = portfoliomod._corr(ra["returns"][-n:], rb["returns"][-n:])
    small = min(ca["capacity_aum"], cb["capacity_aum"])
    conflicts = _flag_conflicts(corr, small)
    return {
        "a": {"family": family_a, "deflated_sharpe": ca["deflated_sharpe"],
              "certainty": ca["certainty"]["certainty"], "verdict": ca["certainty"]["verdict"],
              "capacity_aum": ca["capacity_aum"], "max_dd_pct": ca["max_dd_pct"],
              "ci": ca["bootstrap"]},
        "b": {"family": family_b, "deflated_sharpe": cb["deflated_sharpe"],
              "certainty": cb["certainty"]["certainty"], "verdict": cb["certainty"]["verdict"],
              "capacity_aum": cb["capacity_aum"], "max_dd_pct": cb["max_dd_pct"],
              "ci": cb["bootstrap"]},
        "correlation": round(corr, 3), "conflicts": conflicts,
    }, None
