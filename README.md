# QuantDesk — Quant Research & Trading Platform

Personal quant platform: **QuantConnect/LEAN** for research, **NautilusTrader** for execution, and a local **control-plane GUI** tying the workflow together.

## What's here

| File | Purpose |
|---|---|
| `gui/index.html` | Working GUI — open it in any browser right now. No install, no server, runs on mock data. |
| `BUILD_PROMPT.md` | The refined build brief — paste to a coding agent, work phase by phase. |
| `README.md` | This guide. |

## Step 1 — Try the GUI (30 seconds)

Double-click `gui/index.html`. Explore:

1. **Ops Dashboard** — paper equity curve vs backtest, positions, venue health, activity feed.
2. **Research Journal** — alpha ideas move through Idea → Research → LEAN Backtest → Parity Gate → Paper → Live. Click any card; blocked gates explain why and refuse promotion.
3. **Backtest & Parity** — switch the strategy dropdown to see one passing and one failing parity gate (`pairs-stat-v1` fails on purpose).
4. **Risk Controls** — circuit breakers, per-asset limits, kill-switch dry-run, simulated breach.
5. **Go-Live Wizard** — try to skip a checklist item (you can't). The final flip requires typing `IBKR GO LIVE`; the top banner turns red LIVE and the confirmation is audit-logged.
6. **Kill switch** (top-right, always visible) — requires typing `FLATTEN`.

## Step 2 — Build the platform underneath

Follow `BUILD_PROMPT.md` phase by phase. Short version:

1. **Prereqs**: Docker Desktop, Python 3.11+, QuantConnect account, IB + Alpaca *paper* accounts, crypto testnet keys. Secrets in `.env`, gitignored from commit one.
2. **Phase 1–3 (research)**: `pip install lean` → `lean init` → research in Jupyter → formalize as `QCAlgorithm` → backtest → freeze in git with out-of-sample validation.
3. **Phase 4 (parity — the hard gate)**: port to a NautilusTrader `Strategy`, backtest the identical window, reconcile within tolerance before anything touches paper.
4. **Phase 5–6 (paper)**: dockerized IB Gateway + Nautilus adapters on testnet/sandbox; run the Compose stack unattended for weeks.
5. **Phase 7 (risk)**: loss limits, auto-halt, kill switch — tested, not just configured.
6. **Phase 8 (live)**: one venue, one strategy, typed human confirmation, minimum size, watched end to end.

## Step 3 — Wire the GUI to real data

The GUI's data access is isolated in one place: the `API` object at the top of the `<script>` block in `gui/index.html`. Each property/method maps to one backend endpoint. To go real:

1. Stand up a small FastAPI app (`api/`) exposing e.g. `/api/positions`, `/api/venues`, `/api/parity/{strategy}`, `/api/kill` (reading from Postgres/Redis in the Compose stack, shelling to `lean` CLI for research actions).
2. Replace each `API.*` mock with a `fetch()` call — the UI code never touches data directly, so nothing else changes.
3. Serve `gui/` from FastAPI, bound to `127.0.0.1` only.

## Safety rules (non-negotiable)

- Paper → live is never automated; typed confirmation per venue per strategy.
- Never commit `.env`, `lean.json` credentials, or API keys.
- Kill switch must be tested against live endpoints before any live order.
- Not financial, legal, or tax advice — real capital risk.
