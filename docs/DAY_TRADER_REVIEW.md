# Day Trader's Audit — Every Aspect, Honestly Scored
The platform reviewed the way a working day trader would actually judge it, aspect by aspect. ✅ works today (verified) · 🟡 works with caveats · ❌ not there. No sales talk.

| # | Aspect | Verdict | Evidence / caveat |
|---|---|---|---|
| 1 | **Do I know where signals come from?** | ✅ | LIVE SIGNAL panel + `/api/signals` show every intermediate number; docs/SIGNALS.md derives each formula with citations. Nothing is a black box. |
| 2 | **Is the dashboard truthful?** | ✅ | Equity/positions/orders/P&L calendar read the real Alpaca paper account, labeled "LIVE"; demo data is labeled "demo". Verified: your real $999,999 / 0 positions / canceled test order all display. |
| 3 | **Can I actually place trades?** | ✅ (paper) | Phase 5 checkpoint passed: order placed→confirmed→canceled in 314ms on the real paper API. Node rebalances with `--execute`. Live trading deliberately locked behind M8 wizard. |
| 4 | **Will it stop me from blowing up?** | ✅ | Pre-route risk layer (demonstrated rejecting oversized orders), kill switch flattens the real account, breach simulation, daily-loss/DD limits in `risk/limits.yaml`. Caveat: breakers monitor paper P&L only until fills accumulate. |
| 5 | **Intraday data for actual day trading?** | 🟡 | Real-time quotes via Alpaca IEX (free tier) and real L2 recording for crypto via Coinbase. Historical minute/tick bars for equities need a paid vendor (Databento/Polygon) — daily bars are free via `fetch_data.py`. This is the honest cost of day-trading data. |
| 6 | **Fast strategies?** | 🟡 | Tick/L2 recording, OFI/microprice computation, and the 30-strategy HFT library exist and run. True sub-second execution needs the always-on node + a low-latency VPS (see HFT_STRATEGY_LIBRARY.md infra tiers). Colo-tier is out of scope, and the docs say so. |
| 7 | **Backtests I can trust?** | ✅ | Dual independent engines incl. REAL nautilus_trader 1.230.0, parity gate with trade-level diffs, walk-forward + plateau lessons, deflated-Sharpe guard listed. Real data (1,128 days × 10 symbols). |
| 8 | **P&L, taxes, costs?** | ✅/🟡 | Real P&L calendar from Alpaca history, costs table, trades CSV export, wash-sale heuristic (labeled "not tax advice"). Full tax lots need broker statements ingest (blueprint M14 item). |
| 9 | **Journal & discipline?** | ✅ | Notes with observation/incident/discipline tags, kill criteria enforced on every idea, audit log of every kill/go-live/test order. |
| 10 | **Alerts when I'm away?** | 🟡 | Alert rules UI + email config exist; Telegram bot and server-side rule evaluation are the last unwired M14 pieces (need your bot token/SMTP). |
| 11 | **What does it cost me?** | ✅ | $0 software (stdlib backend, free-tier Alpaca/Yahoo/Coinbase). Optional: LLM key, tick-data vendor, VPS. The Costs table keeps the "net after costs" number in your face. |
| 12 | **Can I learn on it / teach with it?** | ✅ | Docs & Learn screen, 8-week curriculum (docs/LEARNING_PATH.md), collaboration workflows (docs/COLLABORATION.md), every screen doubles as a lesson (break-the-parity button, plateau heatmap, live signal derivation). |
| 13 | **PDT rule awareness?** | ✅ | Flagged in Learn screen + curriculum Week 5: under $25k equity, US regs limit day trades in margin accounts (FINRA link provided). Paper trading is exempt — another reason to stay paper while learning. |
| 14 | **Is anything faked?** | ✅ resolved | Everything simulated or demo is explicitly labeled with provenance (data window headers, "demo seed" chips, "SYNTHETIC" markers). The rule: no number pretends to be real. |

## The three caveats that matter most for a day trader
1. **Free data ≠ day-trading data.** IEX real-time is thin (~2–3% of volume); consolidated-tape quality costs money. Budget for a data subscription before trading intraday strategies with size.
2. **This stack's latency floor is ~100–300ms** to Alpaca. Fine for minutes-scale strategies (#18, #20, #28, #29 in the library); not for the sub-second entries — those need the LOW-LAT tier described honestly in the library.
3. **The strategy shipped is weekly, not intraday** — deliberately: it's the teachable, verifiable baseline. Your intraday strategies enter through the same pipeline (spec → dual backtest → parity → paper) using the same live-signal transparency.
