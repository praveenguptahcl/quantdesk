# How Signals Are Generated — The Complete Answer
This document removes all mystery: it walks through **every number** the platform computes on its way from raw prices to an order, and tells you where to *watch it happen live* (Strategy Lab → "LIVE SIGNAL" panel, backed by `GET /api/signals`). The same function (`api/backtest.py::current_signal`) drives the backtests, the live panel, and the paper node — one source of truth, so what you backtest is exactly what trades.

## The pipeline (6 steps)

```
raw daily bars → 1.REGIME → 2.RANK → 3.FILTER → 4.SIZE → 5.RISK → 6.ORDER
```

### Step 0 — Data
Daily OHLCV bars for SPY + 9 sector ETFs live in `data/catalog/*.csv` (provenance shown in the GUI: `real:yahoo` after `scripts/fetch_data.py`, or `synthetic:v1`). The signal needs at least 253 bars of history: 252 for momentum + 1, and 200 for the regime SMA. This is why the platform's warm-up is 380 calendar days (see STRATEGY_GUIDE.md §3 — the #1 parity-failure cause is getting this wrong).

### Step 1 — Regime filter: are we allowed to be in the market at all?
```
risk_on = SPY_close > SMA(SPY_close, 200 days)
```
If false, the target for **every** position is zero — liquidate and stand aside. No exceptions, no discretion. **Why:** long-run evidence that a simple long-term moving-average filter materially cuts drawdowns of trend/momentum portfolios — Faber, *A Quantitative Approach to Tactical Asset Allocation* (Faber, mebfaber.com). In the 2022 real-data window you can watch this: the strategy sat flat through most of the 2022 decline.

### Step 2 — Rank: which sectors have momentum?
For each of the 9 sector ETFs (XLK, XLF, XLE, XLV, XLI, XLP, XLY, XLU, XLB):
```
mom_12_1 = (close_today / close_252d_ago − 1) − (close_today / close_21d_ago − 1)
```
That's the trailing 12-month return **minus** the most recent month ("12-1"). **Why skip the last month:** short-horizon returns mean-revert, contaminating the momentum signal — the 12-1 convention is standard in the academic literature (Asness, Moskowitz & Pedersen, *Value and Momentum Everywhere*, AQR — link below). ETFs are ranked by this number; the top 3 are candidates.

### Step 3 — Filter: is the signal strong enough to act on?
```
confidence = 1 / (1 + e^(−3 · mom_12_1 / 0.08))        # sigmoid; 0.08 ≈ 8% normalizer
enter when: in_top3 AND confidence ≥ 0.50
stay  while: confidence ≥ 0.25 (hysteresis — prevents churn at the boundary)
```
The sigmoid maps momentum onto a 0–1 confidence score: 0% momentum → 0.50, +8% → ~0.95, negative momentum → below 0.50 (blocked). Hysteresis (different entry/exit thresholds) is a standard control-systems idea to stop the strategy flip-flopping when a signal hovers at the threshold.

### Step 4 — Size: how much?
```
weight = confidence × min(10% / realized_vol_20d_annualized, 1.5) / 3
```
Three forces: **confidence scaling** (stronger signal → bigger position), **volatility targeting** (each position sized so it contributes ~10% annualized vol — a calm sector gets more dollars than a violent one, capped at 1.5× to avoid over-levering sleepy assets), and **equal risk budget** (÷3 across the three slots). If total gross exceeds 100%, everything is scaled down proportionally.

### Step 5 — Risk layer (independent of the strategy)
Before any order routes, `risk/limits.yaml` is enforced by `POST /api/orders/validate`: max $10,000 notional per order, max $15,000 per symbol, ±3% price sanity band. You watched this work: the first paper-node run sized for a $1M account and every order was rejected. The strategy cannot override this layer — that's the point.

### Step 6 — Order
`scripts/paper_node.py` compares targets to actual Alpaca positions and submits only the **deltas** as marketable limit orders (±0.2% through the price), clamped to risk limits, dry-run by default. Weekly cadence: Mondays (the backtests rebalance on Monday bars; the node mirrors that).

## Watch it live
- **GUI:** Strategy Lab → the red "LIVE SIGNAL" card shows today's full table: every ETF's 12-month return, 1-month return, 12-1 momentum, realized vol, confidence, vol scalar, and final weight — plus the regime verdict and the resulting decision.
- **API:** `curl http://127.0.0.1:8700/api/signals` returns the same as JSON.
- **To orders:** `python3 scripts/paper_node.py` prints exactly how those weights become share quantities against your real paper account.

## Extending: where YOUR signals plug in
Every strategy in this platform is the same shape — a function from history to target weights. Three ways to add one:
1. **AI Builder** (GUI): describe a strategy in English → structured spec → journal entry.
2. **HFT Library** (GUI): 30 documented archetypes with precise entry/exit rules — add to journal, then implement its `signal` line the same way `current_signal()` implements momentum.
3. **Code**: copy `strategies/lean/momo_etf_v3/main.py` + `strategies/nautilus/momo_etf_v3.py`, change the indicator math, run the dual backtest, pass parity, paper trade. STRATEGY_GUIDE.md is the line-by-line walkthrough.

## References (verified links)
- Faber (2007), *A Quantitative Approach to Tactical Asset Allocation* — https://mebfaber.com/timing-model/
- Asness, Moskowitz, Pedersen (2013), *Value and Momentum Everywhere* — https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere
- Jegadeesh & Titman (1993), momentum's original evidence — https://www.jstor.org/stable/2328882
- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio* — https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- QuantConnect indicator reference — https://www.quantconnect.com/docs/v2/writing-algorithms/indicators/key-concepts
- NautilusTrader strategy docs — https://nautilustrader.io/docs/latest/concepts/strategies/

*Educational material, not investment advice. Momentum strategies have historically suffered sharp crashes (e.g., 2009); the regime filter mitigates but does not eliminate this.*
