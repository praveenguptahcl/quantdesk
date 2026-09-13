"""nqq.llm — an LLM competitor & council voice that stays HONEST.

Keyless by default: a deterministic policy that reasons over the *honest evidence*
(deflated Sharpe, worst-regime, certainty verdict) and picks accordingly — it cannot
manufacture confidence the numbers don't support. When NQQ_LLM_KEY is set, a real
provider (Anthropic-style /v1/messages) refines the pick, but the honest ceilings still
apply. Used for arena LLM agents and the council's AI voice. Stdlib only.
"""
import json
import logging
import os
import re
import urllib.request

import backtest as bt
import signals as sigmod

_log = logging.getLogger("nqq.llm")


def enabled_provider():
    return bool(os.environ.get("NQQ_LLM_KEY"))


def _real_call(prompt):
    key = os.environ.get("NQQ_LLM_KEY", "")
    model = os.environ.get("NQQ_LLM_MODEL", "claude-sonnet-5")
    body = json.dumps({"model": model, "max_tokens": 300,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read())
    return "".join(b.get("text", "") for b in d.get("content", []))


def pick_strategy(store, operator="llm", candidates=None):
    """The LLM agent's weekly pick: choose the family with the best HONEST evidence.
    Returns {family, params, reason, deflated_sharpe, certainty}."""
    fams = candidates or list(sigmod.FAMILIES.keys())
    scored = []
    for fam in fams:
        card, err = bt.scorecard(store, fam, {}, "SPY", operator=operator,
                                 record_trial=False)
        if err:
            continue
        scored.append((card["deflated_sharpe"], card["certainty"]["certainty"], fam, card))
    if not scored:
        return None
    scored.sort(reverse=True)
    dsr, cert, fam, card = scored[0]
    reason = (f"picked {fam}: highest deflated Sharpe {dsr} among {len(scored)} families, "
              f"certainty {int(cert*100)}% ({card['certainty']['verdict']}). "
              f"Honest evidence, not a hunch.")
    if enabled_provider():
        try:
            pack = [{"family": t[2], "deflated_sharpe": t[0], "certainty": t[1]}
                    for t in scored]
            txt = _real_call("You are an honest paper-trading agent. Given these families "
                             "with their deflated Sharpe and certainty, reply with ONE family "
                             "name and a one-sentence reason (prefer robust evidence over the "
                             f"highest raw number): {json.dumps(pack)}")
            # Match the family the model actually CHOSE, with word boundaries so short
            # names ("rsi") don't match inside other words. Prefer a family that follows an
            # explicit choice cue ("pick/choose/select/go with X"), so "avoid momentum,
            # pick rsi" selects rsi — not momentum just because it appears first or ranks
            # higher in `scored`. Fall back to reading order, then to the keyless pick.
            low = txt.lower()
            by_name = {t[2]: t for t in scored}
            chosen = None
            cue = re.search(r"(?:pick|choose|select|go with|recommend|favou?r)\s+"
                            r"(?:the\s+)?([a-z_]+)", low)
            if cue and cue.group(1) in by_name:
                chosen = by_name[cue.group(1)]
            if chosen is None:                       # else: first family named in the reply
                best_pos = len(low) + 1
                for name, t in by_name.items():
                    m = re.search(r"\b" + re.escape(name.lower()) + r"\b", low)
                    if m and m.start() < best_pos:
                        best_pos, chosen = m.start(), t
            if chosen:
                fam, dsr, cert, card = chosen[2], chosen[0], chosen[1], chosen[3]
                reason = "LLM (provider): " + txt.strip()[:160]
        except Exception as e:   # provider offline → keep the honest keyless pick
            _log.warning("LLM provider pick failed, using keyless policy: %s", e)
    return {"family": fam, "params": {}, "reason": reason, "deflated_sharpe": dsr,
            "certainty": cert, "verdict": card["certainty"]["verdict"]}


def council_voice(store, plan, other_voices):
    """An AI voice for the council. Keyless: summarises the rated evidence honestly and
    refuses to overstate. Stance follows the weight of the RATED voices only."""
    rated = [v for v in other_voices if v["record"]["rated"]]
    side = plan.get("side", "LONG").upper()
    fors = sum(1 for v in rated if v["stance"] == "FOR")
    againsts = sum(1 for v in rated if v["stance"] == "AGAINST")
    if not rated:
        stance, reason = "NEUTRAL", ("No rated voice has a real record here — I will not "
                                     "invent conviction. Gather evidence first.")
    elif fors > againsts:
        stance, reason = "FOR", f"{fors} rated voices lean FOR vs {againsts} against."
    elif againsts > fors:
        stance, reason = "AGAINST", f"{againsts} rated voices lean AGAINST vs {fors} for."
    else:
        stance, reason = "NEUTRAL", "Rated voices are split; no honest edge to the decision."
    if enabled_provider():
        try:
            reason = "AI (provider): " + _real_call(
                "One honest sentence: given these council voices, should we take the trade? "
                + json.dumps([{"f": v["family"], "s": v["stance"],
                               "rated": v["record"]["rated"]} for v in other_voices])
            ).strip()[:180]
        except Exception as e:   # provider offline → keep the honest keyless synthesis
            _log.warning("LLM council voice failed, using keyless synthesis: %s", e)
    # rated=False: this voice is a SYNTHESIZER, not an independent track-recorded voice.
    # It has no IC/live record of its own (ic1=0, pvalue=1.0), so it must not be tallied as
    # a rated vote nor display a "rated" chip — that would let a summary masquerade as
    # independent evidence and inflate the council's combined confidence.
    return {"family": "ai-council", "label": "AI Council (evidence synthesizer)",
            "stance": stance, "reason": reason,
            "record": {"ic1": 0.0, "hit_rate": 0.0, "rated": False,
                       "pvalue": 1.0, "live": None},
            "evidence": "/api/voices", "real_pit": True,
            "provider": "real" if enabled_provider() else "keyless"}
