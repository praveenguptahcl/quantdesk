"""nqq.discovery — systematic alpha search with HONEST multiplicity control.

Search a grid of family × params × symbol, score each candidate's IC, then apply
Benjamini-Hochberg FDR **across the entire search**. This is the honest version of
alpha mining: the more you look, the higher the bar — most "discoveries" are correctly
rejected as noise. Every candidate carries its p-value, q-value, and survive flag, and
the whole search is logged to the trial ledger (it counts toward your multiplicity).
"""
import data as datamod
import signals as sigmod
import stats as st

# a modest, honest search grid (kept small so the demo runs fast)
GRID = {
    "momentum": [{"look": 252, "skip": 21}, {"look": 126, "skip": 10}, {"look": 63, "skip": 5}],
    "mean_reversion": [{"n": 20}, {"n": 10}, {"n": 40}],
    "rsi": [{"n": 14}, {"n": 7}, {"n": 21}],
    "breakout": [{"n": 55}, {"n": 20}, {"n": 100}],
}


def search(store, symbols=None, operator="solo", alpha=0.10):
    symbols = symbols or ["SPY", "XLK", "XLF", "XLE", "XLV"]
    cands, pvals = [], []
    for sym in symbols:
        if not datamod.load_ohlcv(sym):
            continue
        for fam, plist in GRID.items():
            for params in plist:
                ev, err = sigmod.evaluate(fam, params, sym)
                if err:
                    continue
                p = ev["ic_pvalue"][1]
                cands.append({"family": fam, "params": params, "sym": sym,
                              "ic1": ev["ic"][1], "ic5": ev["ic"][5],
                              "hit": ev["hit_rate"], "oos": ev["oos_consistency"],
                              "pvalue": p, "real_pit": ev["real_pit"]})
                pvals.append(p)
    surv, q = st.benjamini_hochberg(pvals, alpha)
    for c, s2, qq in zip(cands, surv, q):
        c["fdr_survived"] = bool(s2); c["qvalue"] = qq
    cands.sort(key=lambda c: c["pvalue"])
    if store is not None:
        # the whole search counts toward the operator's multiplicity
        for c in cands:
            store.record_trial(operator, c["family"], c["sym"], 0.0,
                               {"discovery": True})
        store.stamp("discovery_run", operator,
                    {"tested": len(cands), "survived": sum(surv), "alpha": alpha})
    return {"n_tested": len(cands), "n_survived": sum(surv), "alpha": alpha,
            "candidates": cands,
            "note": (f"{sum(surv)} of {len(cands)} candidates survive FDR control at "
                     f"α={alpha}. Testing many hypotheses raises the bar — that is the "
                     "point. A survivor is a lead, not a promise; take it to the Journal.")}
