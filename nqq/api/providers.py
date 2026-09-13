"""nqq.providers — real market data by reusing whatever keys exist, fail-safe.

Read-only keys only (nqq never routes real orders). Every function returns an honest
`source` label and degrades to catalog/synthetic when a provider is missing or offline —
nothing crashes, and a synthetic value is never labelled "live". Reuses the exact env
var names the other platforms use (kernel.load_env pulls them in).
"""
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

import data as datamod

_log = logging.getLogger("nqq.providers")
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 256          # bound the cache so a symbol-spray can't grow it without limit
_TTL = 15


def _get(url, headers=None, timeout=4):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "nqq/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _alpaca_headers():
    return {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY", ""),
            "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET", ""),
            "User-Agent": "nqq/1.0"}


_CRYPTO_MAP = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}


def quote(sym):
    """Latest price with an honest source. Crypto → Binance public; equities → Alpaca
    IEX when keyed; else catalog last-close; else synthetic."""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(sym)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    out = None
    if sym in _CRYPTO_MAP:
        try:
            d = _get(f"https://api.binance.com/api/v3/ticker/price?symbol={_CRYPTO_MAP[sym]}")
            out = {"sym": sym, "price": round(float(d["price"]), 2),
                   "source": "live·binance", "live": True, "ts": time.strftime("%H:%M:%S")}
        except Exception as e:   # network/parse failure → fall back, but say why in the log
            _log.warning("binance quote failed for %s: %s", sym, e)
            out = None
    elif os.environ.get("ALPACA_PAPER_KEY"):
        try:
            d = _get(f"https://data.alpaca.markets/v2/stocks/{sym}/trades/latest",
                     headers=_alpaca_headers())
            px = d.get("trade", {}).get("p")
            if px:
                out = {"sym": sym, "price": round(float(px), 2), "source": "live·alpaca-iex",
                       "live": True, "ts": time.strftime("%H:%M:%S")}
        except Exception as e:
            _log.warning("alpaca quote failed for %s: %s", sym, e)
            out = None
    if out is None:
        bars = datamod.load_ohlcv(sym)
        if bars:
            out = {"sym": sym, "price": round(bars[-1]["c"], 2), "live": False,
                   "source": "catalog·last-close" if bars[0].get("real") else "synthetic",
                   "ts": bars[-1]["d"]}
        else:
            out = {"sym": sym, "price": None, "source": "unavailable", "live": False}
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX and sym not in _CACHE:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])   # evict least-recently-fetched
            _CACHE.pop(oldest, None)
        _CACHE[sym] = (now, out)
    return out


def quotes(syms):
    return [quote(s) for s in syms]


def stooq_daily(sym):
    """Keyless daily bars from Stooq (extends the catalog on demand). Returns list of
    {d,o,h,l,c,v} or None. US equities use the '.us' suffix."""
    s = sym.lower()
    if "." not in s and sym.isalpha() and sym == sym.upper():
        s = s + ".us"
    url = f"https://stooq.com/q/d/l/?s={urllib.parse.quote(s)}&i=d"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nqq/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            text = r.read().decode()
        rows = [ln.split(",") for ln in text.strip().splitlines()[1:]]
        out = []
        for c in rows:
            if len(c) >= 6 and c[1] not in ("", "N/D"):
                out.append({"d": c[0], "o": float(c[1]), "h": float(c[2]),
                            "l": float(c[3]), "c": float(c[4]),
                            "v": float(c[5]) if c[5] not in ("", "N/D") else 0.0, "real": True})
        return out or None
    except Exception as e:
        _log.warning("stooq daily failed for %s: %s", sym, e)
        return None


def fred_series(series_id="SP500"):
    """A FRED macro series (real regime input) when FRED_KEY is set, else None."""
    key = os.environ.get("FRED_KEY")
    if not key:
        return None
    try:
        d = _get("https://api.stlouisfed.org/fred/series/observations?"
                 + urllib.parse.urlencode({"series_id": series_id, "api_key": key,
                                           "file_type": "json", "limit": 60,
                                           "sort_order": "desc"}))
        obs = [float(o["value"]) for o in d.get("observations", [])
               if o["value"] not in (".", "")]
        return obs[::-1] or None
    except Exception as e:
        _log.warning("fred series %s failed: %s", series_id, e)
        return None


def status():
    import kernel
    ks = kernel.key_status()
    return {
        "keys": ks,
        "providers": [
            {"name": "Binance (crypto)", "live": True, "keyless": True},
            {"name": "Alpaca IEX (equities)", "live": ks["alpaca_paper"],
             "note": "reuses ALPACA_PAPER_KEY"},
            {"name": "Stooq daily (equities)", "live": True, "keyless": True},
            {"name": "FRED macro", "live": ks["fred"], "note": "reuses FRED_KEY"},
            {"name": "Anthropic LLM", "live": ks["llm"], "note": "reuses ANTHROPIC_API_KEY"},
        ],
        "note": ("Real where a key exists, synthetic/catalog otherwise — always labelled. "
                 "Keys are reused read-only from your existing .env; nqq never routes "
                 "real orders."),
    }
