"""nqq.library — a curated library of strategy templates, each honest about what it is.

Not "30 secret winning strategies" — those don't exist. Each entry states its edge
hypothesis, the family it maps to in nqq, its typical horizon, and its known failure
mode. Adopting one drops it into the Research Journal at Idea stage, where it must earn
every gate like anything else. Ported from QuantDesk's HFT library (honesty added).
"""
LIBRARY = [
    {"id": 1, "name": "12-1 Cross-Sectional Momentum", "family": "momentum", "class": "trend",
     "horizon": "months", "edge": "winners keep winning over 3-12 months; skip the last month "
     "to avoid short-term reversal.", "fails": "sharp regime flips (2020, 2022) whipsaw it."},
    {"id": 2, "name": "Sector Mean-Reversion (z-score)", "family": "mean_reversion", "class": "mr",
     "horizon": "days", "edge": "sector ETFs revert to their short mean after dislocations.",
     "fails": "trends — a genuine breakout keeps going and stops you out."},
    {"id": 3, "name": "RSI Dip-Buyer", "family": "rsi", "class": "mr", "horizon": "days",
     "edge": "oversold (RSI<30) bounces in an up-regime.", "fails": "bear markets — oversold "
     "gets more oversold."},
    {"id": 4, "name": "Bollinger Band Fade", "family": "bollinger", "class": "mr",
     "horizon": "days", "edge": "price fades from 2-sigma band extremes.",
     "fails": "volatility expansions ride the band."},
    {"id": 5, "name": "Donchian Channel Breakout", "family": "breakout", "class": "trend",
     "horizon": "weeks", "edge": "a new N-day high starts a trend (turtle-style).",
     "fails": "choppy ranges — repeated false breakouts bleed you."},
    {"id": 6, "name": "Volume Thrust Follow", "family": "volume_thrust", "class": "flow",
     "horizon": "intraday-days", "edge": "abnormal volume marks informed flow; follow it.",
     "fails": "volume without direction (index rebalances) is noise."},
    {"id": 7, "name": "Fast Momentum (6-1)", "family": "momentum", "class": "trend",
     "horizon": "weeks", "edge": "shorter lookback catches faster trends.",
     "fails": "higher turnover — costs and capacity bite sooner."},
    {"id": 8, "name": "Tight Mean-Reversion (10d)", "family": "mean_reversion", "class": "mr",
     "horizon": "days", "edge": "shorter window reverts faster, more trades.",
     "fails": "even more whipsaw-prone; capacity is tiny."},
    {"id": 9, "name": "Wide Breakout (100d)", "family": "breakout", "class": "trend",
     "horizon": "months", "edge": "only the biggest moves trigger; fewer false starts.",
     "fails": "late entries — you miss the first leg."},
    {"id": 10, "name": "RSI Fast (7d)", "family": "rsi", "class": "mr", "horizon": "days",
     "edge": "quicker oversold reads for nimble dip-buying.",
     "fails": "noisy — many shallow signals, most are nothing."},
]


def listing():
    return {"library": LIBRARY,
            "note": ("There are no secret winning strategies. Every template here is a "
                     "hypothesis that must earn each honest gate in the Journal. Adopt one "
                     "to start — the numbers, not the name, decide.")}


def adopt(store, lib_id, owner):
    import journal
    entry = next((x for x in LIBRARY if x["id"] == lib_id), None)
    if not entry:
        return None, "template not found"
    base = entry["name"].lower().replace(" ", "-").replace("(", "").replace(")", "")
    import re
    base = re.sub(r"[^a-z0-9-]", "", base)[:34]
    names = {i["name"] for i in store.list("ideas")}
    name, n = base, 2
    while name in names:
        name, n = f"{base}-{n}", n + 1
    return journal.create(store, owner, name, entry["family"], {},
                          f"from library: {entry['edge']} (fails when: {entry['fails']})")
