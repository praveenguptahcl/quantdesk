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
import threading
import time as _time
import json
import os
import secrets

import backtest
import llm as llm_mod


_INVEST_LOCK = threading.Lock()   # cash check+deduct must be atomic
SESSION_TTL_S = 7 * 24 * 3600     # sessions expire after 7 days


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
    store.set_kv(f"sess:{token}", f"{u}|{int(_time.time())}")
    return token


def whoami(store, token):
    if not enabled():
        return {"u": "solo", "name": "Solo Trader", "role": "admin", "cash": 100_000.0}
    if not token:
        return None
    raw = store.get_kv(f"sess:{token}")
    if not raw:
        return None
    u, _, issued = raw.partition("|")
    if issued and _time.time() - int(issued) > SESSION_TTL_S:
        store.set_kv(f"sess:{token}", "")
        return None
    doc = store.get_doc("users", u)
    if not doc or doc.get("disabled"):
        return None
    if issued and doc.get("sess_epoch") and int(issued) < doc["sess_epoch"]:
        return None   # password changed after this session was issued
    return {k: doc[k] for k in ("u", "name", "role", "cash", "must_change") if k in doc}


def set_password(store, u, new_pw):
    if len(new_pw or "") < 8:
        return "password must be at least 8 characters"
    doc = store.get_doc("users", u)
    salt = secrets.token_hex(16)
    doc.update(salt=salt, pw=_hash_pw(new_pw, salt), must_change=False)
    store.put_doc("users", u, doc)
    doc["sess_epoch"] = int(_time.time())   # old sessions die with the old password
    store.put_doc("users", u, doc)
    return None


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
    mine = idea.get("owner", "solo") in (user["u"], None, "solo") or user.get("role") == "admin"
    if not mine and not (idea.get("public") and idea.get("stage", 1) >= 5):
        return None, "not visible to you — you can invest in your own strategies, or public ones at Paper stage+"
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None, "amount must be a number"
    with _INVEST_LOCK:
        udoc = store.get_doc("users", user["u"])  # tracked in solo mode too (demo accounts)
        cash = udoc["cash"] if udoc else user.get("cash", 0)
        if amount <= 0 or amount > cash:
            return None, f"amount must be 0 < x ≤ your virtual cash (${cash:,.0f})"
        # THE RULE: investing forces the strategy public
        forced = not idea.get("public")
        idea["public"] = True
        store.put_doc("ideas", idea_id, idea)
        if udoc:
            udoc["cash"] = cash - amount
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
    base = f"{src['name']}-copy-{user['u']}"[:36]
    names = {i["name"] for i in store.list_docs("ideas")}
    name, n = base, 2
    while name in names:
        name, n = f"{base}-{n}", n + 1
    doc = logic.new_idea({
        "name": name,
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
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:   # tzdata missing — coarse fallback
        utc = datetime.datetime.utcnow()
        return utc - datetime.timedelta(hours=4 if 3 <= utc.month <= 11 else 5)


def current_week_id():
    et = _now_et()
    sunday = et - datetime.timedelta(days=(et.weekday() + 1) % 7)
    return sunday.strftime("%Y-W%W")


def week_deadline_str():
    return "Saturday 23:59 ET"


_WR_CACHE = {}  # params-json -> (date, value); real returns only move with new bars


def _strategy_week_return(params):
    """REAL last-5-trading-day return of the strategy variant (engine_a). Cached per
    calendar day so a demo-filled leaderboard doesn't rerun 10+ backtests per view."""
    key = json.dumps(params or {}, sort_keys=True)
    today = datetime.date.today().isoformat()
    hit = _WR_CACHE.get(key)
    if hit and hit[0] == today:
        return hit[1]
    r = backtest.engine_a(params or None)
    if not r or len(r["equity_daily"]) < 6:
        return 0.0
    eq = r["equity_daily"]
    val = eq[-1] / eq[-6] - 1
    _WR_CACHE[key] = (today, val)
    return val


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
    if et.weekday() == 5 and et.hour == 23 and et.minute >= 59:
        week = current_week_id()             # Saturday close: this week
    elif et.weekday() == 6:
        prev = et - datetime.timedelta(days=7)
        sunday = prev - datetime.timedelta(days=(prev.weekday() + 1) % 7)
        week = sunday.strftime("%Y-W%W")     # Sunday catch-up: the JUST-ENDED week
    else:
        return
    if store.get_kv(f"finalized:{week}"):
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


# ---------------- LLM traders (autonomous leaderboard participants) ----------------
def create_llm_user(store, u, name, provider="builtin"):
    if not u.isalnum() or store.get_doc("users", u):
        return None, "username taken or invalid"
    doc = {"u": u, "name": name or u, "role": "llm", "provider": provider,
           "salt": "-", "pw": "-", "disabled": False, "cash": 100_000.0}
    store.put_doc("users", u, doc)
    return doc, None


def tuning_pack(store, username, runnable):
    """The weekly export every participant (human or LLM) gets: own results,
    leaderboard, and every public strategy's params + real stats."""
    s = standings(store)
    mine = [inv for inv in store.list_docs("investments") if inv["user"] == username]
    for inv in mine:
        inv["week_return_pct"] = round(_strategy_week_return(inv.get("params")) * 100, 2)
    strategies = []
    for r in runnable:
        if r.get("params") is None:
            continue
        opt = store.get_doc("optimize", r["name"])
        strategies.append({
            "id": r["id"], "name": r["name"], "owner": r["owner"], "stage": r["stage"],
            "params": r["params"],
            "week_return_pct": round(_strategy_week_return(r["params"]) * 100, 2),
            "optimizer": ({"frozen_sharpe": opt["grid"]["frozen"]["sharpe"],
                           "plateau_mean": opt["grid"]["frozen"]["plateau_mean"],
                           "wf": opt["wf"]["verdict"]} if opt else None)})
    sig = backtest.current_signal() or {}
    return {"week": s["week"], "closes": s["closes"],
            "regime": sig.get("regime", {}).get("risk_on"),
            "my_investments": mine, "leaderboard": s["rows"],
            "public_strategies": strategies, "past_winners": s["winners"][:8]}


def divest_all(store, username):
    total = 0.0
    for inv in list(store.list_docs("investments")):
        if inv["user"] == username:
            total += inv["amount"]
            store.delete_doc("investments", inv["id"])
    udoc = store.get_doc("users", username)
    if udoc:
        udoc["cash"] = udoc.get("cash", 0) + total
        store.put_doc("users", username, udoc)
    return total


def _builtin_policy(pack, me=None):
    """Keyless fallback: pick the strategy with the best REAL evidence
    (optimizer plateau Sharpe if computed, else live full-period Sharpe).
    Conviction rule: a bot keeps ITS OWN variant unless another strategy beats
    it by >0.15 Sharpe — otherwise every keyless bot converges on one pick."""
    scored = []
    for st in pack["public_strategies"]:
        if st["optimizer"] and st["optimizer"]["plateau_mean"] is not None:
            score = st["optimizer"]["plateau_mean"]
            src = f"optimizer plateau SR {score}"
        else:
            r = backtest.engine_a(st["params"] or None)
            score = round(r["sharpe"], 2) if r else -9
            src = f"live full-period SR {score}"
        scored.append((score, st, src))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best, why = scored[0]
    own = [t for t in scored if t[1].get("owner") == me]
    if own and best.get("owner") != me and own[0][0] >= best_score - 0.15:
        o_score, o_st, o_src = own[0]
        return {"action": "keep", "strategy_id": o_st["id"], "params": o_st["params"] or {},
                "amount": 20000,
                "reason": f"conviction: my own {o_st['name']} ({o_src}) is within 0.15 SR of "
                          f"the field's best {best['name']} ({why}) — not worth switching"}
    return {"action": "switch", "strategy_id": best["id"], "params": best["params"] or {},
            "amount": 20000,
            "reason": f"builtin policy: highest real evidence — {best['name']} ({why}); "
                      f"last week {best['week_return_pct']:+.2f}%"}


def _llm_policy(pack, provider):
    prompt = ("You are an autonomous paper-trading agent in a weekly contest. "
              "Given this tuning pack (your results, leaderboard, public strategies with "
              "REAL backtest stats), reply ONLY with JSON: "
              '{"action":"keep|switch","strategy_id":<id>,"params":{...engine params...},'
              '"amount":<usd<=cash>,"reason":"<one sentence>"} . Prefer robust plateau '
              "Sharpe over last week's noise. Pack: " + json.dumps(pack)[:6000])
    r = llm_mod._post("https://api.anthropic.com/v1/messages",
                      {"model": os.environ.get("AI_MODEL", "claude-sonnet-5"),
                       "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]},
                      {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                       "anthropic-version": "2023-06-01"})
    txt = "".join(b.get("text", "") for b in r.get("content", []))
    import re as _re
    m = _re.search(r"\{.*\}", txt, _re.S)
    return json.loads(m.group(0)) if m else None


def run_llm_traders(store, runnable):
    """The platform initiates the interaction: brief each LLM user, take their pick,
    reinvest their book, and log the reasoning publicly."""
    results = []
    for u in store.list_docs("users"):
        if u.get("role") != "llm" or u.get("disabled"):
            continue
        pack = tuning_pack(store, u["u"], runnable)
        decision = None
        used = "builtin"
        if u.get("provider") == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                decision = _llm_policy(pack, "anthropic")
                used = "anthropic"
            except Exception:
                decision = None
        if not decision:
            decision = _builtin_policy(pack, me=u["u"])
            used = "builtin"
        if not decision:
            continue
        freed = divest_all(store, u["u"])
        udoc = store.get_doc("users", u["u"])
        amount = min(float(decision.get("amount", 20000)), udoc["cash"])
        user_view = {"u": u["u"], "cash": udoc["cash"], "role": "llm"}
        res, err = invest(store, user_view, decision.get("strategy_id"),
                          amount, decision.get("params"))
        entry = {"user": u["u"], "provider": used, "week": current_week_id(),
                 "decision": decision, "freed": freed, "invested": None if err else amount,
                 "error": err, "at": datetime.datetime.now().isoformat()[:16]}
        log = store.get_doc("static", "llm_log") or []
        log.insert(0, entry)
        store.put_doc("static", "llm_log", log[:60])
        store.add_feed("info", f"🤖 LLM TRADER {u['u']} ({used}): "
                       + (err or f"${amount:,.0f} → {decision.get('reason','')[:110]}"))
        results.append(entry)
    return results


# ---------------- demo community (sample accounts, admin-deletable) ----------------
DEMO_BOTS = [  # username, display, engine-param variant, one-line style
    ("grok",     "Grok",     {"mom": 126, "top_n": 2, "vol_tgt": 0.14}, "aggressive 6-month momentum, concentrated top-2, hot vol target"),
    ("chatgpt",  "ChatGPT",  {"vol_tgt": 0.10, "top_n": 3},             "trusts the frozen momo defaults — plateau over drama"),
    ("deepseek", "DeepSeek", {"mom": 189, "skip": 10, "vol_tgt": 0.12}, "9-month lookback with a short 10-day skip window"),
    ("claude",   "Claude",   {"vol_tgt": 0.08, "top_n": 4},             "low-vol, diversified top-4 — steady compounding"),
    ("copilot",  "Copilot",  {"sma_n": 150, "vol_tgt": 0.11},           "faster SMA-150 regime filter, quicker to re-risk"),
    ("gemini",   "Gemini",   {"mom": 63, "top_n": 2, "vol_tgt": 0.15},  "3-month sprint momentum — rides whatever is hot now"),
]
DEMO_HUMANS = [("maya", "Maya Patel"), ("ravi", "Ravi Sharma"), ("sofia", "Sofia Chen")]


def _demo_idea(store, owner, name, params, hyp):
    import logic
    doc = logic.new_idea({
        "name": name, "ac": "eq", "syms": ["SPY", "XLK"], "owner": owner,
        "hyp": f"[DEMO] {hyp}", "uni": "SPY + 9 sector ETFs",
        "feats": "12-1 momentum family (parameterized engine)",
        "kill": "underperforms frozen momo-etf-v3 by 20% over 8 weeks",
        "engine_params": params, "public": True, "demo": True})
    doc["stage"] = 5
    doc["gateNote"] = "Paper stage — demo account strategy (sample data)"
    store.put_doc("ideas", doc["id"], doc)
    return doc


def seed_demo(store, force=False):
    """Fill the community with sample LLM + human accounts so every screen has
    something to look at. Everything is tagged demo:True and admin-deletable."""
    if store.get_kv("demo:seeded") and not force:
        return {"seeded": 0, "note": "already seeded"}
    n = 0
    amounts = {"grok": 30000, "chatgpt": 22000, "deepseek": 18000,
               "claude": 25000, "copilot": 15000, "gemini": 28000}
    for u, name, params, style in DEMO_BOTS:
        if store.get_doc("users", u):
            continue
        doc, _ = create_llm_user(store, u, name, "builtin")
        doc["demo"] = True
        store.put_doc("users", u, doc)
        idea = _demo_idea(store, u, f"{u}-momo", params, f"{name}: {style}")
        amt = amounts.get(u, 20000)
        user_view = {"u": u, "cash": doc["cash"], "role": "llm"}
        invest(store, user_view, idea["id"], amt, params)
        log = store.get_doc("static", "llm_log") or []
        log.insert(0, {"user": u, "provider": "builtin", "week": current_week_id(),
                       "demo": True,
                       "decision": {"action": "switch", "strategy_id": idea["id"],
                                    "reason": f"[DEMO] {style}"},
                       "freed": 0, "invested": amt,
                       "at": datetime.datetime.now().isoformat()[:16]})
        store.put_doc("static", "llm_log", log[:60])
        n += 1
    human_params = {"maya": {"vol_tgt": 0.09},
                    "ravi": {"mom": 210, "top_n": 3},
                    "sofia": {"sma_n": 220, "vol_tgt": 0.12}}
    for u, name in DEMO_HUMANS:
        if store.get_doc("users", u):
            continue
        doc, _ = create_user(store, u, "demo123", name)
        doc["demo"] = True
        store.put_doc("users", u, doc)
        idea = _demo_idea(store, u, f"{u}-variant", human_params[u],
                          f"{name}'s hand-tuned momentum variant (login {u}/demo123)")
        user_view = {"u": u, "cash": doc["cash"], "role": "user"}
        invest(store, user_view, idea["id"], 12000, human_params[u])
        n += 1
    # cross-pollination: maya copies claude's strategy, ravi invests in chatgpt's book
    claude_idea = next((i for i in store.list_docs("ideas") if i.get("owner") == "claude"), None)
    maya = store.get_doc("users", "maya")
    if claude_idea and maya:
        cp, _ = copy_strategy(store, {"u": "maya"}, claude_idea["id"])
        if cp:
            cp["demo"] = True
            store.put_doc("ideas", cp["id"], cp)
    gpt_idea = next((i for i in store.list_docs("ideas") if i.get("owner") == "chatgpt"), None)
    ravi = store.get_doc("users", "ravi")
    if gpt_idea and ravi:
        invest(store, {"u": "ravi", "cash": ravi["cash"], "role": "user"},
               gpt_idea["id"], 8000, gpt_idea.get("engine_params"))
    store.set_kv("demo:seeded", "1")
    store.add_feed("info", f"🎭 DEMO COMMUNITY SEEDED — {n} sample accounts "
                   "(6 LLM traders + 3 humans) with live strategies; admin can delete any of them")
    return {"seeded": n}


def user_detail(store, username):
    """Everything about one account — the admin per-user management view."""
    u = store.get_doc("users", username)
    if not u:
        return None
    ideas = [{"id": i["id"], "name": i["name"], "stage": i.get("stage", 1),
              "public": bool(i.get("public")), "demo": bool(i.get("demo")),
              "params": i.get("engine_params")}
             for i in store.list_docs("ideas") if i.get("owner") == username]
    invs = [{"id": inv["id"], "strategy": inv["strategy"], "amount": inv["amount"],
             "week_return_pct": round(_strategy_week_return(inv.get("params")) * 100, 2),
             "opened": inv.get("opened")}
            for inv in store.list_docs("investments") if inv["user"] == username]
    log = [e for e in (store.get_doc("static", "llm_log") or []) if e["user"] == username][:5]
    return {"user": {k: u.get(k) for k in ("u", "name", "role", "cash", "disabled",
                                           "demo", "provider", "must_change")},
            "strategies": ideas, "investments": invs, "llm_decisions": log,
            "invested_total": round(sum(i["amount"] for i in invs), 2)}


def delete_user(store, username):
    """Admin: remove an account and all its data (strategies, investments, log)."""
    if username == "admin":
        return None, "the seed admin cannot be deleted"
    # (route also blocks self-delete — see p_delete_user)
    u = store.get_doc("users", username)
    if not u:
        return None, "user not found"
    n_inv = n_ideas = 0
    for inv in list(store.list_docs("investments")):
        if inv["user"] == username:
            store.delete_doc("investments", inv["id"])
            n_inv += 1
    for idea in list(store.list_docs("ideas")):
        if idea.get("owner") == username:
            store.delete_doc("ideas", idea["id"])
            n_ideas += 1
    log = store.get_doc("static", "llm_log") or []
    store.put_doc("static", "llm_log", [e for e in log if e["user"] != username])
    store.delete_doc("users", username)
    store.add_feed("warn", f"🗑 ACCOUNT DELETED by admin: {username} "
                   f"({n_ideas} strategies, {n_inv} investments removed)")
    return {"deleted": username, "strategies": n_ideas, "investments": n_inv}, None


def remove_demo(store):
    """Admin: wipe every demo-tagged account and its data in one click."""
    removed = []
    for u in list(store.list_docs("users")):
        if u.get("demo"):
            delete_user(store, u["u"])
            removed.append(u["u"])
    store.set_kv("demo:seeded", "")
    return {"removed": removed}


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
