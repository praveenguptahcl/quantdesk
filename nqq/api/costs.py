"""nqq.costs — square-root market impact, funding/borrow, and CAPACITY.

"Sharpe without capacity is vanity." Given an edge per trade (bps) and turnover,
there is an AUM beyond which a strategy's own square-root impact eats the edge.
We solve for the participation rate where marginal impact == edge, convert to a
per-trade notional via ADV, and divide by annual turnover to get sustainable AUM.
A crowding factor (>=1) shrinks it to reflect competitors in the same trade.
"""
import math

# Calibrated (pessimistic-by-construction) knobs, aligned with alphaforge docs/COSTS.md.
# The sim must never flatter a strategy: fills are never cost-free, never beat the NBBO.
IMPACT_COEF = 0.10          # legacy alias (fraction form) — retained for reference/compat
IMPACT_COEF_BPS = 10.0      # impact in BPS at 100% ADV participation (= IMPACT_COEF*1e4/1e2)
DEFAULT_SPREAD_BPS = 2.0
DEFAULT_ADV_USD = 5e8       # $500M/day sector-ETF-ish default
TAKER_FEE_BPS = 10.0        # taker fee (bps of notional)
MAKER_FEE_BPS = 2.0         # maker fee (bps of notional)
MIN_ADVERSE_BPS = 1.0       # adverse-selection floor — a fill is NEVER cost-free
MARKET_PARTICIPATION = 0.25  # a market order takes at most 25% of interval volume


def impact_bps(participation):
    """Temporary market-impact cost (bps) for trading `participation` of ADV.
    Square-root law: impact = IMPACT_COEF_BPS · sqrt(participation). At 100% ADV that's
    the full IMPACT_COEF_BPS (10 bps); at 25% it's 5 bps."""
    return IMPACT_COEF_BPS * math.sqrt(max(0.0, participation))


def roundtrip_cost_bps(participation=0.0, spread_bps=DEFAULT_SPREAD_BPS):
    """Per-round-trip cost in bps: half-spread each side + impact."""
    return spread_bps + impact_bps(participation)


def capacity(edge_bps, turnover_annual, adv_usd=DEFAULT_ADV_USD, crowding=1.0):
    """Sustainable AUM (USD) at which impact eats the per-trade edge.
    Solve edge_bps == impact_bps(part) = IMPACT_COEF_BPS·sqrt(part)
       ->  part* = (edge_bps / IMPACT_COEF_BPS)**2.
    Per-trade notional = part* * adv_usd; AUM = notional / turnover (both ways / yr).
    Participation is CLAMPED to 100% ADV: when the edge exceeds impact-at-full-ADV
    (edge_bps > IMPACT_COEF_BPS) the sqrt law would solve for part* > 1, i.e. trading
    more than a day's volume in one interval. That extrapolates the impact model past
    its valid range and OVERSTATES capacity; the real binding constraint there is ADV
    itself, so capacity is just adv/turnover."""
    if edge_bps <= 0 or turnover_annual <= 0:
        return 0.0
    part_star = min(1.0, (edge_bps / IMPACT_COEF_BPS) ** 2)
    per_trade_notional = part_star * adv_usd
    aum = per_trade_notional / max(turnover_annual, 1e-6)
    return round(aum / max(crowding, 1.0), 0)


def funding_bps_per_bar(is_perp=False, is_short=False):
    """Carry: perp funding + short borrow (bps per bar, sample defaults)."""
    c = 0.0
    if is_perp:
        c += 1.0
    if is_short:
        c += 0.5
    return c
