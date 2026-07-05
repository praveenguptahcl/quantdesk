"""Multi-user community layer (M15): auth, roles, virtual books, strategy
publishing, weekly leaderboard (closes Saturday 23:59 ET), copy-trading, chat.

Rules enforced here:
  - Investing in a strategy FORCES it public (leaderboard money is transparent).
  - Visibility cannot be flipped private while invested.
  - Week = Sunday 00:00 -> Saturday 23:59 America/New_York; winner frozen then.
Activation: QD_MULTIUSER=1 in .env. Solo mode (default) bypasses auth entirely.
Stdlib only. Passwords: PBKDF2-SHA256(100k). Sessions: HttpOnly cookie tokens.
"""
import datetime
import hashlib
import json
import os
import secrets

import backtest
import llm as llm_mod


def enabled():
    return os.environ.get("QD_MULTIUSER", "0") == "1"


# ---------------- users & auth ----------------
def _hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex()


def ensure_seed_admin(store):
    if not store.list_docs("users"):
        salt = secrets.token_hex(16)
        store.put_doc("users", "admin", {
            "u": "admin", "name": "Administrator", "role": "admin",
            "salt": salt, "pw": _hash_pw("changeme", salt),
            "must_change": True, "disabled": False, "cash": 100_000.0})


def create_user(store, u, pw, name, role="user"):
    if not u.isalnum() or store.get_doc("users", u):
        return None, "username taken or invalid (alphanumeric only)"
    salt = secrets.token_hex(16)
    doc = {"u": u, "name": name or u, "role": role, "salt": salt,
           "pw": _hash_pw(pw, salt), "must_change": False,
           "disabled": False, "cash": 100_000.0}
    store.put_doc("users", u, doc)
    return doc, None


def login(store, u, pw):
    doc = store.get_doc("users", u)
    if not doc or doc.get("disabled") or _hash_pw(pw, doc["salt"]) != doc["pw"]:
        return None
    token = secrets.token_hex(24)
    store.set_kv(f"sess:{token}", u)
    return token


def whoami(store, token):
    if not enabled():
        return {"u": "solo", "name": "Solo Trader", "role": "admin", "cash": 100_000.0}
    if not token:
        return None
    u = store.get_kv(f"sess:{token}")
    if not u:
        return None
    doc = store.get_doc("users", u)
    if not doc or doc.get("disabled"):
        return None
    return {k: doc[k] for k in ("u", "name", "role", "cash", "must_change") if k in doc}


def set_password(store, u, new_pw):
    doc = store.get_doc("users", u)
    salt = secrets.token_hex(16)
    doc.update(salt=salt, pw=_hash_pw(new_pw, salt), must_change=False)
    store.put_doc("users", u, doc)


# ---------------- publishing & investing ----------------
def publish(store, user, idea_id, public):
    idea = store.get_doc("ideas", idea_id)
    if not idea or idea.get("owner", "solo") not in (user["u"], None) and user["role"] != "admin":
        return None, "not your strategy"
    if not public and _invested_in(store, idea_id):
        return None, "cannot make private while money is invested — divest first (leaderboard rule)"
    idea["public"] = bool(public)
    store.put_doc("ideas", idea_id, idea)
    return idea, None


def _invested_in(store, idea_id):
    return any(str(inv.get("idea_id")) == str(idea_id)
               for inv in store.list_docs("investments"))


def invest(store, user, idea_id, amount, params=None):
    idea = store.get_doc("ideas", idea_id)
    if not idea:
        return None, "strategy not found"
    amount = float(amount)
    if amount <= 0 or amount > user["cash"]:
        return None, f"amount must be 0 < x ≤ your virtual cash (${user['cash']:,.0f})"
    # THE RULE: investing forces the strategy public
    forced = not idea.get("public")
    idea["public"] = True
    store.put_doc("ideas", idea_id, idea)
    udoc = store.get_doc("users", user["u"]) if enabled() else None
    if udoc:
        udoc["cash"] -= amount
        store.put_doc("users", user["u"], udoc)
    inv = {"id": int(datetime.datetime.now().timestamp() * 1000), "user": user["u"],
           "idea_id": idea_id, "strategy": idea["name"], "amount": amount,
           "params": params or {}, "week": current_week_id(),
           "opened": datetime.datetime.now().isoformat()[:16]}
    store.put_doc("investments", inv["id"], inv)
    return {"investment": inv, "forced_public": forced}, None


def copy_strategy(store, user, idea_id):
    src = store.get_doc("ideas", idea_id)
    if not src or not src.get("public"):
        return None, "strategy not found or not public"
    import logic
    doc = logic.new_idea({
        "name": f"{src['name']}-copy-{user['u']}"[:40],
        "ac": src.get("ac", "eq"), "syms": src.get("syms", []),
        "hyp": src.get("hyp", ""), "uni": src.get("uni", ""),
        "feats": src.get("feats", ""), "kill": src.get("kill", "inherit — review!"),
        "owner": user["u"], "public": False,
        "copied_from": {"user": src.get("owner", "solo"), "strategy": src["name"]},
    })
    store.put_doc("ideas", doc["id"], doc)
    return doc, None


# ---------------- weekly leaderboard ----------------
def _now_et():
    # ET = UTC-4 (EDT) / UTC-5 (EST); coarse DST: Mar-Nov -> -4
    utc = datetime.datetime.utcnow()
    off = 4 if 3 <= utc.month <= 11 else 5
    return utc - datetime.timedelta(hours=off)


def current_week_id():
    et = _now_et()
    sunday = et - datetime.timedelta(days=(et.weekday() + 1) % 7)
    return sunday.strftime("%Y-W%W")


def week_deadline_str():
    return "Saturday 23:59 ET"


def _strategy_week_return(params):
    """REAL last-5-trading-day return of the strategy variant (engine_a)."""
    r = backtest.engine_a(params or None)
    if not r or len(r["equity_daily"]) < 6:
        return 0.0
    eq = r["equity_daily"]
    return eq[-1] / eq[-6] - 1


def standings(store):
    week = current_week_id()
    per_user = {}
    for inv in store.list_docs("investments"):
        wr = _strategy_week_return(inv.get("params"))
        pnl = inv["amount"] * wr
        d = per_user.setdefault(inv["user"], {"user": inv["user"], "invested": 0.0,
                                              "week_pnl": 0.0, "positions": []})
        d["invested"] += inv["amount"]
        d["week_pnl"] += pnl
        d["positions"].append({"strategy": inv["strategy"], "amount": inv["amount"],
                               "params": inv.get("params") or "frozen defaults",
                               "week_return_pct": round(wr * 100, 2)})
    rows = sorted(per_user.values(), key=lambda x: x["week_pnl"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["week_return_pct"] = round(r["week_pnl"] / r["invested"] * 100, 2) if r["invested"] else 0.0
        r["week_pnl"] = round(r["week_pnl"], 2)
    return {"week": week, "closes": week_deadline_str(), "rows": rows,
            "winners": store.get_doc("static", "winners") or []}


def maybe_finalize_week(store):
    """Called periodically; freezes the winner at/after Saturday 23:59 ET."""
    et = _now_et()
    week = current_week_id()
    if store.get_kv(f"finalized:{week}"):
        return
    if not (et.weekday() == 5 and et.hour == 23 and et.minute >= 59) and not (et.weekday() == 6):
        return
    s = standings(store)
    winners = store.get_doc("static", "winners") or []
    if s["rows"]:
        w = s["rows"][0]
        winners.insert(0, {"week": week, "user": w["user"],
                           "week_pnl": w["week_pnl"], "week_return_pct": w["week_return_pct"],
                           "finalized": et.isoformat()[:16]})
        store.put_doc("static", "winners", winners[:52])
        store.add_feed("info", f"🏆 WEEK {week} WINNER: {w['user']} "
                       f"({w['week_return_pct']:+.2f}% on virtual book)")
    store.set_kv(f"finalized:{week}", "1")


# ---------------- LLM chatbot ----------------
SYSTEM = """You are QuantDesk's assistant. The platform: signals (docs/SIGNALS.md,
regime SPY>SMA200 then 12-1 momentum top-3, sigmoid confidence, vol-target sizing),
dual-backtest parity gate, paper trading via Alpaca, weekly leaderboard closing
Saturday 23:59 ET where invested strategies are forcibly public and copyable.
Answer concisely. Educational only — never give personalized financial advice."""


def chat(store, user, msg, provider="builtin"):
    sig = backtest.current_signal() or {}
    lead = standings(store)
    context = (f"Live: regime {'RISK-ON' if sig.get('regime', {}).get('risk_on') else 'RISK-OFF'}, "
               f"decision: {sig.get('decision', 'n/a')[:90]}. "
               f"Leaderboard week {lead['week']}: "
               + (", ".join(f"#{r['rank']} {r['user']} {r['week_return_pct']:+.1f}%"
                            for r in lead["rows"][:3]) or "no investments yet"))
    if provider != "builtin":
        try:
            spec = llm_mod._post  # reuse transport
            body = {"model": os.environ.get("AI_MODEL", "claude-sonnet-5"), "max_tokens": 400,
                    "system": SYSTEM + " Context: " + context,
                    "messages": [{"role": "user", "content": msg}]}
            r = llm_mod._post("https://api.anthropic.com/v1/messages", body,
                              {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                               "anthropic-version": "2023-06-01"})
            return "".join(b.get("text", "") for b in r.get("content", [])), provider
        except Exception:
            pass
    # builtin: pattern-matched answers grounded in live data
    m = msg.lower()
    if "signal" in m or "regime" in m:
        return (f"Current state: {context.split('Leaderboard')[0]} Full derivation lives in "
                "docs/SIGNALS.md and the Strategy Lab's LIVE SIGNAL panel shows every number."), "builtin"
    if "leader" in m or "winner" in m or "rank" in m:
        return (f"{context.split('. Leaderboard')[-1].strip()} — week closes {lead['closes']}. "
                "Rule: any strategy you invest in becomes public automatically."), "builtin"
    if "copy" in m:
        return ("Open Leaderboard, expand any player's positions, press Copy — the spec lands in "
                "your Journal as a private draft (crediting the author). Review its kill criterion "
                "before investing."), "builtin"
    if "invest" in m or "public" in m:
        return ("Invest from your User Panel: pick a strategy, optional parameter tweaks, amount "
                "from your $100k virtual book. Investing FORCES the strategy public — that's the "
                "leaderboard's transparency rule."), "builtin"
    return ("I can explain signals/regime, the leaderboard, copying, and investing. For deeper "
            "answers connect an LLM key in .env (ANTHROPIC_API_KEY) — this is the built-in "
            "fallback. Docs & Learn has the full curriculum."), "builtin"
