"""nqq.evolve — governance for "alive software" (ported from zingq evolve).

Agents propose, humans dispose. Every proposal states a METRIC CLAIM. The lifecycle
is an append-only ledger with legal transitions only; the HONESTY CORE is
amendment-protected (changes to it need two human signatures + a red-team note).
Constitutional rules live in code, not a wiki.
"""
import time

# legal status transitions (agents propose -> humans dispose)
FLOW = {
    "proposed": {"accepted", "rejected"},
    "accepted": {"test_soaked", "rejected"},
    "test_soaked": {"signed", "rejected"},
    "signed": {"shipped"},
    "shipped": {"measured"},
    "measured": {"entrenched", "revert"},
    "revert": {"reverted"},
    "entrenched": set(),
    "rejected": set(),
    "reverted": set(),
}
# paths whose change requires the amendment procedure (2 signatures + red-team)
HONESTY_CORE = {"stats.py", "kernel.py"}


def propose(store, author, title, metric_claim, target=""):
    """File a proposal. A proposal without a falsifiable metric claim is refused."""
    if not (metric_claim or "").strip():
        return None, "a proposal must state a falsifiable metric claim (r67)"
    pid = int(time.time() * 1000)
    doc = {"id": pid, "author": author, "title": title, "metric_claim": metric_claim,
           "target": target, "status": "proposed",
           "protected": any(h in (target or "") for h in HONESTY_CORE),
           "signatures": [], "history": [{"status": "proposed", "by": author,
                                          "at": time.strftime("%Y-%m-%d %H:%M")}]}
    store.put("proposals", pid, doc)
    store.stamp("proposal", str(pid), {"title": title, "author": author,
                                       "claim": metric_claim})
    store.feed("info", f"EVOLVE — proposal filed by {author}: {title}")
    return doc, None


def transition(store, pid, new_status, actor, note="", signature=False):
    """Move a proposal along its lifecycle. Illegal transitions are refused.
    Protected (honesty-core) proposals need two human signatures before 'signed'."""
    # atomic RMW: validate + (maybe) add signature + transition under one lock, so two
    # concurrent signers/transitions can't race on signatures[] or history[]
    out = {"err": None, "signature_persisted": False}

    def _apply(doc):
        if not doc:
            out["err"] = "proposal not found"
            return None
        cur = doc["status"]
        if new_status not in FLOW.get(cur, set()):
            out["err"] = f"illegal transition {cur} → {new_status}"
            return None
        if signature and actor not in doc["signatures"]:
            doc["signatures"].append(actor)
            out["signature_persisted"] = True   # persisted atomically with this write
        if new_status == "signed" and doc["protected"] and len(doc["signatures"]) < 2:
            out["err"] = ("honesty-core change needs two human signatures + a red-team note "
                          f"before it can be signed (r66) — {len(doc['signatures'])}/2 so far")
            # still persist the freshly-added signature (write the doc, skip the transition)
            return doc if out["signature_persisted"] else None
        doc["status"] = new_status
        doc["history"].append({"status": new_status, "by": actor, "note": note,
                               "at": time.strftime("%Y-%m-%d %H:%M")})
        return doc

    doc = store.mutate("proposals", pid, _apply)
    if out["err"]:
        return None, out["err"]
    store.stamp("proposal_transition", str(pid), {"to": new_status, "by": actor})
    return doc, None


def sign(store, pid, signer):
    """Add a distinct human signature to a proposal (honesty-core changes need two).
    Signers must be distinct — you cannot sign your own change twice."""
    if not (signer or "").strip():
        return None, "a signer name is required"
    out = {"err": None}

    def _apply(doc):
        if not doc:
            out["err"] = "proposal not found"
            return None
        if signer in doc["signatures"]:
            out["err"] = f"{signer} has already signed — a second, distinct human must sign"
            return None
        doc["signatures"].append(signer)   # atomic append — no lost-signature race
        return doc

    doc = store.mutate("proposals", pid, _apply)
    if out["err"]:
        return None, out["err"]
    store.stamp("proposal_signed", str(pid), {"signer": signer,
                                              "signatures": len(doc["signatures"])})
    return doc, None


def ledger(store):
    rows = sorted(store.list("proposals"), key=lambda d: d["id"], reverse=True)
    return {"proposals": rows, "flow": {k: sorted(v) for k, v in FLOW.items()},
            "honesty_core": sorted(HONESTY_CORE)}


def seed(store, force=False):
    if store.get_kv("evolve:seeded") == "1" and not force:
        return {"seeded": False}
    p1, _ = propose(store, "claudebot", "Add per-regime capacity to the arena scorecard",
                    "worst-regime capacity shown for >=80% of agents; no rank change >1 place",
                    "arena.py")
    p2, _ = propose(store, "gridbot", "Tighten the certainty synthetic-data cap to 0.45",
                    "sample-symbol median certainty drops by >=0.05 with no real-symbol change",
                    "stats.py")
    if p1:
        transition(store, p1["id"], "accepted", "admin", "sensible, low risk")
    store.set_kv("evolve:seeded", "1")
    return {"seeded": True}
