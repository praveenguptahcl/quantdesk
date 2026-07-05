"""Alpaca paper-trading client (M6) — stdlib only, paper endpoints only.

Reads ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET / ALPACA_PAPER_BASE from the
environment (loaded from .env by server.py). Credentials are never logged,
printed, or included in any response payload. This module refuses to talk to
anything but a paper endpoint: live trading routes only through the M8 wizard.
"""
import json
import os
import time
import urllib.error
import urllib.request


class AlpacaError(Exception):
    pass


def _cfg():
    key = os.environ.get("ALPACA_PAPER_KEY", "").strip()
    sec = os.environ.get("ALPACA_PAPER_SECRET", "").strip()
    base = os.environ.get("ALPACA_PAPER_BASE", "https://paper-api.alpaca.markets").strip().rstrip("/")
    if not key or not sec or key.startswith("paste-"):
        return None
    if "paper" not in base:
        raise AlpacaError("refusing non-paper endpoint — live routes only through the go-live wizard (M8)")
    return key, sec, base


def configured():
    try:
        return _cfg() is not None
    except AlpacaError:
        return False


def _req(method, path, body=None, timeout=15):
    cfg = _cfg()
    if cfg is None:
        raise AlpacaError("Alpaca paper keys not configured in .env")
    key, sec, base = cfg
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={
        "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec,
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise AlpacaError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise AlpacaError(f"unreachable: {e.reason}")


# ---------------- public API ----------------
def account():
    a = _req("GET", "/v2/account")
    return {  # non-sensitive fields only
        "status": a.get("status"), "currency": a.get("currency"),
        "equity": float(a.get("equity", 0)), "cash": float(a.get("cash", 0)),
        "buying_power": float(a.get("buying_power", 0)),
        "portfolio_value": float(a.get("portfolio_value", 0)),
        "pattern_day_trader": a.get("pattern_day_trader"),
        "account_blocked": a.get("account_blocked"),
        "number_masked": "…" + str(a.get("account_number", ""))[-4:],
    }


def clock():
    c = _req("GET", "/v2/clock")
    return {"is_open": c.get("is_open"), "next_open": c.get("next_open"),
            "next_close": c.get("next_close")}


def positions():
    return [{"sym": p["symbol"], "qty": p["qty"], "avg_px": p["avg_entry_price"],
             "market_value": p["market_value"], "unrealized_pl": p["unrealized_pl"]}
            for p in _req("GET", "/v2/positions")]


def open_orders():
    return [{"id": o["id"][:8], "sym": o["symbol"], "side": o["side"].upper(),
             "type": o["type"].upper(), "qty": o["qty"], "px": o.get("limit_price") or "MKT",
             "state": o["status"]}
            for o in _req("GET", "/v2/orders?status=open")]


def latency_ms():
    t0 = time.time()
    _req("GET", "/v2/clock")
    return int((time.time() - t0) * 1000)


def submit_order(symbol, qty, side, order_type="limit", limit_price=None, tif="day"):
    body = {"symbol": symbol, "qty": str(qty), "side": side,
            "type": order_type, "time_in_force": tif}
    if limit_price is not None:
        body["limit_price"] = str(limit_price)
    o = _req("POST", "/v2/orders", body)
    return {"id": o["id"], "status": o["status"], "sym": o["symbol"],
            "side": o["side"], "qty": o["qty"], "limit": o.get("limit_price")}


def get_order(order_id):
    o = _req("GET", f"/v2/orders/{order_id}")
    return {"id": o["id"], "status": o["status"], "filled_qty": o.get("filled_qty")}


def cancel_order(order_id):
    _req("DELETE", f"/v2/orders/{order_id}")
    return True


def cancel_all_orders():
    return _req("DELETE", "/v2/orders")


def close_all_positions():
    """Kill-switch path: flatten everything on the paper account."""
    return _req("DELETE", "/v2/positions?cancel_orders=true")


def last_price(symbol):
    """Market data lives on data.alpaca.markets (IEX feed on free tier)."""
    cfg = _cfg()
    if cfg is None:
        return None
    key, sec, _ = cfg
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest?feed=iex"
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return float(d["trade"]["p"])
    except Exception:
        return None


def closed_orders(limit=15):
    out = []
    for o in _req("GET", f"/v2/orders?status=closed&limit={limit}&direction=desc"):
        out.append({"t": (o.get("submitted_at") or "")[11:19], "sym": o["symbol"],
                    "sq": f"{o['side'].upper()} {o['qty']}",
                    "px": o.get("filled_avg_price") or "—", "slip": "—",
                    "st": o["status"]})
    return out


def portfolio_history(period="1M"):
    """Real daily equity/P&L series from Alpaca."""
    return _req("GET", f"/v2/account/portfolio/history?period={period}&timeframe=1D")


def phase5_checkpoint(symbol="SPY"):
    """Place → confirm → cancel one tiny far-from-market limit order (1 share,
    ~50% below market so it can never fill). Returns an audit-able summary."""
    px = last_price(symbol) or 600.0
    limit = round(px * 0.5, 2)
    t0 = time.time()
    o = submit_order(symbol, 1, "buy", "limit", limit_price=limit)
    placed_ms = int((time.time() - t0) * 1000)
    st = get_order(o["id"])
    cancel_order(o["id"])
    time.sleep(0.5)
    final = get_order(o["id"])
    return {"symbol": symbol, "qty": 1, "limit": limit,
            "placed_status": st["status"], "final_status": final["status"],
            "roundtrip_ms": placed_ms,
            "ok": final["status"] in ("canceled", "pending_cancel")}
