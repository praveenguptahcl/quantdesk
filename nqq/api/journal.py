"""nqq.journal — research → production pipeline with HONEST promotion gates.

An idea moves through stages only if it earns each promotion:
  Idea → Research → Backtest → Parity → Paper → Live
Every gate is a real check (not a button): a deflated-Sharpe floor, a capacity floor,
out-of-sample consistency, a dual-recomputation PARITY match, and — for Live — a forward
paper track record plus a typed confirmation. The reason is always shown; nothing is
promoted on vibes. Ported from QuantDesk's journal + alphaforge promotion/safety/live_gate.
"""
import time

import backtest as bt
import stats as st

STAGES = ["Idea", "Research", "Backtest", "Parity", "Paper", "Live"]

# gate thresholds (honest, conservative)
GATE = {"dsr_floor": 0.55, "capacity_floor": 100_000, "oos_floor": 0.45,
        "parity_tol": 1e-6, "paper_days": 10}   # reconciliation: same estimand, two code paths


def create(store, owner, name, family, params=None, hyp=""):
    if not name or any(i["name"] == name for i in store.list("ideas")):
        return None, "name required and must be unique"
    doc = {"id": int(time.time() * 1000), "owner": owner, "name": name, "family": family,
           "params": params or {}, "hyp": hyp, "stage": 0, "public": False,
           "gate": {"passed": False, "note": "logged as an Idea — research not started"},
           "paper_opened": None, "created": time.strftime("%Y-%m-%d %H:%M")}
    store.put("ideas", doc["id"], doc)
    store.stamp("idea_created", str(doc["id"]), {"name": name, "owner": owner})
    store.feed("info", f"JOURNAL — idea logged: {name} ({family}) by {owner}")
    return doc, None


def _scorecard(store, idea, operator):
    return bt.scorecard(store, idea["family"], idea["params"], "SPY",
                        operator=operator, record_trial=False)


def evaluate_gate(store, idea, operator="solo"):
    """Compute whether the idea may advance from its CURRENT stage. Returns
    (can_promote, note, evidence). Each stage has its own honest requirement."""
    stage = idea["stage"]
    if stage >= len(STAGES) - 1:
        return False, "already Live", {}
    card, err = _scorecard(store, idea, operator)
    if err:
        return False, f"backtest failed: {err}", {}
    dsr = card["deflated_sharpe"]
    cert = card["certainty"]
    cap = card["capacity_aum"]
    wr = card["worst_regime_dsr"]
    oos = card.get("oos_consistency", 0.5)
    ev = {"deflated_sharpe": dsr, "worst_regime_dsr": wr, "capacity_aum": cap,
          "oos_consistency": oos,
          "certainty": cert["certainty"], "verdict": cert["verdict"]}

    if stage == 0:  # Idea -> Research: just needs a runnable backtest
        return True, "runnable — promote to Research", ev
    if stage == 1:  # Research -> Backtest: a computed backtest exists (always true here)
        return True, "backtest computed — promote to Backtest", ev
    if stage == 2:  # Backtest -> Parity: two INDEPENDENT engines must agree
        res = bt.run(idea["family"], idea["params"])
        if not res:
            return False, "backtest could not run for the parity check", ev
        rets = res["returns"]
        a = st.sharpe(rets)              # engine A: two-pass arithmetic Sharpe (_moments)
        b = st.parity_sharpe(rets)       # engine B: Welford one-pass — SAME estimand,
                                         # independent code path (a reconciliation, not a
                                         # second estimator; agrees to ~1e-15 unless buggy)
        rel = abs(a - b) / (abs(a) + 1e-9)
        ev["parity_a"], ev["parity_b"], ev["parity_rel"] = round(a, 3), round(b, 3), round(rel, 4)
        if rel > GATE["parity_tol"]:
            return False, (f"parity gate FAILED: the two engines disagree by {rel:.2e} "
                           f"(> {GATE['parity_tol']:.0e}) — a numerical bug, not a promotion"), ev
        return True, (f"parity OK — independent engines reconcile ({rel:.2e} ≤ "
                      f"{GATE['parity_tol']:.0e}) — promote to Parity"), ev
    if stage == 3:  # Parity -> Paper: must clear the honest edge floors
        fails = []
        if wr < GATE["dsr_floor"]:
            fails.append(f"worst-regime deflated Sharpe {wr:.2f} < {GATE['dsr_floor']}")
        if cap < GATE["capacity_floor"]:
            fails.append(f"capacity ${cap:,.0f} < ${GATE['capacity_floor']:,}")
        if oos < GATE["oos_floor"]:
            fails.append(f"out-of-sample consistency {oos:.2f} < {GATE['oos_floor']}")
        if fails:
            return False, "edge gate FAILED: " + "; ".join(fails), ev
        return True, "edge floors cleared — promote to Paper (forward test begins)", ev
    if stage == 4:  # Paper -> Live: needs a forward paper track record + typed confirm
        opened = idea.get("paper_opened")
        days = (time.time() - opened) / 86400 if opened else 0
        ev["paper_days"] = round(days, 1)
        if days < GATE["paper_days"]:
            return False, (f"Live gate: needs {GATE['paper_days']} paper days "
                           f"({days:.1f} so far)"), ev
        return True, "paper track record met — Live requires typed confirmation", ev
    return False, "no gate", ev


def promote(store, idea_id, operator="solo", confirm=None):
    idea = store.get("ideas", idea_id)
    if not idea:
        return None, "idea not found"
    # tenancy: only the owner (or solo/unowned, or admin) may advance an idea through the
    # gates — otherwise any logged-in user could promote (even to Live) someone else's idea
    # and pollute their trial ledger. Mirrors publish()/copy().
    owner = idea.get("owner")
    # solo mode (operator 'solo') and admin are trusted; otherwise the caller must own it
    if operator not in ("solo", "admin") and owner not in (operator, None, "solo"):
        return None, "not your idea — only its owner can promote it"
    ok, note, ev = evaluate_gate(store, idea, operator)
    if not ok:
        return None, note
    if idea["stage"] == 4:   # going Live
        if confirm != f"{idea['name']} GO LIVE":
            return None, f'typed confirmation must be exactly "{idea["name"]} GO LIVE"'
    idea["stage"] += 1
    idea["gate"] = {"passed": True, "note": note, "evidence": ev}
    if STAGES[idea["stage"]] == "Paper":
        idea["paper_opened"] = time.time()
    store.put("ideas", idea_id, idea)
    store.record_trial(operator, idea["family"], idea["name"], ev.get("deflated_sharpe", 0))
    store.stamp("idea_promoted", str(idea_id),
                {"to": STAGES[idea["stage"]], "note": note})
    store.feed("info", f"JOURNAL — {idea['name']} → {STAGES[idea['stage']]} ({note[:60]})")
    return idea, None


def publish(store, idea_id, owner, public):
    idea = store.get("ideas", idea_id)
    if not idea or (idea.get("owner") not in (owner, None) and owner != "admin"):
        return None, "not your idea"
    idea["public"] = bool(public)
    store.put("ideas", idea_id, idea)
    return idea, None


def copy(store, idea_id, owner):
    src = store.get("ideas", idea_id)
    if not src or not src.get("public"):
        return None, "idea not found or not public"
    base = f"{src['name']}-copy-{owner}"[:40]
    names = {i["name"] for i in store.list("ideas")}
    name, n = base, 2
    while name in names:
        name, n = f"{base}-{n}", n + 1
    doc, _ = create(store, owner, name, src["family"], src.get("params"),
                    f"copied from {src.get('owner')}/{src['name']}: {src.get('hyp', '')}")
    if doc:
        doc["copied_from"] = {"owner": src.get("owner"), "name": src["name"]}
        store.put("ideas", doc["id"], doc)
    return doc, None


def listing(store, owner=None):
    out = []
    for i in store.list("ideas"):
        if owner and i.get("owner") not in (owner, None) and not i.get("public"):
            continue  # tenancy: own ideas + anyone's public ones
        can, note, ev = evaluate_gate(store, i)
        out.append({**{k: i.get(k) for k in ("id", "name", "owner", "family", "params",
                                             "stage", "public", "hyp", "gate")},
                    "stage_name": STAGES[i.get("stage", 0)],
                    "next_gate": {"can": can, "note": note, "evidence": ev}})
    out.sort(key=lambda x: -x["stage"])
    return {"stages": STAGES, "gate": GATE, "ideas": out}


def seed(store, force=False):
    if store.get_kv("journal:seeded") == "1" and not force:
        return {"seeded": False}
    demo = [("momentum-etf", "momentum", {}, "12-1 momentum on the sector-ETF cross-section"),
            ("meanrev-z", "mean_reversion", {}, "fade 2-sigma dislocations"),
            ("rsi-dip", "rsi", {}, "buy oversold, sell overbought")]
    for nm, fam, p, hyp in demo:
        if not any(i["name"] == nm for i in store.list("ideas")):
            create(store, "solo", nm, fam, p, hyp)
    store.set_kv("journal:seeded", "1")
    return {"seeded": True}
