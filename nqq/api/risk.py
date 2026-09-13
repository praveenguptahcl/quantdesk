"""nqq.risk — the risk envelope: circuit breakers, per-asset limits, kill switch.

Pre-trade limits and a daily-loss / max-drawdown halt that blocks new orders when
tripped. The kill switch is the one real safeguard; it halts and stamps the event.
Ported from QuantDesk risk controls + alphaforge safety/envelope.
"""
LIMITS = {
    "max_order_notional_usd": 100_000,
    "price_band_pct": 3.0,
    "daily_loss_halt_usd": -2_000,
    "max_drawdown_halt_pct": -8.0,
    "per_asset": {"eq": 15_000, "fut": 40_000, "cry": 8_000},
}


def state(store):
    return {
        "limits": LIMITS,
        "halted": store.get_kv("risk:halted") == "1",
        "halt_reason": store.get_kv("risk:halt_reason", ""),
    }


def validate(order):
    """Pre-route check. Returns (ok, reasons[])."""
    reasons = []
    notional = abs(order.get("qty", 0)) * order.get("price", 0)
    if notional > LIMITS["max_order_notional_usd"]:
        reasons.append(f"notional ${notional:,.0f} exceeds "
                       f"${LIMITS['max_order_notional_usd']:,}")
    ref = order.get("ref_price", order.get("price", 0)) or order.get("price", 0)
    price = order.get("price", 0)
    if ref and abs(price / ref - 1) * 100 > LIMITS["price_band_pct"]:
        reasons.append(f"price {price} outside ±{LIMITS['price_band_pct']}% of last {ref}")
    ac = order.get("ac", "eq")
    cap = LIMITS["per_asset"].get(ac, 15_000)
    if notional > cap:
        reasons.append(f"exceeds {ac} per-symbol cap ${cap:,}")
    return len(reasons) == 0, reasons


def kill(store, confirm):
    if confirm != "FLATTEN":
        return None, 'confirmation must be exactly "FLATTEN"'
    store.set_kv("risk:halted", "1")
    store.set_kv("risk:halt_reason", "manual kill switch")
    store.stamp("kill_switch", "risk", {"halted": True})
    store.feed("err", "KILL SWITCH — new order submission halted")
    return {"halted": True}, None


def simulate_breach(store):
    store.set_kv("risk:halted", "1")
    store.set_kv("risk:halt_reason", "simulated daily-loss breach")
    store.stamp("breach_test", "risk", {"halted": True})
    store.feed("err", "BREACH SIMULATED — halted (test)")
    return {"halted": True}


def rearm(store):
    store.set_kv("risk:halted", "0")
    store.set_kv("risk:halt_reason", "")
    store.stamp("risk_rearm", "risk", {"halted": False})
    store.feed("info", "RISK — breakers re-armed; order submission enabled")
    return {"halted": False}
