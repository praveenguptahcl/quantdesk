"""nqq.arena — the honest competition.

Agents (rule families run by operators) compete. The DURABLE ranking is the
deflated Sharpe with operator-level multiplicity (an operator's whole fleet + its
trial history deflate its best result — Sybil / variance-farming defence). Every row
carries a bootstrap CI, a baseline-excess, a trial count, and the mandatory
"weekly raw return is spectacle, not evidence" banner. Short samples rank below-fold.
"""
import datetime
import json

import backtest as bt
import kernel
import stats as st

NOT_EVIDENCE = ("Weekly raw return is SPECTACLE, not evidence. The durable ranking is the "
                "deflated Sharpe — multiplicity-adjusted, net of costs, with a confidence "
                "interval. A short or low-confidence sample cannot be a winner.")

# seed field: (operator, agent, family, params, agent_display)
SEED_AGENTS = [
    ("alice", "momentum-classic", "momentum", {}, "Momentum Classic"),
    ("alice", "momentum-fast", "momentum", {"look": 126}, "Momentum Fast"),
    ("bob", "meanrev-z", "mean_reversion", {}, "Mean-Reversion Z"),
    ("bob", "rsi-dip", "rsi", {}, "RSI Dip-Buyer"),
    ("carol", "breakout-55", "breakout", {}, "Donchian 55"),
    ("carol", "breakout-20", "breakout", {"n": 20}, "Donchian 20"),
    ("dave", "bollinger", "bollinger", {}, "Bollinger Fade"),
    ("erin", "volthrust", "volume_thrust", {}, "Volume Thrust"),
]


LLM_AGENTS = [
    ("claudebot", "ai-alpha", "AI Alpha (picks best evidence)"),
    ("gridbot", "ai-scout", "AI Scout (picks best evidence)"),
]


def seed(store, force=False):
    if store.get_kv("arena:seeded") == "2" and not force:
        return {"seeded": False}
    agents = [{"operator": op, "agent": ag, "family": fam, "params": p, "display": disp,
               "demo": True} for op, ag, fam, p, disp in SEED_AGENTS]
    agents += [{"operator": op, "agent": ag, "family": "llm", "params": {}, "display": disp,
                "llm": True, "demo": True} for op, ag, disp in LLM_AGENTS]
    store.put("arena_agents", "field", {"agents": agents})
    store.set_kv("arena:seeded", "2")
    store.set_kv("arena:standings", "")   # force recompute
    store.feed("info", f"ARENA — seeded {len(agents)} demo agents across 5 operators")
    return {"seeded": True, "agents": len(agents)}


def reset(store):
    store.put("arena_agents", "field", {"agents": []})
    store.set_kv("arena:seeded", "")
    store.set_kv("arena:standings", "")
    store.feed("warn", "ARENA — demo agents cleared by admin")
    return {"reset": True}


def _week_id():
    d = datetime.date.today()
    return d.strftime("%Y-W%W")


def standings(store, recompute=False):
    """Cached daily. Ranks agents by deflated Sharpe (durable); shows raw as spectacle."""
    day = datetime.date.today().isoformat()
    cached = store.get_kv("arena:standings")
    if cached and not recompute:
        try:
            obj = json.loads(cached)
            if obj.get("day") == day:
                return obj
        except ValueError:
            pass
    field = (store.get("arena_agents", "field") or {}).get("agents", [])
    rows = []
    for a in field:
        family, params, llm_reason = a["family"], a["params"], None
        if a.get("llm"):
            import llm
            pick = llm.pick_strategy(store, operator=a["operator"])
            if not pick:
                continue
            family, params, llm_reason = pick["family"], pick["params"], pick["reason"]
        card, err = bt.scorecard(store, family, params, "SPY",
                                 operator=a["operator"], record_trial=False)
        if err:
            continue
        cert = card["certainty"]
        rows.append({
            "operator": a["operator"], "agent": a["agent"], "display": a["display"],
            "family": (family + " (AI)" if a.get("llm") else family),
            "llm_reason": llm_reason, "raw_sharpe": card["sharpe"],
            "deflated_sharpe": card["deflated_sharpe"],
            "ci": card["bootstrap"], "n_obs": card["n_obs"],
            # show the SAME multiplicity count that deflated this row's Sharpe (prior
            # recorded trials + this evaluation itself = trial_count()+1). Recomputing
            # store.trial_count() here reported k while the DSR beside it was deflated
            # against k+1 — the "Trials" column disagreed with its own P(edge).
            "n_trials": card["n_trials"],
            "capacity_aum": card["capacity_aum"], "certainty": cert["certainty"],
            "verdict": cert["verdict"], "real_pit": card["real_pit"],
            "below_fold": card["n_obs"] < 60 or cert["certainty"] < 0.40,
        })
    # durable ranking: deflated Sharpe desc; below-fold always sink
    rows.sort(key=lambda r: (r["below_fold"], -r["deflated_sharpe"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    obj = {"day": day, "week": _week_id(), "banner": NOT_EVIDENCE, "rows": rows,
           "winners": store.get("static", "arena_winners") or []}
    store.set_kv("arena:standings", json.dumps(obj))
    return obj


def finalize_week(store):
    """Freeze the week AS A SIGNED, REPLAYABLE BUNDLE. Only an evidence-grade agent
    (above the fold) can be crowned; otherwise the honest result is 'no winner' — but
    the field is still signed and verifiable for the record."""
    s = standings(store, recompute=True)
    eligible = [r for r in s["rows"] if not r["below_fold"]]
    week = _week_id()
    w = eligible[0] if eligible else None
    bundle = kernel.sign_bundle({"week": week, "winner": w, "field": s["rows"],
                                 "banner": NOT_EVIDENCE})
    store.put("bundles", f"week-{week}", bundle)
    verdict = (f"{w['operator']}/{w['agent']} (deflated Sharpe {w['deflated_sharpe']})"
               if w else "NO evidence-grade winner — every agent is Inconclusive")
    h = store.stamp("week_finalized", f"week-{week}",
                    {"winner": (w["operator"] + "/" + w["agent"]) if w else None,
                     "verdict": verdict, "sig": bundle["sig"][:16]})
    weeks = store.get("static", "arena_weeks") or []
    weeks.insert(0, {"week": week, "winner": (w["operator"] + "/" + w["agent"]) if w else None,
                     "deflated_sharpe": w["deflated_sharpe"] if w else None,
                     "verdict": verdict, "bundle_sig": bundle["sig"][:16], "stamp": h[:16]})
    store.put("static", "arena_weeks", weeks[:52])
    store.feed("info", f"🏆 ARENA week {week}: {verdict} — signed bundle week-{week}")
    return {"winner": w, "verdict": verdict, "bundle_sig": bundle["sig"], "stamp": h}
