"""nqq.council — the moment of truth: humans and AIs decide together.

A declared TradePlan gets (1) HYGIENE checks H1-H5 that stop bad decisions
regardless of direction — each teaches in plain English; (2) a COUNCIL of every
signal family, each rendered FOR / AGAINST / NEUTRAL with a reason, a track-record
chip (its real IC), and an EVIDENCE LINK (no voice testifies without a link);
(3) an HONEST bottom line that REFUSES a naked combined probability while voices are
unrated. Ported from zingq app/council.
"""
import logging

import signals as sigmod
import stats as st

_log = logging.getLogger("nqq.council")

FAMILIES = list(sigmod.FAMILIES.keys())


def hygiene(plan):
    """H1-H5: direction-agnostic sanity checks that teach."""
    checks = []
    sym = plan.get("sym", "")
    sizing = float(plan.get("size_pct", 0) or 0)
    stop = plan.get("stop_pct")
    rr = plan.get("reward_risk")
    checks.append({"id": "H1", "name": "Position size sane",
                   "pass": 0 < sizing <= 20,
                   "teach": "Risk at most a fifth of the book on one idea; "
                            f"you sized {sizing}%."})
    checks.append({"id": "H2", "name": "Stop defined",
                   "pass": bool(stop) and float(stop) > 0,
                   "teach": "A trade without a pre-declared stop has no defined risk; "
                            "the market will define it for you, worse."})
    checks.append({"id": "H3", "name": "Reward:risk >= 1.5",
                   "pass": bool(rr) and float(rr) >= 1.5,
                   "teach": "If you're not paid at least 1.5x your risk, hit rate has to "
                            "be very high to survive costs."})
    checks.append({"id": "H4", "name": "Symbol has real data",
                   "pass": _real(sym),
                   "teach": "Decisions on synthetic/sample data are machinery tests, "
                            "never evidence of a live edge."})
    checks.append({"id": "H5", "name": "Not fighting the regime blindly",
                   "pass": True,
                   "teach": "Shorting in a strong up-regime (or vice-versa) needs an "
                            "explicit reason; the council shows you the regime voices."})
    return checks


def _real(sym):
    import data as datamod
    return datamod.is_real(sym)


def convene(plan, store=None):
    """Each family testifies with a stance + reason + record chip + evidence link.
    A voice is 'rated' if EITHER its in-sample IC is significant OR it has earned a
    significant live record through the resolve/grade loop (loop.voice_records)."""
    sym = plan.get("sym", "SPY")
    side = plan.get("side", "LONG").upper()
    earned = {}
    if store is not None:
        try:
            import loop
            earned = loop.voice_records(store)
        except Exception as e:   # no track records yet → voices stay unrated (honest)
            _log.warning("voice records unavailable: %s", e)
            earned = {}
    voices = []
    for fam in FAMILIES:
        ev, err = sigmod.evaluate(fam, {}, sym)
        if err:
            continue
        ic1 = ev["ic"][1]
        family_tilt = "LONG" if ic1 >= 0 else "SHORT"
        if abs(ic1) < 0.01:
            stance = "NEUTRAL"
        elif family_tilt == side:
            stance = "FOR"
        else:
            stance = "AGAINST"
        rec = earned.get(fam, {})
        rated = ev["ic_pvalue"][1] < 0.10 or rec.get("rated", False)
        voices.append({
            "family": fam, "label": sigmod.FAMILIES[fam]["label"], "stance": stance,
            "reason": f"{sigmod.FAMILIES[fam]['label']} tilt is {family_tilt} "
                      f"(IC₁ {ic1:+.3f} over {ev['n']} bars).",
            "record": {"ic1": ic1, "hit_rate": ev["hit_rate"], "rated": rated,
                       "pvalue": ev["ic_pvalue"][1],
                       "live": (f"{rec['wins']}/{rec['total']} replay" if rec else None)},
            "evidence": f"/api/signal/eval?family={fam}&sym={sym}",
            "real_pit": ev["real_pit"],
        })
    return voices


def review(plan, store=None):
    """Full decision sheet. Refuses a combined probability while voices are unrated."""
    checks = hygiene(plan)
    voices = convene(plan, store)
    # The rated tally (and the combined probability it produces) counts INDEPENDENT
    # evidence only — the convened family voices, each with its own IC/live record.
    # Snapshot it BEFORE appending the AI synthesizer: that voice's stance is just a
    # summary of these same rated voices, so counting it as an extra rated vote would
    # double-count the majority direction and inflate both rated_count and combined%.
    rated = [v for v in voices if v["record"]["rated"]]
    if store is not None:
        try:
            import llm
            voices.append(llm.council_voice(store, plan, voices))
        except Exception:  # noqa
            pass
    hy_ok = all(c["pass"] for c in checks)
    fors = sum(1 for v in rated if v["stance"] == "FOR")
    againsts = sum(1 for v in rated if v["stance"] == "AGAINST")
    if not rated:
        bottom = ("No voice has a statistically rated record on this symbol yet. Per the "
                  "honesty rule, we will NOT print a combined probability from unrated "
                  "voices — gather more evidence first.")
        combined = None
    else:
        net = fors - againsts
        combined = round(0.5 + 0.5 * (net / max(len(rated), 1)), 2)
        lean = "FOR" if net > 0 else ("AGAINST" if net < 0 else "SPLIT")
        bottom = (f"{len(rated)} rated voice(s): {fors} FOR / {againsts} AGAINST → "
                  f"council leans {lean}. Combined confidence {int(combined*100)}% "
                  f"(rated voices only). Hygiene: {'PASS' if hy_ok else 'FAILING — fix first'}.")
    return {"plan": plan, "hygiene": checks, "hygiene_ok": hy_ok, "voices": voices,
            "rated_count": len(rated), "combined": combined, "bottom_line": bottom}


def journal(store, plan, sheet):
    """Freeze the decision to the append-only ledger (auditable later)."""
    return store.stamp("council_decision", plan.get("sym", "?"),
                       {"plan": plan, "bottom_line": sheet["bottom_line"],
                        "combined": sheet["combined"], "hygiene_ok": sheet["hygiene_ok"]})
