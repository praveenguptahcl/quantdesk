# Crafting a Real Strategy: momo-etf-v3, End to End
How to build a strategy that **actually backtests** — with explicit triggers, correct history/warm-up, a regime filter, volatility-targeted position sizing, and a system-confidence score — implemented twice (LEAN and NautilusTrader) so it can pass the parity gate. Every named block here maps 1:1 to a box in the dashboard's **Strategy Lab** screen.

> API note: both platforms evolve. Verify signatures against the LEAN docs and the NautilusTrader version you install (`pip show nautilus_trader`). The *logic* below is the contract; adjust syntax to your installed version.

---

## 1. The strategy in one paragraph

Hold the top-3 of 9 SPDR sector ETFs ranked by **12-1 momentum** (12-month return, skipping the most recent month), but **only when the market regime is risk-on** (SPY above its 200-day SMA). Position size is **volatility-targeted** (10% annualized per position, capped at 1.5× notional scale) and further scaled by a **confidence score** — a sigmoid of the momentum z-score. Rebalance weekly. When the regime flips risk-off, liquidate everything and stand aside.

## 2. The decision chain (what the Strategy Lab visualizes)

```
Data → History/Warm-up → Regime Filter → Trigger → Confidence → Sizing → Risk Layer → Order
```

| Block | Rule (exact) | Chart in Strategy Lab |
|---|---|---|
| History | 380 calendar days warm-up (covers 252d momentum + 200d SMA) | — (anatomy box) |
| Regime filter | risk-on ⇔ SPY close > SMA(SPY, 200) | green/red shading on price chart |
| Trigger (entry) | risk-on AND asset in top-3 by mom₁₂₋₁ AND confidence ≥ 0.50 | ▲ markers |
| Trigger (exit) | confidence < 0.25 (hysteresis) OR drops out of top-3 OR regime flips | ▼ markers |
| Confidence | `conf = sigmoid(3 · mom/0.08) if risk-on else 0` | purple confidence chart |
| Sizing | `weight = conf × min(0.10 / σ₂₀d_ann, 1.5)`, portfolio-capped | blue sizing chart |
| Risk layer | pre-route: price sanity ±3%, max $10k/order — outside the strategy | anatomy box |

Why each piece exists: the **12-1 skip** avoids short-term reversal contaminating momentum; the **regime filter** is the single biggest drawdown reducer for long-only momentum; **hysteresis** (enter ≥0.50, exit <0.25) prevents whipsaw churn at the threshold; **vol targeting** equalizes risk across sectors and de-levers automatically in storms; **confidence scaling** makes position size proportional to signal strength instead of binary.

---

## 3. LEAN implementation (`strategies/lean/momo_etf_v3/main.py`)

```python
from AlgorithmImports import *
import math

class MomoEtfV3(QCAlgorithm):

    # ---- parameters (chosen from the optimization plateau, not the peak) ----
    MOM_LOOKBACK   = 252   # 12 months
    MOM_SKIP       = 21    # skip most recent month ("12-1")
    REGIME_SMA     = 200
    VOL_WINDOW     = 20
    VOL_TARGET     = 0.10  # 10% annualized per position
    VOL_SCALE_CAP  = 1.5
    TOP_N          = 3
    ENTRY_CONF     = 0.50
    EXIT_CONF      = 0.25

    def Initialize(self):
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2025, 12, 31)
        self.SetCash(100_000)
        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)

        tickers = ["XLK","XLF","XLE","XLV","XLI","XLP","XLY","XLU","XLB"]
        self.etfs = [self.AddEquity(t, Resolution.Daily).Symbol for t in tickers]
        self.spy  = self.AddEquity("SPY", Resolution.Daily).Symbol

        # ---- indicators ----
        self.regime_sma = self.SMA(self.spy, self.REGIME_SMA, Resolution.Daily)
        # 12-1 momentum = MOMP(252) - MOMP(21): full-year % move minus last-month % move
        self.mom_long  = {s: self.MOMP(s, self.MOM_LOOKBACK, Resolution.Daily) for s in self.etfs}
        self.mom_short = {s: self.MOMP(s, self.MOM_SKIP,     Resolution.Daily) for s in self.etfs}
        self.vol       = {s: self.STD(s,  self.VOL_WINDOW,   Resolution.Daily) for s in self.etfs}

        # ---- HISTORY: warm-up must cover the LONGEST lookback ----
        # 252 trading days ≈ 365 calendar days; add buffer → 380. Getting this wrong
        # is the #1 cause of parity failures later (see Data & Artifacts screen).
        self.SetWarmUp(timedelta(days=380))

        # weekly rebalance, 30 min after open
        self.Schedule.On(self.DateRules.WeekStart(self.spy),
                         self.TimeRules.AfterMarketOpen(self.spy, 30),
                         self.Rebalance)

        # ---- custom charts: regime, confidence, exposure ----
        for name, series in [("Regime", "RiskOn"), ("Confidence", "MaxConf"), ("Exposure", "Gross")]:
            ch = Chart(name); ch.AddSeries(Series(series, SeriesType.Line, 0)); self.AddChart(ch)

        self.entered = set()   # symbols currently held (for hysteresis)

    # ---------- helpers ----------
    def momentum_12_1(self, s):
        return self.mom_long[s].Current.Value - self.mom_short[s].Current.Value

    def confidence(self, s, risk_on):
        if not risk_on:
            return 0.0
        z = self.momentum_12_1(s) / 8.0     # MOMP is in %, so 8.0 ≈ 8% normalizer
        return 1.0 / (1.0 + math.exp(-3.0 * z))

    def ann_vol(self, s):
        px = self.Securities[s].Price
        if px == 0 or not self.vol[s].IsReady:
            return 0.15
        return max((self.vol[s].Current.Value / px) * math.sqrt(252), 0.02)

    # ---------- the decision chain ----------
    def Rebalance(self):
        if self.IsWarmingUp or not self.regime_sma.IsReady:
            return

        # 1) REGIME FILTER
        risk_on = self.Securities[self.spy].Price > self.regime_sma.Current.Value
        self.Plot("Regime", "RiskOn", 1 if risk_on else 0)

        if not risk_on:
            self.Liquidate()                       # flat in risk-off — no exceptions
            self.entered.clear()
            self.Plot("Exposure", "Gross", 0)
            return

        # 2) TRIGGER: rank by 12-1 momentum
        ranked = sorted(self.etfs, key=self.momentum_12_1, reverse=True)
        top = set(ranked[:self.TOP_N])

        targets, max_conf = {}, 0.0
        for s in self.etfs:
            conf = self.confidence(s, risk_on)
            max_conf = max(max_conf, conf)
            in_pos = s in self.entered

            # 3) CONFIDENCE with hysteresis: enter ≥ 0.50, hold until < 0.25
            should_hold = (s in top) and (conf >= (self.EXIT_CONF if in_pos else self.ENTRY_CONF))

            if should_hold:
                # 4) POSITION SIZING: vol target × confidence
                scale  = min(self.VOL_TARGET / self.ann_vol(s), self.VOL_SCALE_CAP)
                targets[s] = conf * scale / self.TOP_N
                self.entered.add(s)
            else:
                targets[s] = 0.0
                self.entered.discard(s)

        # portfolio cap: gross ≤ 100%
        gross = sum(targets.values())
        if gross > 1.0:
            targets = {s: w / gross for s, w in targets.items()}

        # 5) ORDERS (LEAN's fee/slippage model applies; risk layer is Nautilus-side live)
        self.SetHoldings([PortfolioTarget(s, w) for s, w in targets.items()])
        self.Plot("Confidence", "MaxConf", max_conf)
        self.Plot("Exposure",  "Gross",   sum(targets.values()))
```

Run it:
```bash
lean project-create "momo-etf-v3" --language python   # then paste main.py
lean backtest "momo-etf-v3"
```
The result folder contains the tearsheet JSON (Sharpe, DD, trades — what QuantDesk's Backtest screen parses) and your three custom charts (**Regime**, **Confidence**, **Exposure**) — the same series the Strategy Lab renders.

---

## 4. NautilusTrader port (`strategies/nautilus/momo_etf_v3.py`)

The port re-implements the identical chain over Nautilus's event model. Key mapping:

| LEAN | Nautilus |
|---|---|
| `Initialize` | `on_start` (subscribe + **request historical bars for warm-up**) |
| `Schedule.On(WeekStart…)` | `clock.set_timer` weekly |
| `self.SMA/MOMP/STD` (auto-fed) | indicators registered via `register_indicator_for_bars` (or manual deques) |
| `SetHoldings(target %)` | compute qty from account equity, submit market orders |
| `SetWarmUp` | `request_bars(...)` — **must replay the same 380 days** |

```python
import math
from collections import deque
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy


class MomoEtfV3Config(StrategyConfig, frozen=True):
    etf_bar_types: list[str]      # e.g. ["XLK.XNAS-1-DAY-LAST-EXTERNAL", ...]
    spy_bar_type: str
    mom_lookback: int = 252
    mom_skip: int = 21
    regime_sma: int = 200
    vol_window: int = 20
    vol_target: float = 0.10
    vol_scale_cap: float = 1.5
    top_n: int = 3
    entry_conf: float = 0.50
    exit_conf: float = 0.25


class MomoEtfV3(Strategy):
    def __init__(self, config: MomoEtfV3Config):
        super().__init__(config)
        self.spy_bt  = BarType.from_str(config.spy_bar_type)
        self.etf_bts = [BarType.from_str(s) for s in config.etf_bar_types]
        n = config.mom_lookback + 5
        self.px   = {bt.instrument_id: deque(maxlen=n) for bt in self.etf_bts}
        self.spy_px = deque(maxlen=config.regime_sma + 5)
        self.entered: set[InstrumentId] = set()

    # ---------- lifecycle ----------
    def on_start(self):
        # HISTORY: request warm-up bars BEFORE live subscription — the parity-critical step.
        # Same 380 days LEAN warmed up with; indicators must be primed identically.
        for bt in [self.spy_bt, *self.etf_bts]:
            self.request_bars(bt)          # replays catalog/adapter history into on_historical_data
            self.subscribe_bars(bt)
        # weekly rebalance timer (Mon 10:00 ET expressed in your clock's tz handling)
        self.clock.set_timer(name="rebalance", interval=pd.Timedelta(days=7),
                             callback=self.on_rebalance_timer)

    def on_historical_data(self, data):      # warm-up replay lands here
        for bar in data:
            self._store(bar)

    def on_bar(self, bar: Bar):
        self._store(bar)

    def _store(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        if bar.bar_type == self.spy_bt:
            self.spy_px.append(float(bar.close))
        elif iid in self.px:
            self.px[iid].append(float(bar.close))

    # ---------- identical decision chain ----------
    def _mom_12_1(self, iid):
        p = self.px[iid]
        if len(p) < self.config.mom_lookback:
            return None
        full  = p[-1] / p[-self.config.mom_lookback] - 1
        short = p[-1] / p[-self.config.mom_skip] - 1
        return (full - short) * 100.0          # percent, matching LEAN's MOMP

    def _confidence(self, iid, risk_on):
        if not risk_on:
            return 0.0
        m = self._mom_12_1(iid)
        if m is None:
            return 0.0
        return 1.0 / (1.0 + math.exp(-3.0 * (m / 8.0)))

    def _ann_vol(self, iid):
        p = list(self.px[iid])[-(self.config.vol_window + 1):]
        if len(p) < self.config.vol_window + 1:
            return 0.15
        rets = [p[i] / p[i - 1] - 1 for i in range(1, len(p))]
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
        return max(sd * math.sqrt(252), 0.02)

    def on_rebalance_timer(self, event):
        if len(self.spy_px) < self.config.regime_sma:
            return                                              # still warming up

        # 1) REGIME
        sma = sum(list(self.spy_px)[-self.config.regime_sma:]) / self.config.regime_sma
        risk_on = self.spy_px[-1] > sma
        if not risk_on:
            self.close_all_positions()                          # flat in risk-off
            self.entered.clear()
            return

        # 2) TRIGGER: rank
        moms = {iid: self._mom_12_1(iid) for iid in self.px}
        ranked = sorted((i for i, m in moms.items() if m is not None),
                        key=lambda i: moms[i], reverse=True)
        top = set(ranked[: self.config.top_n])

        equity = float(self.portfolio.account(self.etf_bts[0].instrument_id.venue)
                       .balance_total().as_double())

        targets = {}
        for iid in self.px:
            conf = self._confidence(iid, risk_on)
            in_pos = iid in self.entered
            threshold = self.config.exit_conf if in_pos else self.config.entry_conf
            if iid in top and conf >= threshold:
                scale = min(self.config.vol_target / self._ann_vol(iid),
                            self.config.vol_scale_cap)
                targets[iid] = conf * scale / self.config.top_n   # 3) + 4) conf × vol sizing
                self.entered.add(iid)
            else:
                targets[iid] = 0.0
                self.entered.discard(iid)

        gross = sum(targets.values())
        if gross > 1.0:
            targets = {i: w / gross for i, w in targets.items()}

        # 5) ORDERS: convert target weight → qty delta, submit market orders.
        for iid, w in targets.items():
            instrument = self.cache.instrument(iid)
            price = self.px[iid][-1]
            target_qty = int((equity * w) / price)
            current = self.portfolio.net_position(iid)
            delta = target_qty - int(current)
            if delta == 0:
                continue
            order = self.order_factory.market(
                instrument_id=iid,
                order_side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
                quantity=instrument.make_qty(abs(delta)),
            )
            self.submit_order(order)   # Nautilus RiskEngine validates pre-route (Phase 7 layer)
```

Backtest it against the **same window and same data** (exported to the `ParquetDataCatalog` — see the Data & Artifacts screen):

```python
# scripts/backtest_nautilus.py (sketch)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (BacktestRunConfig, BacktestVenueConfig,
                                    BacktestDataConfig, BacktestEngineConfig,
                                    ImportableStrategyConfig)

run = BacktestRunConfig(
    engine=BacktestEngineConfig(strategies=[ImportableStrategyConfig(
        strategy_path="strategies.nautilus.momo_etf_v3:MomoEtfV3",
        config_path="strategies.nautilus.momo_etf_v3:MomoEtfV3Config",
        config={"etf_bar_types": [...], "spy_bar_type": "SPY.XNAS-1-DAY-LAST-EXTERNAL"},
    )]),
    venues=[BacktestVenueConfig(name="XNAS", oms_type="NETTING",
            account_type="MARGIN", starting_balances=["100000 USD"])],
    data=[BacktestDataConfig(catalog_path="data/catalog", data_cls=Bar, ...)],
)
BacktestNode(configs=[run]).run()
```

---

## 5. The five parity traps (why ports fail the gate)

1. **Warm-up length** — LEAN's `SetWarmUp(380d)` vs Nautilus's `request_bars` depth. One bar short → first signals differ → every trade after diverges. (This is exactly the seeded failure in `pairs-stat-v1` on the parity screen.)
2. **MOMP semantics** — LEAN's `MOMP(252)` is percent; a naive `p[-1]/p[-252]-1` is a fraction. The port above multiplies by 100 to match. Unit mismatches silently corrupt the confidence sigmoid.
3. **Bar timestamps** — LEAN daily bars stamp at midnight-after-close; Nautilus external bars stamp per catalog convention. An off-by-one-day means Monday's rebalance sees Friday's bar in one engine, Thursday's in the other.
4. **Fee/slippage models** — LEAN's IB fee model vs Nautilus venue config. Set both explicitly; never rely on defaults.
5. **Order timing** — `SetHoldings` at 10:00 fills that bar in LEAN; a Nautilus market order fills the *next* tick/bar. On daily bars, align by rebalancing on bar close or accept a 1-bar tolerance in the parity thresholds.

## 6. Workflow summary

1. Prototype the signal in `lean research` (QuantBook) — verify momentum spread between top-3 and bottom-3 is positive and significant before writing any algorithm code.
2. Implement the LEAN algo above → `lean backtest` → check the tearsheet + your Regime/Confidence/Exposure charts.
3. Sweep parameters (`lean optimize`), pick the **plateau center** (see Optimize & WF screen), freeze in git.
4. Walk-forward validate — Phase 3 checkpoint.
5. Export the window to the catalog, run the Nautilus port, reconcile on the Backtest & Parity screen — Phase 4 hard gate.
6. Paper trade; the Strategy Lab screen then shows this exact decision chain running live.
