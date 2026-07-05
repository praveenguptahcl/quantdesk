"""Business rules: gating, go-live validation, the verbal->spec parser (server-side
port of the GUI's built-in parser), daily P&L aggregation and wash-sale check.
Pure functions where possible — the test suite hits these directly and via HTTP.
"""
import math
import re
import time

STAGES = ["—", "Idea", "Research", "LEAN Backtest", "Parity Gate", "Paper", "Live"]

GOLIVE_CHECKLIST_LEN = 10  # 6 pre-flight + 4 risk sign-off items


# ---------------- promotion gate ----------------
def can_promote(idea):
    """Returns (ok, http_status, reason)."""
    if idea is None:
        return False, 404, "idea not found"
    if idea.get("stage", 1) >= 6:
        return False, 409, "already live"
    if idea.get("stage") == 5:
        return False, 409, "live promotion must go through the Go-Live Wizard"
    if idea.get("gate") != "pass":
        return False, 409, f"gate blocked: {idea.get('gateNote', 'checkpoint not passed')}"
    return True, 200, "ok"


def promote(idea):
    idea["stage"] = min(idea["stage"] + 1, 5)
    idea["gate"] = "block"
    idea["gateNote"] = "New stage — checkpoint not yet evaluated"
    return idea


# ---------------- go-live validation ----------------
def validate_golive(body, parity_lookup=None):
    """Returns (ok, status, reason). parity_lookup: fn(strategy)->parity dict or None."""
    venue = (body or {}).get("venue", "")
    strategy = (body or {}).get("strategy", "")
    confirm = (body or {}).get("confirm", "")
    checklist = (body or {}).get("checklist", [])
    if not venue or not strategy:
        return False, 422, "venue and strategy required"
    if len(checklist) < GOLIVE_CHECKLIST_LEN or not all(bool(x) for x in checklist):
        return False, 409, "all checklist items must be confirmed true"
    if confirm != f"{venue} GO LIVE":
        return False, 403, f'typed confirmation must be exactly "{venue} GO LIVE"'
    if parity_lookup:
        p = parity_lookup(strategy)
        if p is not None and not p.get("pass", False):
            return False, 409, f"parity gate failed for {strategy} — promotion to live blocked"
    return True, 200, "ok"


# ---------------- kill switch ----------------
def validate_kill(body):
    if (body or {}).get("confirm") != "FLATTEN":
        return False, 400, 'confirmation must be exactly "FLATTEN"'
    return True, 200, "ok"


# ---------------- idea validation ----------------
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def validate_idea(doc, existing_names):
    errs = []
    if not SLUG_RE.match(doc.get("name", "")):
        errs.append("name must be kebab-case slug")
    if doc.get("name") in existing_names:
        errs.append("name already exists")
    if len(doc.get("hyp", "")) < 20:
        errs.append("hypothesis too short")
    if not doc.get("uni"):
        errs.append("universe required")
    if not doc.get("kill") or doc.get("kill") in ("TBD", ""):
        errs.append("kill criterion required")
    return errs


def new_idea(doc):
    doc.setdefault("id", int(time.time() * 1000))
    doc["stage"] = 1
    doc["gate"] = "block"
    doc["gateNote"] = "Hypothesis logged — research not started"
    doc.setdefault("syms", [])
    doc.setdefault("seed", doc["id"] % 1000)
    doc.setdefault("sharpe", None)
    doc.setdefault("turn", "TBD")
    doc.setdefault("cap", "TBD")
    doc.setdefault("feats", "TBD")
    return doc


# ---------------- verbal -> spec parser (port of the GUI parser) ----------------
_STOP = {"AND", "OR", "THE", "IF", "EOD", "VWAP", "SMA", "RSI", "OFI", "LLM"}


def ai_parse(text):
    t = text.lower()
    syms = []
    for m in re.findall(r"\b[A-Z]{2,6}(?:USDT|USD)?\b", text):
        if m not in _STOP and m not in syms:
            syms.append(m)

    def num(pattern, default=None):
        m = re.search(pattern, t)
        return float(m.group(1)) if m else default

    if re.search(r"market.?mak", t):
        cls = "market_making"
    elif re.search(r"imbalance|order.?flow|ofi", t):
        cls = "order_flow"
    elif re.search(r"mean.?rever|revert|fade", t):
        cls = "mean_reversion"
    elif re.search(r"momentum|breakout|trend", t):
        cls = "momentum"
    else:
        cls = "unclassified"

    if re.search(r"\bms\b|millisecond|tick|order.?flow|book", t):
        resolution = "tick_l2"
    elif re.search(r"\b1?s(econd)?\b", t):
        resolution = "second"
    else:
        resolution = "minute"

    m_first = re.search(r"first (\d+) ?min", t)
    if m_first:
        regime = f"exclude_first_{m_first.group(1)}min_after_open"
    elif re.search(r"vix|regime|sma", t):
        regime = "detected — review"
    else:
        regime = "none"

    stop_raw = num(r"[−-](\d+) ?bps")
    m_kill = re.search(r"kill[^.]*?if (.+?)(?:\.|$)", t)

    spec = {
        "schema": "quantdesk.strategy.v1",
        "name": (syms[0] if syms else "strategy").lower() + "-" + cls.split("_")[0] + "-ai1",
        "class": cls,
        "universe": syms if syms else ["<UNSPECIFIED>"],
        "data": {"resolution": resolution, "warmup": "auto_from_lookbacks"},
        "regime": {"filter": regime},
        "entry": {
            "signal": ("ofi_z > %s" % num(r"above ([\d.]+)", 0.6)) if re.search(r"imbalance|ofi", t) else "<EXTRACT_FAILED>",
            "persistence_ms": num(r"(\d+) ?ms"),
            "spread_max_ticks": num(r"spread[^.]*?(\d+) tick"),
        },
        "exit": {
            "take_profit_bps": num(r"\+(\d+) ?bps"),
            "stop_bps": -abs(stop_raw) if stop_raw is not None else None,
            "timeout_s": num(r"after (\d+) second"),
        },
        "sizing": {
            "base_pct_equity": num(r"(\d+(?:\.\d+)?) ?% of equity"),
            "vol_adjust": "halve_when_short_horizon_rv_doubles" if re.search(r"halve|reduce|scale.*vol", t) else "none",
        },
        "confidence": {"model": "threshold", "note": "upgrade to sigmoid(z) per STRATEGY_GUIDE §2"},
        "risk": {"daily_loss_halt": "inherit_global", "per_trade_stop": "from exit.stop_bps"},
        "kill_criterion": m_kill.group(1).strip() if m_kill else "<REQUIRED — not found>",
        "holding_period": "seconds" if re.search(r"second|\bms\b", t) else "minutes",
        "generated_by": "builtin-parser-v1-server",
        "requires_review": True,
    }
    warns = []
    if spec["universe"][0] == "<UNSPECIFIED>":
        warns.append("No symbols detected — specify a universe.")
    if spec["kill_criterion"].startswith("<"):
        warns.append("No kill criterion found — required before a journal entry can be created.")
    if spec["entry"]["signal"] == "<EXTRACT_FAILED>":
        warns.append("Entry signal not extracted — rephrase, or connect an LLM provider.")
    if spec["data"]["resolution"] == "tick_l2":
        warns.append("Tick/L2 strategy — confirm the symbol spine has the L2 tier downloaded.")
    return spec, warns


def spec_blockers(spec):
    errs = []
    if spec.get("kill_criterion", "").startswith("<"):
        errs.append("spec has no kill criterion")
    uni = spec.get("universe") or ["<UNSPECIFIED>"]
    if uni[0] == "<UNSPECIFIED>":
        errs.append("spec has no universe")
    return errs


# ---------------- minimal YAML subset parser (risk/limits.yaml) ----------------
def parse_simple_yaml(text):
    """Parses the 2-level key/value structure used by risk/limits.yaml. Stdlib only."""
    root, current = {}, None
    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if not raw.startswith(" "):            # top-level section or scalar
            if val == "":
                current = {}
                root[key.strip()] = current
            else:
                root[key.strip()] = _scalar(val)
                current = None
        elif current is not None:
            current[key.strip()] = _scalar(val)
    return root


def _scalar(v):
    v = v.strip().strip('"').strip("'")
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


# ---------------- pre-route order validation (M7) ----------------
def validate_order(order, limits):
    """order: {sym, ac: eq|fut|cry, side, qty, price, ref_price}. Returns (ok, reasons)."""
    reasons = []
    g = limits.get("global", {})
    qty = float(order.get("qty", 0) or 0)
    price = float(order.get("price", 0) or 0)
    ref = float(order.get("ref_price", price) or price)
    notional = abs(qty * price)
    if qty <= 0 or price <= 0:
        return False, ["qty and price must be positive"]
    max_notional = g.get("max_single_order_notional_usd", 10000)
    if notional > max_notional:
        reasons.append(f"notional ${notional:,.0f} exceeds max ${max_notional:,.0f}")
    band = g.get("price_sanity_band_pct", 3.0)
    if ref > 0 and abs(price / ref - 1) * 100 > band:
        reasons.append(f"price {price} outside ±{band}% sanity band of last {ref}")
    ac = order.get("ac", "eq")
    if ac == "eq":
        cap = limits.get("equities", {}).get("max_position_usd_per_symbol", 15000)
        if notional > cap:
            reasons.append(f"exceeds equity per-symbol cap ${cap:,.0f}")
    elif ac == "fut":
        cap = limits.get("futures", {}).get("max_contracts_per_product", 4)
        if qty > cap:
            reasons.append(f"{qty:.0f} contracts exceeds limit {cap}")
    elif ac == "cry":
        cap = limits.get("crypto", {}).get("max_position_usd", 8000)
        if notional > cap:
            reasons.append(f"exceeds crypto position cap ${cap:,.0f}")
    return len(reasons) == 0, reasons


# ---------------- daily P&L + wash-sale ----------------
def daily_pnl(seed=9, year=2026, month=7):
    """Deterministic mock matching the GUI's generator until real fills exist (M14)."""
    s = seed * 7919 + 1
    out = {}
    import datetime
    for d in range(1, 32):
        try:
            wd = datetime.date(year, month, d).weekday()
        except ValueError:
            continue
        s = (s * 1103515245 + 12345) % 2147483648
        r = s / 2147483648
        out[d] = None if wd >= 5 else round((r - 0.42) * 900)
    return out


def wash_sale_flags(fills):
    """Minimal wash-sale heuristic: a loss-realizing sell followed by a buy of the
    same symbol within 30 days. fills: [{date:'YYYY-MM-DD', sym, side, qty, pnl}].
    Returns list of {sym, sell_date, rebuy_date}. NOT tax advice."""
    import datetime
    flags = []
    sells = [f for f in fills if f["side"] == "SELL" and f.get("pnl", 0) < 0]
    buys = [f for f in fills if f["side"] == "BUY"]
    for s_ in sells:
        sd = datetime.date.fromisoformat(s_["date"])
        for b in buys:
            bd = datetime.date.fromisoformat(b["date"])
            if b["sym"] == s_["sym"] and 0 < (bd - sd).days <= 30:
                flags.append({"sym": s_["sym"], "sell_date": s_["date"], "rebuy_date": b["date"]})
                break
    return flags
