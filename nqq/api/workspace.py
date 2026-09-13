"""nqq.workspace — portable, signed export/import of an entire workspace.

The whole state a user cares about — ideas, arena field, rooms, evolve proposals,
console tape, and the honesty artifacts (stamp ledger + trial count) — packaged as ONE
HMAC-signed bundle you can move between deployments. Import verifies the signature first
(a tampered bundle is refused), then restores. This is data portability with integrity,
not a raw dump. Ports zingq's bundle-codec idea.
"""
import time

import kernel

_KINDS = ("ideas", "signals", "rooms", "proposals", "console_open", "console_closed",
          "voice_records", "orders", "static")


def export(store, owner=None):
    """Package the workspace as a signed bundle. If owner is given, only that owner's
    documents are exported (multi-tenant isolation)."""
    docs = {}
    for kind in _KINDS:
        rows = store.list(kind)
        if owner and kind in ("ideas", "rooms", "proposals"):
            rows = [r for r in rows if r.get("owner") in (owner, None) or owner in
                    (r.get("members") or [])]
        docs[kind] = rows
    payload = {
        "nqq_workspace": 1, "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "owner": owner or "all", "docs": docs,
        "trials": store.trial_count(operator=owner) if owner else store.trial_count(),
        "ledger_ok": store.verify_ledger()[0],
    }
    return kernel.sign_bundle(payload)


def preview(bundle):
    # portability: require key-INDEPENDENT integrity (tamper-evidence). Authenticity
    # (signed by THIS deployment) is a bonus flag, not a barrier — so a workspace can
    # move between deployments while a tampered one is still refused.
    ok, reason = kernel.verify_integrity(bundle)
    if not ok:
        return None, reason
    p = bundle["payload"]
    if p.get("nqq_workspace") != 1:
        return None, "not an nqq workspace bundle"
    counts = {k: len(v) for k, v in p.get("docs", {}).items() if v}
    return {"exported": p.get("exported"), "owner": p.get("owner"), "counts": counts,
            "trials": p.get("trials"),
            "authentic": kernel.is_authentic(bundle)}, None


def _sanitize(kind, r, importer):
    """Strip fields a bundle must NOT be able to forge on import. A bundle can be
    integrity-valid (untampered) yet UNAUTHENTIC (not signed by this deployment), so an
    imported governance proposal or idea must not arrive pre-signed, pre-promoted, or
    owned by someone else. We reset ownership to the importer and force safe initial
    lifecycle state; the imported CONTENT (title, claim, params) is preserved."""
    r = dict(r)
    if kind == "proposals":
        import evolve
        r["author"] = importer
        r["status"] = "proposed"                       # never import a signed/entrenched state
        r["signatures"] = []                            # signatures are per-deployment human acts
        r["protected"] = any(h in (r.get("target") or "") for h in evolve.HONESTY_CORE)
        r["history"] = [{"status": "proposed", "by": importer, "at": "imported"}]
    elif kind == "ideas":
        r["owner"] = importer
        r["stage"] = 0                                  # re-earn every promotion gate locally
        r["public"] = False
        r["paper_opened"] = None
        r["gate"] = {"passed": False, "note": "imported — research not started"}
    elif kind == "rooms":
        r["owner"] = importer
    return r


def restore(store, bundle, merge=True, importer="solo"):
    """Verify then restore. merge=True keeps existing docs (new ids), merge=False replaces
    by id. A tampered bundle is refused before anything is written. Imported governance/
    idea docs are sanitized so a bundle can't forge ownership or lifecycle state."""
    ok, reason = kernel.verify_integrity(bundle)   # portable tamper-check (key-independent)
    if not ok:
        return None, f"refused — {reason}"
    p = bundle["payload"]
    if p.get("nqq_workspace") != 1:
        return None, "not an nqq workspace bundle"
    # SECURITY: only user-content kinds are importable. NEVER users/static/kv/sessions —
    # otherwise a valid-digest (but unauthenticated) bundle could overwrite admin creds or
    # forge arena winners. Privileged/global state stays local to each deployment.
    importable = {"ideas", "signals", "rooms", "proposals", "voice_records"}
    n, skipped = 0, []
    for kind, rows in p.get("docs", {}).items():
        if kind not in importable:
            if rows:
                skipped.append(kind)
            continue
        for r in rows:
            r = _sanitize(kind, r, importer)
            rid = r.get("id") or r.get("name") or n
            if merge and store.get(kind, rid):
                rid = f"{rid}-imp-{int(time.time()*1000)}-{n}"
                r = dict(r, id=rid)
            store.put(kind, rid, r)
            n += 1
    store.stamp("workspace_restored", p.get("owner", "all"),
                {"docs": n, "from": p.get("exported")})
    store.feed("info", f"WORKSPACE — restored {n} documents from a signed bundle")
    return {"restored": n, "skipped_privileged": skipped}, None
