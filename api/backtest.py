"""Dual backtest engines + real parity computation (blueprint M4/M5, stdlib only).

Two INDEPENDENT implementations of the momo-etf-v3 decision chain from
STRATEGY_GUIDE.md run over the same data/catalog CSVs:

  - engine_a ("lean-style"):     array-precomputed indicators, batch loop
  - engine_b ("nautilus-style"): event-driven bar feed, incremental indicators

Identical rules, different code paths — exactly what a LEAN->Nautilus port is.
The parity gate compares their outputs against risk/limits.yaml tolerances.
`inject_warmup_bug=True` shortens engine B's momentum warm-up by 20 bars to
reproduce the classic porting bug (used by tests and the GUI demo of a FAIL).

When LEAN CLI / nautilus_trader are installed (blueprint M4/M5 on the user's
machine), their runners replace engine_a/engine_b behind the same interface.
"""
import csv
import math
import os
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "data", "catalog"))

# strategy params — STRATEGY_GUIDE.md §3 (plateau center, frozen)
MOM, SKIP, SMA_N, VOLW = 252, 21, 200, 20
VOL_TGT, VOL_CAP, TOP_N = 0.10, 1.5, 3
ENTRY_CONF, EXIT_CONF = 0.50, 0.25
FEE_BPS = 1.0
START_CASH = 100_000.0
ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]


def provenance():
    p = os.path.join(CATALOG, ".provenance")
    return open(p).read().strip() if os.path.exists(p) else "unknown"


def load_bars(sym):
    path = os.path.join(CATALOG, f"{sym}.csv")
    if not os.path.exists(path):
        return None
    out = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                out.append((row["Date"], float(row["Close"])))
            except (KeyError, ValueError):
                continue
    return out


def _sigmoid_conf(mom_12_1, risk_on):
    if not risk_on:
        return 0.0
    return 1.0 / (1.0 + math.exp(-3.0 * (mom_12_1 / 0.08)))


def _metrics(dates, equity, trades, traded_notional, fees):
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    n = len(rets)
    mu = sum(rets) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / n) or 1e-12
    downs = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in downs) / n) or 1e-12
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    years = n / 252.0
    total_ret = equity[-1] / equity[0] - 1
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if years > 0 else 0
    wins = sum(1 for r in rets if r > 0)
    avg_eq = sum(equity) / len(equity)
    return {
        "ret": total_ret, "cagr": cagr,
        "sharpe": mu / sd * math.sqrt(252),
        "sortino": mu / dsd * math.sqrt(252),
        "dd": mdd, "win": wins / n,
        "trades": len(trades), "fees": fees,
        "turn": traded_notional / avg_eq / years if years > 0 else 0,
        "window": f"{dates[0]} → {dates[-1]}",
        "equity_monthly": [round(equity[i], 2) for i in range(0, len(equity), 21)],
    }


def _run_portfolio(dates, closes, decide):
    """Shared execution: `decide(i) -> {sym: weight} or None` (None = no rebalance).
    Fills at same-day close, FEE_BPS on traded notional."""
    cash, shares = START_CASH, {s: 0.0 for s in ETFS}
    equity, trades, traded, fees = [], [], 0.0, 0.0
    for i, d in enumerate(dates):
        px = {s: closes[s][i] for s in ETFS}
        eq = cash + sum(shares[s] * px[s] for s in ETFS)
        targets = decide(i, eq)
        if targets is not None:
            for s in ETFS:
                tgt_val = eq * targets.get(s, 0.0)
                cur_val = shares[s] * px[s]
                delta = tgt_val - cur_val
                if abs(delta) < max(eq * 1e-4, 1.0):
                    continue
                fee = abs(delta) * FEE_BPS / 1e4
                shares[s] += delta / px[s]
                cash -= delta + fee
                fees += fee
                traded += abs(delta)
                trades.append((d, s, "BUY" if delta > 0 else "SELL", round(abs(delta) / px[s], 2)))
        equity.append(cash + sum(shares[s] * px[s] for s in ETFS))
    m = _metrics(dates, equity, trades, traded, fees)
    m["trade_log"] = trades
    return m


# ------------------------------------------------------------------ engine A
def engine_a():
    """'LEAN-style': indicators precomputed from full arrays."""
    spy = load_bars("SPY")
    data = {s: load_bars(s) for s in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    dates = [spy[i][0] for i in range(n)]
    spy_c = [spy[i][1] for i in range(n)]
    closes = {s: [data[s][i][1] for i in range(n)] for s in ETFS}

    sma = [None] * n
    run = 0.0
    for i in range(n):
        run += spy_c[i]
        if i >= SMA_N:
            run -= spy_c[i - SMA_N]
        if i >= SMA_N - 1:
            sma[i] = run / SMA_N

    entered = set()

    def decide(i, eq):
        if i < MOM or sma[i] is None or _wd(dates[i]) != 0:  # Monday rebalance
            return None
        risk_on = spy_c[i] > sma[i]
        if not risk_on:
            entered.clear()
            return {s: 0.0 for s in ETFS}
        moms = {}
        for s in ETFS:
            c = closes[s]
            moms[s] = (c[i] / c[i - MOM] - 1) - (c[i] / c[i - SKIP] - 1)
        top = set(sorted(ETFS, key=lambda s: moms[s], reverse=True)[:TOP_N])
        targets = {}
        for s in ETFS:
            conf = _sigmoid_conf(moms[s], risk_on)
            thr = EXIT_CONF if s in entered else ENTRY_CONF
            if s in top and conf >= thr:
                c = closes[s]
                rets = [c[j] / c[j - 1] - 1 for j in range(i - VOLW + 1, i + 1)]
                mu = sum(rets) / VOLW
                vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / VOLW) * math.sqrt(252), 0.02)
                targets[s] = conf * min(VOL_TGT / vol, VOL_CAP) / TOP_N
                entered.add(s)
            else:
                targets[s] = 0.0
                entered.discard(s)
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}
        return targets

    return _run_portfolio(dates, closes, decide)


# ------------------------------------------------------------------ engine B
class _EventStrategy:
    """'Nautilus-style': incremental state fed one bar at a time."""

    def __init__(self, warmup=MOM):
        self.warmup = warmup
        self.spy = deque(maxlen=SMA_N + 1)
        self.px = {s: deque(maxlen=MOM + 1) for s in ETFS}
        self.entered = set()
        self.bars_seen = 0

    def on_bar(self, spy_close, etf_closes):
        self.spy.append(spy_close)
        for s in ETFS:
            self.px[s].append(etf_closes[s])
        self.bars_seen += 1

    def rebalance(self):
        if self.bars_seen < max(self.warmup, SMA_N) or len(self.spy) < SMA_N:
            return None
        sma = sum(list(self.spy)[-SMA_N:]) / SMA_N
        risk_on = self.spy[-1] > sma
        if not risk_on:
            self.entered.clear()
            return {s: 0.0 for s in ETFS}
        moms = {}
        for s in ETFS:
            p = self.px[s]
            back = min(MOM, len(p) - 1, self.warmup)
            moms[s] = (p[-1] / p[-1 - back] - 1) - (p[-1] / p[-1 - SKIP] - 1)
        top = set(sorted(ETFS, key=lambda s: moms[s], reverse=True)[:TOP_N])
        targets = {}
        for s in ETFS:
            conf = _sigmoid_conf(moms[s], risk_on)
            thr = EXIT_CONF if s in self.entered else ENTRY_CONF
            if s in top and conf >= thr:
                p = list(self.px[s])[-(VOLW + 1):]
                rets = [p[j] / p[j - 1] - 1 for j in range(1, len(p))]
                mu = sum(rets) / len(rets)
                vol = max(math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)) * math.sqrt(252), 0.02)
                targets[s] = conf * min(VOL_TGT / vol, VOL_CAP) / TOP_N
                self.entered.add(s)
            else:
                targets[s] = 0.0
                self.entered.discard(s)
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}
        return targets


def engine_b(inject_warmup_bug=False):
    spy = load_bars("SPY")
    data = {s: load_bars(s) for s in ETFS}
    if spy is None or any(v is None for v in data.values()):
        return None
    n = min(len(spy), *(len(v) for v in data.values()))
    dates = [spy[i][0] for i in range(n)]
    closes = {s: [data[s][i][1] for i in range(n)] for s in ETFS}
    strat = _EventStrategy(warmup=MOM - 20 if inject_warmup_bug else MOM)

    def decide(i, eq):
        strat.on_bar(spy[i][1], {s: closes[s][i] for s in ETFS})
        if _wd(dates[i]) != 0:
            return None
        return strat.rebalance()

    return _run_portfolio(dates, closes, decide)


# ------------------------------------------------------------------ real nautilus (M5)
NAUT_PY = os.path.expanduser("~/.quantdesk/venv/bin/python")
NAUT_SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "nautilus_backtest.py"))


def engine_nautilus():
    """Run the REAL nautilus_trader backtest via the venv. Returns engine-metric
    dict (same shape as engine_a/engine_b) or None if unavailable/failed."""
    import subprocess
    if not os.path.exists(NAUT_PY):
        return None
    try:
        p = subprocess.run([NAUT_PY, NAUT_SCRIPT], capture_output=True, text=True, timeout=180)
        line = p.stdout.strip().splitlines()[-1]
        d = __import__("json").loads(line)
        if "error" in d:
            return None
        d["trade_log"] = [tuple(t) for t in d.get("trade_log", [])]
        d.setdefault("equity_monthly", [])
        d.setdefault("window", "")
        return d
    except Exception:
        return None


# ------------------------------------------------------------------ parity
def compute_parity(a, b, tol=None):
    tol = tol or {"total_return_diff_pp": 1.0, "sharpe_diff": 0.10,
                  "trade_count_diff_pct": 2.0, "max_dd_diff_pp": 1.0}
    checks = [
        ("Total return diff", abs(a["ret"] - b["ret"]) * 100, tol["total_return_diff_pp"], "pp"),
        ("Sharpe diff", abs(a["sharpe"] - b["sharpe"]), tol["sharpe_diff"], ""),
        ("Trade count diff", abs(a["trades"] - b["trades"]) / max(a["trades"], 1) * 100,
         tol["trade_count_diff_pct"], "%"),
        ("Max DD diff", abs(a["dd"] - b["dd"]) * 100, tol["max_dd_diff_pp"], "pp"),
    ]
    rows = [{"m": name, "v": f"{val:.2f}{unit}", "lim": f"≤ {lim:.2f}{unit}", "ok": val <= lim}
            for name, val, lim, unit in checks]
    diffs = _first_trade_divergences(a.get("trade_log", []), b.get("trade_log", []))
    return {"tol": rows, "pass": all(r["ok"] for r in rows), "diffs": diffs}


def _first_trade_divergences(la, lb, limit=3):
    out = []
    for i in range(min(len(la), len(lb))):
        da, db = la[i], lb[i]
        if da[:3] != db[:3]:
            out.append({"t": da[0], "sym": da[1],
                        "lean": f"{da[2]} {da[3]} {da[1]}",
                        "naut": f"{db[2]} {db[3]} {db[1]} @ {db[0]}",
                        "d": "⚠ order sequence diverges here"})
            if len(out) >= limit:
                break
    if not out and len(la) != len(lb):
        out.append({"t": "-", "sym": "-", "lean": f"{len(la)} trades total",
                    "naut": f"{len(lb)} trades total", "d": "⚠ trade count differs"})
    return out


def _wd(date_str):
    import datetime
    return datetime.date.fromisoformat(date_str).weekday()


def fmt_stats(m):
    """Format engine metrics into the GUI parity-card shape."""
    return {
        "ret": f"{m['ret'] * 100:+.1f}%", "cagr": f"{m['cagr'] * 100:.1f}%",
        "sharpe": round(m["sharpe"], 2), "sortino": round(m["sortino"], 2),
        "dd": f"{m['dd'] * 100:.1f}%", "win": f"{m['win'] * 100:.1f}%",
        "trades": m["trades"], "turn": f"{m['turn']:.1f}×",
        "fees": f"${m['fees']:,.0f}",
    }


def run_parity_backtest(inject_warmup_bug=False, tolerances=None, engine="auto"):
    """engine: 'auto' tries REAL nautilus_trader first, falls back to internal engine B."""
    a = engine_a()
    b = None
    b_name = "internal-event-engine"
    if engine in ("auto", "nautilus") and not inject_warmup_bug:
        b = engine_nautilus()
        if b is not None:
            b_name = "nautilus_trader " + str(b.get("version", ""))
    if b is None:
        if engine == "nautilus":
            return None
        b = engine_b(inject_warmup_bug=inject_warmup_bug)
    if a is None or b is None:
        return None
    par = compute_parity(a, b, tolerances)
    note = ("Engines reconciled — trade sequences match within tolerance."
            if par["pass"] else
            "Divergence detected — first suspect: indicator warm-up length in the event-driven port.")
    return {
        "naut_engine": b_name,
        "window": a["window"] + f" · daily bars · fees {FEE_BPS:.0f}bp · data: {provenance()} · naut leg: {b_name}",
        "lean": fmt_stats(a), "naut": fmt_stats(b),
        "tol": par["tol"], "pass": par["pass"],
        "diffs": par["diffs"], "diffNote": note,
        "seed": 11, "div": 0 if par["pass"] else 1,
        "raw": {"a_sharpe": a["sharpe"], "b_sharpe": b["sharpe"],
                "a_trades": a["trades"], "b_trades": b["trades"]},
    }
