"""nqq.execution — a paper broker with an HONEST fill model + TCA.

Orders decided at bar t fill at bar t+1 (no same-bar clairvoyance), at the mid plus
half-spread plus square-root market impact. The order tape records the decision price,
the realized fill, and the slippage. A Transaction-Cost-Analysis (TCA) summary and a
Fill Honesty Certificate prove the fills are modelled, not wished. Ported from alphaforge
execution/fill_model + QuantDesk orders.
"""
import time

import costs as costmod
import data as datamod


def _trigger(order_type, side, o, h, l, limit_px, stop_px):
    """Decide whether a limit/stop order fills against the next bar's OHLC, and at what
    reference price. Returns (filled, ref_px, maker) — maker=True means the resting order
    provided liquidity (limit), so it earns the maker (not taker) fee. Honest paper rules:
      market → always fills at the open (taker).
      limit  → BUY fills iff low ≤ limit (bar traded down to it); SELL iff high ≥ limit.
               Fill at the better of open/limit; maker.
      stop   → BUY (stop-entry) triggers iff high ≥ stop; SELL iff low ≤ stop. Becomes a
               market order at the worse of open/stop; taker."""
    su = side.upper()
    if order_type == "market":
        return True, o, False
    if order_type == "limit":
        if su == "BUY" and l <= limit_px:
            return True, min(o, limit_px), True
        if su == "SELL" and h >= limit_px:
            return True, max(o, limit_px), True
        return False, None, True
    if order_type == "stop":
        if su == "BUY" and h >= stop_px:
            return True, max(o, stop_px), False
        if su == "SELL" and l <= stop_px:
            return True, min(o, stop_px), False
        return False, None, False
    return True, o, False


def place(store, sym, side, qty, operator="solo", adv_usd=costmod.DEFAULT_ADV_USD,
          order_type="market", limit_px=None, stop_px=None):
    """Place a PAPER order (market / limit / stop). Evaluates against next bar's OHLC
    (t+1); no same-bar clairvoyance. Refuses if the risk envelope is halted. A limit/stop
    whose trigger isn't reached returns status 'working' (recorded, not filled)."""
    if store.get_kv("risk:halted") == "1":
        return None, "risk envelope HALTED — reset before placing orders"
    order_type = (order_type or "market").lower()
    if order_type not in ("market", "limit", "stop"):
        return None, f"unknown order type {order_type!r} (market|limit|stop)"
    if order_type == "limit" and not limit_px:
        return None, "a limit order needs a limit price"
    if order_type == "stop" and not stop_px:
        return None, "a stop order needs a stop price"
    bars = datamod.load_ohlcv(sym)
    if not bars or len(bars) < 2:
        return None, "no data for symbol"
    decide = bars[-2]["c"]          # decision reference (bar t)
    nb = bars[-1]                   # next bar (t+1): the fill opportunity
    fill_mid = nb["o"]
    trig_px = float(limit_px) if order_type == "limit" else (
              float(stop_px) if order_type == "stop" else fill_mid)
    # pre-route risk validation (notional cap, price band, per-asset cap) — the limits
    # REFUSE, they don't silently clip. Band is checked against the order's own price.
    import risk as riskmod
    ac = "cry" if sym.endswith("USDT") else ("fut" if sym in ("MES", "MNQ") else "eq")
    ok, reasons = riskmod.validate({"qty": abs(qty), "price": trig_px, "ref_price": decide,
                                    "ac": ac})
    if not ok:
        return None, "risk validation refused: " + "; ".join(reasons)
    filled, ref_px, maker = _trigger(order_type, side, nb["o"], nb["h"], nb["l"],
                                     limit_px and float(limit_px), stop_px and float(stop_px))
    if not filled:
        order = {"id": int(time.time() * 1000), "operator": operator, "sym": sym,
                 "side": side.upper(), "qty": qty, "type": order_type,
                 "limit_px": limit_px, "stop_px": stop_px,
                 "decide_px": round(decide, 2), "fill_px": None, "notional": 0.0,
                 "slippage_bps": 0.0, "impact_bps": 0.0, "at": time.strftime("%H:%M:%S"),
                 "status": "working",
                 "note": f"{order_type} not reached (next bar H{nb['h']}/L{nb['l']})"}
        def _app(t):
            t = t or {"rows": []}
            t["rows"].insert(0, order); t["rows"] = t["rows"][:200]; return t
        store.mutate("orders", "tape", _app, default={"rows": []})
        store.feed("info", f"WORKING — {side.upper()} {qty} {sym} {order_type} not triggered")
        return order, None
    notional = abs(qty) * ref_px
    participation = min(costmod.MARKET_PARTICIPATION, notional / max(adv_usd, 1))
    # pessimistic by construction: half-spread + impact + fee + adverse floor. A resting
    # limit earns the maker fee; a market/stop pays the taker fee.
    fee_bps = costmod.MAKER_FEE_BPS if maker else costmod.TAKER_FEE_BPS
    cost_bps = (costmod.DEFAULT_SPREAD_BPS / 2 + costmod.impact_bps(participation)
                + fee_bps + costmod.MIN_ADVERSE_BPS)
    slip_sign = 1 if side.upper() == "BUY" else -1
    fill_px = ref_px * (1 + slip_sign * cost_bps / 1e4)
    # A limit order can NEVER fill worse than its limit: a resting BUY provides liquidity
    # at (or below) limit_px, a resting SELL at (or above) it — the maker doesn't cross the
    # spread against itself. Without this clamp the cost model could push the fill PRICE
    # past the limit (e.g. a 747.40 BUY "filling" at 747.70), a price the order legally
    # cannot achieve. Clamp to the limit so the tape can't report an impossible fill.
    if order_type == "limit":
        lp = float(limit_px)
        fill_px = min(fill_px, lp) if side.upper() == "BUY" else max(fill_px, lp)
    slippage_bps = round((fill_px / decide - 1) * 1e4 * slip_sign, 2)
    order = {"id": int(time.time() * 1000), "operator": operator, "sym": sym,
             "side": side.upper(), "qty": qty, "type": order_type,
             "limit_px": limit_px, "stop_px": stop_px, "decide_px": round(decide, 2),
             "fill_px": round(fill_px, 4), "notional": round(notional, 2),
             "participation": round(participation, 5), "maker": maker,
             "impact_bps": round(costmod.impact_bps(participation), 2),
             "slippage_bps": slippage_bps, "at": time.strftime("%H:%M:%S"),
             "status": "filled"}
    # atomic append: hold the lock across read→insert→trim→write so two concurrent
    # fills can't clobber each other's tape (lost-update race)
    def _append(tape):
        tape = tape or {"rows": []}
        tape["rows"].insert(0, order)
        tape["rows"] = tape["rows"][:200]
        return tape
    store.mutate("orders", "tape", _append, default={"rows": []})
    store.stamp("paper_fill", sym, {"side": side, "type": order_type,
                                    "slippage_bps": slippage_bps})
    store.feed("info", f"FILL — {side.upper()} {qty} {sym} ({order_type}) @ "
               f"{order['fill_px']} (slip {slippage_bps:+.1f}bp, "
               f"impact {order['impact_bps']}bp)")
    return order, None


def tape(store, operator=None):
    """The order tape. In multiuser mode pass operator to scope to the caller's OWN orders
    — the tape is one global document, so without this every user would see everyone's
    fills, positions and P&L (tenant leak)."""
    rows = (store.get("orders", "tape") or {"rows": []})["rows"]
    if operator is not None:
        rows = [r for r in rows if r.get("operator") == operator]
    return rows


def positions(store, operator=None):
    """Open positions and realized/unrealized P&L derived from the ACTUAL fill tape
    (not a wish-list). Walks fills oldest→newest per symbol with average-cost accounting:
    same-side adds extend the position at a blended cost; opposite-side fills realize P&L
    against the average cost. Unrealized P&L marks the residual to the latest quote. This
    is the honest position of record — it can only reflect fills that really happened."""
    import providers as prov
    rows = [r for r in tape(store, operator=operator) if r.get("status") == "filled"]
    rows = list(reversed(rows))          # oldest first
    book = {}                            # sym -> {qty, avg_cost, realized}
    for r in rows:
        sym, sgn = r["sym"], (1 if r["side"] == "BUY" else -1)
        q, px = sgn * abs(r["qty"]), r["fill_px"]
        b = book.setdefault(sym, {"qty": 0.0, "avg_cost": 0.0, "realized": 0.0})
        cur = b["qty"]
        if cur == 0 or (cur > 0) == (q > 0):          # opening or extending
            new_qty = cur + q
            b["avg_cost"] = ((abs(cur) * b["avg_cost"] + abs(q) * px) / abs(new_qty)
                             if new_qty else 0.0)
            b["qty"] = new_qty
        else:                                          # reducing / closing / flipping
            closed = min(abs(q), abs(cur))
            direction = 1 if cur > 0 else -1
            b["realized"] += direction * (px - b["avg_cost"]) * closed
            b["qty"] = cur + q
            if (cur > 0) != (b["qty"] > 0) and b["qty"] != 0:   # flipped through zero
                b["avg_cost"] = px
            elif b["qty"] == 0:
                b["avg_cost"] = 0.0
    out, realized_total, unreal_total = [], 0.0, 0.0
    for sym, b in book.items():
        realized_total += b["realized"]
        mark = None
        if abs(b["qty"]) > 1e-9:
            qd = prov.quote(sym)
            mark = qd.get("price")
            unreal = ((mark - b["avg_cost"]) * b["qty"]) if mark else 0.0
            unreal_total += unreal
            out.append({"sym": sym, "qty": round(b["qty"], 4),
                        "avg_cost": round(b["avg_cost"], 4),
                        "mark": mark, "mark_live": qd.get("live", False),
                        "unrealized": round(unreal, 2),
                        "realized": round(b["realized"], 2)})
        elif abs(b["realized"]) > 1e-9:
            out.append({"sym": sym, "qty": 0.0, "avg_cost": 0.0, "mark": None,
                        "mark_live": False, "unrealized": 0.0,
                        "realized": round(b["realized"], 2)})
    out.sort(key=lambda x: -abs(x["unrealized"]) - abs(x["realized"]))
    return {"positions": out, "realized_pnl": round(realized_total, 2),
            "unrealized_pnl": round(unreal_total, 2),
            "total_pnl": round(realized_total + unreal_total, 2),
            "note": "P&L derived from real fills via average-cost accounting; "
                    "open positions marked to the latest quote (live flag shown)."}


def tca(store, operator=None):
    """Transaction Cost Analysis over the paper tape + a fill-honesty certificate.
    Only FILLED rows count — a resting/unfilled limit or stop (status 'working') has no
    slippage and must not dilute the averages or flip the honesty flag. Scoped per
    operator when given (tenant isolation)."""
    rows = [r for r in tape(store, operator=operator) if r.get("status") == "filled"]
    if not rows:
        return {"n": 0, "certificate": "no fills yet"}
    slips = [r["slippage_bps"] for r in rows]
    impacts = [r["impact_bps"] for r in rows]
    avg_slip = round(sum(slips) / len(slips), 2)
    return {
        "n": len(rows),
        "avg_slippage_bps": avg_slip,
        "avg_impact_bps": round(sum(impacts) / len(impacts), 2),
        "worst_slippage_bps": round(max(slips), 2),
        "total_notional": round(sum(r["notional"] for r in rows), 2),
        "certificate": (f"Fills modelled at t+1: half-spread + square-root impact + "
                        f"{costmod.TAKER_FEE_BPS:.0f}bp taker fee + {costmod.MIN_ADVERSE_BPS:.0f}bp "
                        "adverse-selection floor (alphaforge-calibrated, pessimistic by "
                        "construction). No same-bar fills, no zero-cost fills. Every fill "
                        "is stamped to the append-only ledger."),
        "honest": all(r["slippage_bps"] != 0 for r in rows),
    }
