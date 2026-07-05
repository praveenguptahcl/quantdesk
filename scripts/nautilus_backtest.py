#!/usr/bin/env python3
"""REAL NautilusTrader backtest of momo-etf-v3 over data/catalog CSVs (M5).

Run with the venv python:  ~/.quantdesk/venv/bin/python scripts/nautilus_backtest.py
Prints a single JSON object with the same metric shape as api/backtest.py engines,
so the server can slot it straight into the parity gate as the "naut" leg.
"""
import csv
import json
import math
import os
import sys

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "data", "catalog"))
VENUE = Venue("XNAS")
ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB"]
ALL = ["SPY"] + ETFS

# identical params to api/backtest.py (STRATEGY_GUIDE plateau center)
MOM, SKIP, SMA_N, VOLW = 252, 21, 200, 20
VOL_TGT, VOL_CAP, TOP_N = 0.10, 1.5, 3
ENTRY_CONF, EXIT_CONF = 0.50, 0.25
START_CASH = 100_000.0


def load_rows(sym):
    out = []
    with open(os.path.join(CATALOG, f"{sym}.csv")) as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["Date"], float(r["Open"]), float(r["High"]),
                            float(r["Low"]), float(r["Close"]), float(r["Volume"] or 0)))
            except (KeyError, ValueError):
                continue
    return out


class MomoConfig(StrategyConfig, frozen=True):
    pass


class MomoEtfV3(Strategy):
    def __init__(self, config=None):
        super().__init__(config)
        self.px = {s: [] for s in ALL}
        self.instruments = {}
        self.entered = set()
        self.equity_curve = []
        self.n_trades = 0
        self.traded_notional = 0.0
        self.trade_log = []
        self.dates = []
        # own ledger from actual FILLS (independent of account model internals)
        self.cash = START_CASH
        self.pos = {s: 0 for s in ETFS}
        self.fills = 0
        self.rejects = []

    def on_start(self):
        for s in ALL:
            inst = self.cache.instrument(self.instruments[s])
            bt = BarType.from_str(f"{s}.XNAS-1-DAY-LAST-EXTERNAL")
            self.subscribe_bars(bt)

    def on_bar(self, bar: Bar):
        sym = bar.bar_type.instrument_id.symbol.value
        self.px[sym].append(float(bar.close))
        if sym != "XLB":          # XLB is the last bar delivered per day
            return
        import datetime
        dt = datetime.datetime.fromtimestamp(bar.ts_event / 1e9, tz=datetime.timezone.utc)
        self.dates.append(dt.date().isoformat())
        self._mark_equity()
        if dt.weekday() == 0:
            self._rebalance()

    # ---- fills -> own ledger ----
    def on_order_filled(self, event):
        sym = event.instrument_id.symbol.value
        qty = float(event.last_qty)
        px = float(event.last_px)
        side = 1 if str(event.order_side) == "OrderSide.BUY" or getattr(event.order_side, "name", "") == "BUY" else -1
        self.cash -= side * qty * px
        self.pos[sym] = self.pos.get(sym, 0) + side * qty
        self.fills += 1

    def on_order_rejected(self, event):
        if len(self.rejects) < 5:
            self.rejects.append(str(getattr(event, "reason", event))[:120])

    def on_order_denied(self, event):
        if len(self.rejects) < 5:
            self.rejects.append("DENIED: " + str(getattr(event, "reason", event))[:110])

    def _mark_equity(self):
        pos_val = sum(self.pos.get(s, 0) * (self.px[s][-1] if self.px[s] else 0) for s in ETFS)
        self.equity_curve.append(self.cash + pos_val)

    def _rebalance(self):
        spy = self.px["SPY"]
        if len(spy) < max(MOM, SMA_N) + 1:
            return
        sma = sum(spy[-SMA_N:]) / SMA_N
        risk_on = spy[-1] > sma
        eq = self.equity_curve[-1] if self.equity_curve else START_CASH
        targets = {}
        if not risk_on:
            self.entered.clear()
            targets = {s: 0.0 for s in ETFS}
        else:
            moms = {}
            for s in ETFS:
                p = self.px[s]
                if len(p) < MOM + 1:
                    return
                moms[s] = (p[-1] / p[-1 - MOM] - 1) - (p[-1] / p[-1 - SKIP] - 1)
            top = set(sorted(ETFS, key=lambda x: moms[x], reverse=True)[:TOP_N])
            for s in ETFS:
                conf = 0.0 if not risk_on else 1.0 / (1.0 + math.exp(-3.0 * (moms[s] / 0.08)))
                thr = EXIT_CONF if s in self.entered else ENTRY_CONF
                if s in top and conf >= thr:
                    p = self.px[s][-(VOLW + 1):]
                    rets = [p[i] / p[i - 1] - 1 for i in range(1, len(p))]
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
        # convert to orders
        for s, w in targets.items():
            price = self.px[s][-1] if self.px[s] else 0
            if price <= 0:
                continue
            target_qty = int((eq * w) / price)
            current = int(self.pos.get(s, 0))
            delta = target_qty - current
            if delta == 0:
                continue
            inst = self.cache.instrument(self.instruments[s])
            order = self.order_factory.market(
                instrument_id=self.instruments[s],
                order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=inst.make_qty(abs(delta)),
            )
            self.submit_order(order)
            self.n_trades += 1
            self.traded_notional += abs(delta) * price
            self.trade_log.append((self.dates[-1][:10], s, "BUY" if delta > 0 else "SELL", abs(delta)))


def to_bars(sym, rows, instrument):
    import pandas as pd
    bt = BarType.from_str(f"{sym}.XNAS-1-DAY-LAST-EXTERNAL")
    bars = []
    for d, o, h, l, c, v in rows:
        ts = int(pd.Timestamp(d, tz="UTC").value)
        bars.append(Bar(
            bar_type=bt,
            open=Price(o, instrument.price_precision),
            high=Price(h, instrument.price_precision),
            low=Price(l, instrument.price_precision),
            close=Price(c, instrument.price_precision),
            volume=Quantity(max(v, 1), instrument.size_precision),
            ts_event=ts, ts_init=ts,
        ))
    return bars


def main():
    data = {s: load_rows(s) for s in ALL}
    n = min(len(v) for v in data.values())
    # align: keep only dates present in all series
    common = set.intersection(*(set(r[0] for r in v) for v in data.values()))
    data = {s: [r for r in v if r[0] in common] for s, v in data.items()}

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("QD-001"),
        logging=LoggingConfig(log_level="ERROR", print_config=False),
    ))
    engine.add_venue(venue=VENUE, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USD, starting_balances=[Money(START_CASH, USD)])
    strat = MomoEtfV3(MomoConfig())
    for s in ALL:
        inst = TestInstrumentProvider.equity(s, "XNAS")
        engine.add_instrument(inst)
        strat.instruments[s] = inst.id
    all_bars = []
    for s in ALL:
        all_bars += to_bars(s, data[s], engine.cache.instrument(strat.instruments[s]))
    all_bars.sort(key=lambda b: b.ts_init)
    engine.add_data(all_bars)
    engine.add_strategy(strat)
    engine.run()

    eq = strat.equity_curve
    if len(eq) < 50:
        print(json.dumps({"error": f"too few equity points: {len(eq)}"}))
        return
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    nn = len(rets)
    mu = sum(rets) / nn
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / nn) or 1e-12
    downs = [r for r in rets if r < 0]
    dsd = math.sqrt(sum(r * r for r in downs) / nn) or 1e-12
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    years = nn / 252.0
    result = {
        "engine": "nautilus_trader",
        "version": __import__("nautilus_trader").__version__,
        "ret": eq[-1] / eq[0] - 1,
        "cagr": (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 else 0,
        "sharpe": mu / sd * math.sqrt(252),
        "sortino": mu / dsd * math.sqrt(252),
        "dd": mdd, "win": sum(1 for r in rets if r > 0) / nn,
        "trades": strat.n_trades,
        "fills": strat.fills,
        "rejects": strat.rejects,
        "turn": strat.traded_notional / (sum(eq) / len(eq)) / years if years else 0,
        "fees": 0.0,  # fee model config TBD; engines A/B use 1bp — note in parity
        "bars": len(eq),
        "trade_log": strat.trade_log[:2000],
    }
    engine.dispose()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
