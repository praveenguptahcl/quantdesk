# HFT Strategy Library — 30 Archetypes with Specs
These are the 30 strategies seeded into the platform (HFT Library screen). They are **documented archetypes from the academic and practitioner literature**, specified precisely enough to implement and backtest. Honesty first: nobody can hand you 30 "guaranteed winning" HFT strategies — realized edge in this space is a function of **latency tier, queue position, fee/rebate schedule, and adverse selection**, not just the signal. The SR ranges quoted are what the literature and practitioner reports attribute to well-executed implementations at the stated infra tier; treat them as capacity-of-the-idea, not a promise.

**Infra tiers (be ruthless about this):**
- **RETAIL** — implementable today on this stack (IB/Alpaca/crypto venues from a home machine or VPS). Holding periods ≥ minutes, or crypto where venue APIs are direct.
- **LOW-LAT** — needs a VPS near the venue (AWS Tokyo for Binance, NY4/NJ metro for US equities via a low-latency broker), direct market data, single-digit-ms RTT. Crypto HFT is realistically accessible here.
- **COLO** — needs colocation, direct exchange feeds (ITCH/OUCH, MDP3), sub-100µs tick-to-trade. Listed for completeness and for paper-mode study; not reachable on a retail stack. Do not pay for colo before consistently profitable at LOW-LAT.

**Implementation path for every entry:** journal → spec (schema v1, same as the AI Builder emits) → LEAN backtest where the data resolution allows (minute/second) → NautilusTrader tick-level backtest via `ParquetDataCatalog` L1/L2 tiers → parity → paper. Nautilus is the engine that matters here: its nanosecond event loop and L2 book reconstruction are what make tick-level backtests honest.

---

## A. Market Making (1–5)

**1. Avellaneda–Stoikov Inventory MM** · eq/cry · ms–min · COLO · SR 2–6
Quote both sides around a reservation price `r = mid − q·γσ²τ` (q = signed inventory), spread `γσ²τ + (2/γ)ln(1+γ/κ)`. Inventory mean-reverts by construction. Params: γ (risk aversion) 0.1–1, κ from book density fit, σ from 1-min RV. Risk: pull quotes on 3σ vol spikes or news flags; hard inventory cap 2% ADV. *Reference: Avellaneda & Stoikov (2008).*

**2. Multi-Venue MM with Netting** · cry · ms–min · LOW-LAT · SR 2–5
Run #1 simultaneously on 2–3 crypto venues off a consolidated fair value; net inventory globally, hedge residual on the deepest venue. Edge adds venue-spread differentials to #1. Risk: venue outage triggers cancel-all on siblings; pre-funded balances.

**3. Queue-Position MM** · eq/fut · s–min · COLO · SR 2–5
Priority is the asset: join deep queues early, model expected queue value = P(fill)·(spread − adverse selection). Cancel when queue-ahead depletion accelerates (toxicity proxy). Requires MBO (market-by-order) data. Risk: venue cancel-ratio budgets.

**4. Delta-Hedged Options MM** · opt · min–hr · COLO · SR 2–4
Fit a vol surface continuously; quote strikes where |market − fit| > edge threshold; hedge delta in the underlying within ms. Risk: vega/gamma/pin caps, pull on refit failure. Capital-heavy; listed as the canonical options MM archetype.

**5. Funding-Aware Perp MM** · cry · min–hr · LOW-LAT · SR 1.5–4
Perp MM with quotes skewed toward the side that accrues predicted funding (EMA of premium index). Collect spread + funding. Risk: 5× liquidation buffer, kill on spot-perp basis blowout. Retail-adjacent: doable on Binance/Bybit testnets today.

## B. Order-Flow / Microstructure Alpha (6–13)

**6. Order-Flow Imbalance Momentum** · eq/fut/cry · 100ms–30s · LOW-LAT · SR 2–5
OFI = Σ(ΔbidSize@best − ΔaskSize@best) over 200ms–1s. Enter with the imbalance when OFI z-score > 2 sustained 500ms and spread ≤ 1 tick; exit +4bps/−2bps/30s. The single best-documented microstructure signal. *Reference: Cont, Kukanov & Stoikov (2014).*

**7. Tape-Aggressor Momentum** · eq/cry · 1–60s · LOW-LAT · SR 1.5–4
Signed-volume ratio (aggressor buys vs sells) over rolling 2s; enter > 0.75 with 3× volume; exit on normalization or bracket. Simpler cousin of #6 using trades only (works from L1).

**8. Depth-Imbalance Reversion** · eq/fut · 1–20s · COLO · SR 2–4
Extreme one-sided *displayed* depth that fails to move price is disproportionately spoof/noise — fade it. Gate through #13. Strict stops; small size.

**9. Microprice Reversion** · eq/cry · 100ms–5s · COLO · SR 2–5
Microprice = (Pa·Qb + Pb·Qa)/(Qa+Qb) leads mid. Post passively on the microprice side when |micro−mid| > 0.4 tick. Maker-only. *Reference: Stoikov (2018).*

**10. Sweep Follow-Through** · eq/fut · 1–30s · LOW-LAT · SR 1.5–3.5
A single event clearing ≥3 book levels = informed flow; enter in sweep direction within 50ms, exit +6/−3bps or on refill.

**11. Iceberg Detection Fade** · eq/fut · 10s–5min · LOW-LAT · SR 1.5–3
Same-price refills >4× displayed size reveal hidden liquidity walls; rest in front of the wall, exit when refills stop.

**12. Cross-Asset Lead-Lag (ES→NQ)** · fut · 100ms–10s · COLO · SR 2–4
Rolling Hayashi–Yoshida lead-lag; when the leader moves k ticks and the laggard hasn't, trade the laggard. Disable below 0.7 correlation.

**13. Book-Anomaly Filter (meta-strategy)** · all · — · LOW-LAT · SR n/a
Not standalone alpha. Detects layering/quote-stuffing signatures (cancel-burst rate, depth-flicker entropy) and emits a trade/no-trade gate consumed by #6–#12. Raises every flow strategy's realized SR by cutting toxic periods. Build it first.

## C. Arbitrage / Relative Value (14–21)

**14. ETF–Basket Arbitrage** · eq · s–min · COLO · SR 3–6 — ETF mid vs Σwᵢ·midᵢ; trade the rich/cheap leg when |premium| > costs + k·σ. True create/redeem arb needs AP status; retail trades the convergence signal.
**15. Futures–ETF Basis (ES↔SPY)** · eq/fut · s–min · COLO · SR 2–5 — carry-adjusted basis z > 2.5, both legs simultaneous, 200ms leg-out limit.
**16. Cross-Venue Crypto Arb** · cry · ms–s · LOW-LAT · SR 2–6 — bid(A) > ask(B) + fees: simultaneous IOC both venues, pre-funded. The most accessible genuine HFT arb for non-institutions.
**17. Triangular Crypto Arb** · cry · ms–s · LOW-LAT · SR 1.5–4 — single-venue 3-leg cycle; partial-fill unwind logic is the whole game.
**18. Perp–Spot Basis Capture** · cry · hr–days · **RETAIL** · SR 1.5–3 — long spot, short perp when funding rich; delta-neutral carry. Slow "HFT-adjacent" income; works on this stack today.
**19. HF Pairs Convergence** · eq · min–hr · LOW-LAT · SR 1.5–3 — tight cointegrated pairs on 1s bars; z > 2.2 in, < 0.3 out; daily coint re-test.
**20. Index Rebalance Micro-Drift** · eq · min–hr · **RETAIL** · SR 1–2.5 — announced adds/deletes drift on tracking flow into effective date.
**21. Calendar-Spread Scalping** · fut · s–min · COLO · SR 2–4 — front/back spread book mean-reverts in an intraday band; passive at edges.

## D. Event-Driven (22–25)

**22. Opening Auction Imbalance** · eq · min · LOW-LAT · SR 1.5–3 — 9:28 imbalance/ADV predicts post-open drift; bracket exit at +15min.
**23. Closing Auction MOC Imbalance** · eq · min · LOW-LAT · SR 1.5–3.5 — 3:50pm publication → drift into close; exit via MOC order; skip earnings names.
**24. Macro-Release Momentum** · fut · 100ms–5min · COLO · SR 2–5 — surprise vs consensus parsed in ms; the latency race is won at ~10ms. Included for completeness — study in paper mode, don't fund it retail.
**25. Halt-Resumption Volatility** · eq · s–min · LOW-LAT · SR 1.5–3 — LULD reopen overshoots revert; fade gaps > 2× band width; one attempt per halt.

## E. Liquidity Provision (26–27)

**26. Inverted-Venue Rebate Capture** · eq · s–min · COLO · SR 1–2.5 — post at NBBO on taker-rebate venues where crossing probability is high; monitor per-venue markouts.
**27. Midpoint Dark Liquidity** · eq · s–min · LOW-LAT · SR 1–2.5 — rest midpoint pegs (dark pools/IEX); capture half-spread; route by venue toxicity score.

## F. Intraday Scalping (28–30) — the RETAIL on-ramp

**28. VWAP-Band Reversion** · eq/cry · min · **RETAIL** · SR 1–2.5 — fade ±1.5σ VWAP-band extremes with a tape-deceleration confirm; ADX trend-day gate is mandatory (trend days are the entire loss distribution).
**29. Opening-Range Breakout** · eq/fut · min–hr · **RETAIL** · SR 1–2 — decayed classic; only tradable with volume confirm + inside-day filter + fixed 0.25% risk per trade.
**30. Stop-Run Fade** · fut/cry · s–min · LOW-LAT · SR 1.5–3 — flushes through swing levels on thin books revert when depth refills; never fade news (gate through #13 + calendar).

---

## Recommended build order on this platform

1. **RETAIL tier first** (#18, #20, #28, #29): validates the whole pipeline end-to-end with data you already have.
2. **#13 (anomaly filter)** next — it's a dependency of everything in section B.
3. **Crypto LOW-LAT** (#16, #17, #5, #6-on-BTCUSDT): crypto venues give retail direct API access with ~10–50ms RTT from a well-placed VPS; this is where genuine sub-second strategies are realistically yours.
4. **Equity/futures LOW-LAT** (#6, #7, #10, #22, #23, #25) once a low-latency broker + direct data are in place.
5. **COLO tier**: paper-study only until the economics (colo ≈ $2–10k/mo + data fees) are justified by live LOW-LAT P&L.

Every entry maps to the spec schema (`quantdesk.strategy.v1`) — the AI Builder can also regenerate any of them from a verbal description, and the Journal enforces the same gates (backtest → parity → paper → live) regardless of frequency.
