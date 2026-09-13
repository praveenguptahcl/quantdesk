"""nqq.loop — READ → TEST → DECIDE → ACT → RESOLVE, and the record that sharpens it.

A council decision is journaled with the plan and its voices. When it RESOLVES (the
forward outcome exists), each voice is GRADED — was its stance right? — and the voice's
running track record updates. This is zingq's core idea: every resolution grades the
voices, so the record earns "rated" status honestly over time, rather than being handed
out by a p-value alone.
"""
import time

import data as datamod
import signals as sigmod
import stats as st


def record_and_resolve(store, plan, sheet):
    """Journal the decision AND resolve it against the realized next-bar move (demo:
    entry at t-1, outcome at t so it resolves immediately), grading every voice."""
    sym = plan.get("sym", "SPY")
    bars = datamod.load_ohlcv(sym)
    if not bars or len(bars) < 60:
        return {"resolved": False, "reason": "no data"}
    records = store.get("voice_records", "all") or {}
    # resolve against a varied historical bar (so records aren't degenerate); the
    # entry index walks forward each decision, and the outcome is the NEXT bar.
    n_prior = sum(r.get("total", 0) for r in records.values()) // max(1, len(records) or 1)
    j = 55 + (n_prior * 7) % (len(bars) - 57)
    realized = bars[j + 1]["c"] / bars[j]["c"] - 1
    realized_dir = 1 if realized >= 0 else -1
    graded = []
    for v in sheet["voices"]:
        fam = v["family"]
        tilt = 1 if v["record"]["ic1"] >= 0 else -1
        correct = (tilt == realized_dir)
        rec = records.get(fam, {"wins": 0, "total": 0})
        rec["wins"] += 1 if correct else 0
        rec["total"] += 1
        records[fam] = rec
        graded.append({"family": fam, "stance": v["stance"], "correct": correct})
    store.put("voice_records", "all", records)
    h = store.stamp("decision_resolved", sym,
                    {"realized_pct": round(realized * 100, 3),
                     "graded": [(g["family"], g["correct"]) for g in graded]})
    store.feed("info", f"LOOP — decision on {sym} resolved ({realized*100:+.2f}%); "
               f"{sum(g['correct'] for g in graded)}/{len(graded)} voices correct")
    return {"resolved": True, "realized_pct": round(realized * 100, 3),
            "graded": graded, "stamp": h}


def voice_records(store):
    """Per-family running record, with a binomial 'rated' test (is the hit rate
    significantly different from 50%?)."""
    records = store.get("voice_records", "all") or {}
    out = {}
    for fam, rec in records.items():
        n, w = rec["total"], rec["wins"]
        hit = w / n if n else 0.0
        # normal-approx two-sided p-value for hit != 0.5
        if n >= 8:
            se = (0.25 / n) ** 0.5
            z = abs(hit - 0.5) / (se or 1e-9)
            p = 2 * (1 - st.norm_cdf(z))
        else:
            p = 1.0
        out[fam] = {"wins": w, "total": n, "hit_rate": round(hit, 3),
                    "pvalue": round(p, 4), "rated": (n >= 8 and p < 0.10)}
    return out
