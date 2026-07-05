# The Regime Filter — Complete Logic
The regime filter is the strategy's master switch: it decides whether the system is allowed to hold *anything*. This document is the full specification — definition, exact algorithm, anti-whipsaw logic, edge cases, measured impact on real data, and the alternatives you can test yourself in the Strategy Lab's what-if editor.

## 1. Definition
The market is in one of two states:
- **RISK-ON** — the strategy may hold its top-momentum positions.
- **RISK-OFF** — the strategy liquidates everything and holds cash. No exceptions, no discretion, no partial positions.

The state is derived from one benchmark (SPY, as the broad-market proxy) so that all nine sector positions share a single market-level gate. Per-asset regimes are a valid alternative (see §6) but multiply whipsaw.

## 2. Exact algorithm (as implemented in `api/backtest.py::signal_history`)
```
inputs:  SPY daily closes, N = 200 (SMA length), band = 0% (anti-whipsaw, optional)

each day t:
  SMA_t = mean(close[t-N+1 .. t])                  # simple moving average
  if close_t > SMA_t × (1 + band):   state ← RISK-ON
  elif close_t < SMA_t × (1 − band): state ← RISK-OFF
  else:                              state ← unchanged (hysteresis zone)

first N−1 days: state = RISK-OFF (SMA undefined — never trade blind warm-up)
```
With `band = 0` this reduces to the classic rule `risk_on = close > SMA200`. The **band** creates a neutral zone around the SMA: price must cross *decisively* to flip the state, which is the standard cure for whipsaw (rapid ON/OFF/ON flips when price hugs the SMA). Try `band = 1–2%` in the Strategy Lab's parameter editor and watch the flip count in the Regime Impact card drop.

## 3. When it acts
The regime is *evaluated* daily but *acted on* weekly: the Monday rebalance reads the state and either computes targets (ON) or liquidates (OFF). Timing chain: Monday's close data → decision → paper node executes ~09:35 AM ET the next session. This lag is deliberate — trading the open on yesterday's close is reproducible in backtests; intraday regime reactions are not (for a daily-bar strategy).

## 4. Why it exists (evidence)
Faber (2007, updated 2013) showed that a simple 10-month (~200-day) SMA timing rule on US equities kept most of buy-and-hold's return while cutting maximum drawdown roughly in half over 1901–2012 — https://mebfaber.com/timing-model/. Momentum portfolios specifically are prone to violent crashes when markets rebound off bottoms (momentum was short the recovery in 2009); a market-level trend gate reduces exposure in exactly those windows. The trade-off is honest: trend filters lag. You give back the first leg of every recovery and suffer whipsaw in sideways markets — the filter buys crash protection with those costs.

## 5. Measured impact on this platform's real data
Open Strategy Lab → Regime Impact card (computed live by `/api/signals/history`). On the 2022→2026 Yahoo window you can verify: the strategy sat RISK-OFF through most of 2022's decline (the "SPY move while OFF" stat shows what the filter dodged — negative means avoided losses), then re-entered in 2023. The flip log lists every transition with its date; the ◆ markers on the regime chart show them in context.

## 6. Alternatives (test them as what-ifs)
| Variant | How | Trade-off |
|---|---|---|
| Longer/shorter SMA | change "Regime SMA" (e.g., 150/250) | shorter = faster exits, more whipsaw |
| Hysteresis band | "Regime band %" = 1–2 | fewer flips, later entries/exits |
| Dual momentum | SPY 12-month return > 0 instead of SMA (Antonacci) | similar spirit; different lag profile |
| Volatility regime | OFF when realized vol > threshold | catches vol spikes SMA misses; different failure modes |
| Per-asset regime | each ETF gated by its own SMA | finer control, ~9× the whipsaw events |
The what-if editor covers the first two directly; the others are one-function changes in `signal_history` — a good Week-7 exercise from the learning path.

## 7. Edge cases the implementation handles
Warm-up: no state until N bars exist (defaults OFF — never trade on an undefined signal). Data gaps: missing days simply don't update the SMA window (CSV rows are trading days only). Flip on rebalance day: the Monday decision uses that Monday's close-state — regime and rebalance can never disagree within a bar. Mid-week flips: noted daily in the state series but only *acted on* at the next Monday rebalance; a mid-week crash therefore hits the current holdings until Monday — this is a deliberate simplicity/robustness trade-off, and adding a daily "emergency exit on regime flip" is another good student exercise (it changes turnover materially; backtest both).

## 8. What the regime does NOT do
It does not protect against overnight gaps, single-stock (sector) blowups while the market is fine, or fast crashes between rebalances (see §7). It is not a volatility forecast. The circuit breakers in `risk/limits.yaml` (daily loss halt, max-DD halt) exist precisely because the regime filter's protection is slow — two layers, different time scales.

*References: Faber — https://mebfaber.com/timing-model/ · Antonacci's dual momentum summary — https://www.optimalmomentum.com/ · this platform's implementation: `api/backtest.py` (`signal_history`, `current_signal`) and docs/SIGNALS.md Step 1.*
