# Build Prompt: Personal Quant Research, Alpha Discovery & Trading Platform
**QuantConnect/LEAN (research) + NautilusTrader (execution) + Control-Plane GUI**

**How to use this document:** Paste this file to a coding agent as the task brief. Work phase by phase — each phase ends with a checkpoint that must pass before moving on. Never flip any venue from paper to live, and never place a live order, without a separate explicit human confirmation at that moment. Treat all API keys and credentials as secrets — never print them in logs, commits, or chat.

---

## 0. Objective

Build a personal quant trading system for equities/ETFs, futures & options, and crypto:

- **QuantConnect / LEAN** — research and alpha discovery: data library, Jupyter research environment, backtesting, optimization.
- **NautilusTrader** — execution: high-performance event-driven engine for parity backtests, paper trading, and live trading (Interactive Brokers, Alpaca*, Binance, Bybit, Coinbase).
- **Control-Plane GUI** — a local web dashboard (this repo's `gui/`) that is the single pane of glass for the whole workflow: alpha journal, backtest/parity gates, paper-trading ops, risk controls, and a staged go-live wizard.

Everything runs locally via Docker Desktop first. Live trading is a deliberate, staged, human-approved final step — never a default.

---

## 1. Architecture rationale

| Concern | QuantConnect / LEAN | NautilusTrader |
|---|---|---|
| Role | Research, alpha discovery, backtesting, data | Live/paper execution engine |
| Interface | Jupyter (`QuantBook`), `QCAlgorithm` | Python `Strategy` classes over Rust core |
| Data | Broad hosted datasets | `ParquetDataCatalog`, venue-native adapters |
| Backtest | Mature, cloud + local (LEAN CLI + Docker) | Nanosecond-resolution, deterministic, same engine live |
| Brokerages | IB, Alpaca, Tradier, Binance, Bybit, Coinbase, Kraken… | IB, Binance, Bybit, Coinbase (*Alpaca community/RFC — see Phase 5) |
| License | LEAN open source; QC Cloud free/paid tiers | LGPL-3.0-or-later, self-hosted, no platform fee |

Use QuantConnect for idea generation and fast iteration; hand a *validated* strategy to NautilusTrader for self-hosted execution. **The parity backtest is the gate between the two.** The GUI enforces the gates visually: an idea cannot advance to the next stage until its checkpoint is marked passed.

---

## 2. Prerequisites

1. Docker Desktop running (Linux containers).
2. Python 3.11+, `git`, VS Code or similar.
3. Accounts: QuantConnect (community tier OK to start), Interactive Brokers **paper**, Alpaca **paper**, testnet/sandbox keys for target crypto exchanges.
4. **Apple Silicon note:** local live trading via LEAN CLI with IB is not supported on ARM — plan to run that leg on an x86 host/VPS, or use NautilusTrader's IB adapter (dockerized IB Gateway), which has no such restriction.
5. Secrets: single `.env` (never committed), referenced by Docker Compose; `.env` and `lean.json` credential blocks in `.gitignore` from the first commit.

**Checkpoint:** `docker run hello-world` succeeds; venv created; all accounts exist with paper/sandbox credentials.

---

## Phase 1 — LEAN CLI locally

1. `pip install lean`
2. `lean init` (pulls `quantconnect/lean` image, scaffolds workspace)
3. `lean login`; configure `lean.json` (org ID, data provider)
4. `lean project-create "alpha-research"`
5. `lean research` → Jupyter Research Environment (`QuantBook`)
6. Pull a sample dataset (e.g., SPY daily bars) in the notebook.

**Checkpoint:** Notebook opens in-container, `QuantBook` loads data, `lean backtest` runs the default template end to end.

---

## Phase 2 — Alpha discovery workflow

1. Define initial universes: small equity basket, one liquid futures product, one or two crypto pairs.
2. Use `QuantBook` for history, feature engineering, exploratory stats (correlation, stationarity, factor exposure, simple `sklearn` models).
3. Persist model artifacts (scalers, weights, feature lists) to the **Object Store** so backtest algo and Nautilus strategy load the same artifact.
4. Log every idea in the GUI's **Research Journal** (backed by `research_log.md` / JSON): hypothesis, universe, features, in-sample stats, expected Sharpe/turnover/capacity, and a **kill criterion**.

**Checkpoint:** ≥1 idea with documented hypothesis, passing in-sample significance checks, artifact saved to Object Store, journal entry created in the GUI.

---

## Phase 3 — Formalize and backtest in LEAN

1. Convert the idea to a `QCAlgorithm` subclass.
2. `lean backtest` locally; iterate.
3. Review tearsheet: Sharpe, max drawdown, turnover, exposure, capacity.
4. Optionally `lean cloud backtest` / `lean optimize` for larger sweeps.
5. Freeze the version in git once results meet the pre-defined bar — don't keep tuning the same window (overfitting).

**Checkpoint:** Frozen, version-controlled `QCAlgorithm` with out-of-sample or walk-forward validation. Record the run in the GUI's Backtest screen.

---

## Phase 4 — NautilusTrader + parity gate

1. `pip install nautilus_trader` (or the Docker/JupyterLab image).
2. Set up a local `ParquetDataCatalog`; import the same historical window used in Phase 3.
3. **Port the strategy** to a Nautilus `Strategy` class — map `Initialize`/`OnData` to `on_start`/`on_bar`/`on_quote_tick`/`on_order_filled`; re-implement indicators.
4. Run `BacktestEngine` (or `BacktestNode` + catalog) over the identical window.
5. **Parity check** in the GUI: side-by-side LEAN vs Nautilus returns, Sharpe, trade log; define tolerance up front. Divergence usually = translation bug (bar timing, fee/slippage model, indicator warm-up).

**Checkpoint (hard gate):** Nautilus results within agreed tolerance of LEAN. The GUI blocks promotion to paper until this gate is marked passed.

---

## Phase 5 — Broker/exchange adapters (paper)

1. **IB:** dockerized IB Gateway in paper mode; Nautilus IB adapter; verify data entitlements and order routing.
2. **Alpaca:** not an officially shipped Nautilus adapter (community/RFC) — check current repo state. If not production-ready: (a) run the Alpaca leg via `lean live --paper` on a separate track, (b) patch the community adapter, or (c) defer.
3. **Crypto:** official testnet/sandbox endpoints; Nautilus adapters with `environment=testnet/sandbox` until go-live.
4. All credentials in `.env` / Docker secrets — never hardcoded.

**Checkpoint:** Each venue connects in paper/sandbox and can place, fill, and cancel a test order. Venue status shows green in the GUI.

---

## Phase 6 — Paper trading operations

1. `docker-compose.yml` stack: Nautilus `TradingNode`, IB Gateway, Postgres (state/trades), Redis (message bus), optional Prometheus + Grafana, and the **GUI backend** (FastAPI, serving `gui/` and bridging to the stack).
2. Launch `TradingNode` in paper mode across all venues.
3. Structured logging + alerting (webhook/Slack/email) for errors, fills, risk breaches — surfaced in the GUI's activity feed.
4. Set a minimum paper duration and acceptance bar (e.g., several weeks; paper P&L tracks backtest within realistic slippage/latency assumptions).

**Checkpoint:** Stack runs unattended for multiple sessions; alerts fire on a deliberately triggered test failure; GUI dashboard reflects live state.

---

## Phase 7 — Risk management and controls (paper and live)

1. Position sizing rules and max leverage per venue and asset class.
2. Circuit breakers: daily loss limit, max-drawdown auto-halt, **manual kill switch** (flattens positions, disables new orders) — a prominent, always-visible control in the GUI requiring typed confirmation.
3. Pre-route order validation layer (price sanity bounds, max order size), independent of strategy logic.
4. Separate risk parameters per asset class (equity limits ≠ futures margin rules ≠ crypto vol-adjusted sizing).

**Checkpoint:** Kill switch tested — flattens and halts within acceptable time; daily loss limit tested with simulated breach; both testable from the GUI.

---

## Phase 8 — Go-live (GUI wizard)

The GUI's **Go-Live Wizard** enforces this sequence per venue, per strategy:

1. Checklist: live-scoped API keys confirmed, account funded, kill switch verified live, monitoring verified, first-trade size set deliberately small.
2. One venue + one strategy at a time.
3. The paper→live flip requires a distinct typed human confirmation each time (e.g., typing the venue name + "GO LIVE") — never automated, never bundled into a deploy.
4. Live trading involves real financial risk and potential tax/regulatory obligations — this document is not financial, legal, or tax advice.

**Checkpoint:** Wizard checklist fully signed off; first live trade minimum size, manually observed end to end.

---

## Phase 9 — Feedback loop

1. Pipe live/paper fills and execution quality back to the Object Store or local data lake (slippage vs. assumptions, realized vs. expected Sharpe) — shown as backtest-vs-realized drift in the GUI.
2. Periodic walk-forward re-validation in LEAN for alpha decay; decayed strategies flagged in the journal for review or retirement.
3. Everything in git: strategy code, artifacts, configs, parameter changelog — every incident reproducible.

---

## Phase 10 — GUI layer specification

**Stack:** static SPA (single HTML file to start; React later if needed) + FastAPI backend bridging to LEAN CLI, Nautilus `TradingNode` (via Redis message bus / Postgres), and Docker. Local-only binding (`127.0.0.1`), no external exposure.

**Screens:**

1. **Ops Dashboard** — global mode banner (PAPER/LIVE), equity curve, day/total P&L, open positions with unrealized P&L, venue health (latency, connection state), strategy status, live activity feed, kill switch.
2. **Research Journal** — alpha idea cards through pipeline stages (Idea → Research → LEAN Backtest → Parity → Paper → Live), each with hypothesis, stats, kill criterion; stage promotion blocked until the stage's checkpoint is marked passed.
3. **Backtest & Parity** — LEAN vs Nautilus side-by-side metrics, overlaid equity curves, per-trade diff table, tolerance thresholds with pass/fail verdict; parity failure blocks promotion.
4. **Risk Controls** — per-venue/per-asset-class limits editor, circuit-breaker states, order-validation log, kill-switch test button.
5. **Go-Live Wizard** — staged checklist with per-item sign-off and typed final confirmation; one venue/strategy per run; full audit log of confirmations.

**Design principles:** dark theme suited to long sessions; red reserved exclusively for loss/danger/live; paper vs live states unmistakable at a glance (color-coded global banner); destructive actions require typed confirmation; every gate shows *why* it's blocked; zero-data states guide the user to the next step.

**Checkpoint:** GUI opens locally, all five screens navigable, kill-switch confirmation flow works, wizard refuses to complete without all items checked + typed confirmation.

---

## Appendix A — Repository structure

```
quant-platform/
  research/                 # LEAN CLI project(s)
  strategies/
    lean/                   # QCAlgorithm implementations
    nautilus/               # Ported Nautilus Strategy classes
  data/catalog/             # ParquetDataCatalog
  gui/                      # Control-plane dashboard (this deliverable)
  api/                      # FastAPI backend (bridges GUI ↔ stack)
  infra/
    docker-compose.yml
    docker-compose.paper.yml
    docker-compose.live.yml
  risk/                     # Shared risk/circuit-breaker config
  logs_and_artifacts/
  .env                      # gitignored
  research_log.md           # alpha journal (GUI-backed)
```

## Appendix B — Security checklist

Never commit `.env`, `lean.json` credentials, or API keys. Read-only/paper-scoped keys until go-live. Rotate live keys regularly. IB Gateway, GUI, and all ports bound to localhost or VPN only.

## Appendix C — Licensing

LEAN engine open source; QC Cloud has free/paid tiers — verify current pricing per dataset/node. NautilusTrader is LGPL-3.0-or-later, self-hosted; you bear infra and data licensing costs (IB market data, exchange API fees).

## Appendix D — Disclaimer

Technical build plan, not financial/legal/tax advice. Live algorithmic trading risks real capital loss. Validate in backtest and paper, start live at minimal size, consult qualified professionals for your jurisdiction.
