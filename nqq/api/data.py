"""nqq.data — catalog OHLCV + point-in-time discipline.

A PointInTime view refuses to let a feature computed "as of bar t" read any bar
> t. The lookahead guard turns silent optimism (using tomorrow's close today) into
a loud error. Real daily bars live in nqq/data/catalog/*.csv (copied from the
QuantDesk catalog); synthetic fallback keeps everything runnable offline.
"""
import csv
import hashlib
import math
import os
import random


def _stable_seed(sym):
    """A process-independent seed. Python's builtin hash() is salted per process
    (PYTHONHASHSEED), which would make 'deterministic synthetic' data differ across
    restarts and break replayable evidence over synthetic symbols. SHA-256 is stable."""
    return int(hashlib.sha256(sym.encode()).hexdigest()[:8], 16)

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "data", "catalog"))


class LookaheadError(Exception):
    pass


def available():
    if not os.path.isdir(CATALOG):
        return []
    return sorted(f[:-4] for f in os.listdir(CATALOG) if f.endswith(".csv"))


def load_ohlcv(sym):
    """List of dicts {d,o,h,l,c,v}; real if in catalog, else deterministic synthetic."""
    path = os.path.join(CATALOG, f"{sym}.csv")
    if os.path.exists(path):
        out = []
        with open(path) as f:
            for row in csv.DictReader(f):
                try:
                    out.append({"d": row["Date"], "o": float(row["Open"]),
                                "h": float(row["High"]), "l": float(row["Low"]),
                                "c": float(row["Close"]), "v": float(row["Volume"]),
                                "real": True})
                except (KeyError, ValueError):
                    continue
        if out:
            return out
    return _synth(sym)


def _synth(sym, n=760):
    seed = _stable_seed(sym)
    rng = random.Random(seed)
    p, out = 100.0, []
    import datetime
    d0 = datetime.date(2022, 1, 3)
    drift = 0.0003 * (1 if (seed % 2) else -1)
    for i in range(n):
        p *= 1 + rng.gauss(drift, 0.013)
        o = p * (1 + rng.uniform(-0.004, 0.004))
        out.append({"d": (d0 + datetime.timedelta(days=i)).isoformat(),
                    "o": round(o, 2), "h": round(max(o, p) * 1.004, 2),
                    "l": round(min(o, p) * 0.996, 2), "c": round(p, 2),
                    "v": round(rng.uniform(4e6, 9e7)), "real": False})
    return out


def is_real(sym):
    bars = load_ohlcv(sym)
    return bool(bars and bars[0].get("real"))


class PointInTime:
    """A cursor that only exposes bars at or before `t`. Reading ahead raises."""
    def __init__(self, bars):
        self._bars = bars
        self.t = -1

    def __len__(self):
        return len(self._bars)

    def advance(self):
        self.t += 1
        return self.t < len(self._bars)

    def closes(self):
        return [b["c"] for b in self._bars[: self.t + 1]]

    def at(self, i):
        if i > self.t:
            raise LookaheadError(f"lookahead: asked for bar {i} while as-of is {self.t}")
        return self._bars[i]

    def future(self, i):
        """Explicit, audited access to a future bar — ONLY for computing realized
        forward returns during evaluation, never inside a signal."""
        return self._bars[i] if i < len(self._bars) else None
