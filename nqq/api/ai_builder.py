"""nqq.ai_builder — verbal description → signal/strategy spec (keyless parser).

Maps a plain-English idea to a signal family + params + symbol, so a beginner can go
from a sentence to a testable, honestly-scored strategy. Keyless by default (a
deterministic keyword parser); when NQQ_LLM is configured a real model can refine it
(not required). Every spec is marked requires_review — the honesty gates still apply.
"""
import re

import signals as sigmod

_KEYWORDS = [
    (r"\brsi\b|oversold|overbought", "rsi"),
    (r"bollinger|z-?score|std\s*dev", "bollinger"),
    (r"mean.?rever|revert|fade", "mean_reversion"),
    (r"breakout|donchian|new high|new low|channel", "breakout"),
    (r"volume|thrust|surge", "volume_thrust"),
    (r"momentum|trend|12.?1|carry the winners", "momentum"),
]
_SYMS = ["SPY", "QQQ", "AAPL", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY",
         "XLU", "XLB", "BTCUSDT", "ETHUSDT", "MES"]


def parse(text):
    t = (text or "").lower()
    family = next((f for pat, f in _KEYWORDS if re.search(pat, t)), "momentum")
    params = dict(sigmod.FAMILIES[family]["params"])
    m = re.search(r"(\d+)\s*[- ]?day", t)
    if m:
        n = int(m.group(1))
        for k in ("n", "look"):
            if k in params:
                params[k] = n
    if family == "rsi":
        mb = re.search(r"below\s+(\d+)", t)
        ms = re.search(r"above\s+(\d+)", t)
        if mb:
            params["buy_below"] = int(mb.group(1))
        if ms:
            params["sell_above"] = int(ms.group(1))
    sym = next((s for s in _SYMS if re.search(r"\b" + s.lower() + r"\b", t)), "SPY")
    warns = []
    if not any(re.search(pat, t) for pat, _ in _KEYWORDS):
        warns.append("No signal keyword recognised — defaulted to 12-1 momentum. "
                     "Keywords: RSI, Bollinger, mean-reversion, breakout, volume, momentum.")
    return {
        "family": family, "params": params, "sym": sym,
        "label": sigmod.FAMILIES[family]["label"],
        "suggested_name": f"{family.replace('_', '-')}-{sym.lower()}-ai1",
        "requires_review": True,
        "note": "Parsed to a testable spec. It earns nothing until it clears the honest "
                "gates in the Journal — parse ≠ edge.",
    }, warns
