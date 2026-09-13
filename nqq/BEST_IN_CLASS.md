# nqq → best-in-class: the plan

Decisive, opinionated, executed in batches (not micro-iterations). Every item keeps the
governing thesis: *a system that tells the truth about whether an edge exists is worth more
than one that flatters you.* Nothing here fakes reality; missing capabilities degrade to a
clearly-labelled fallback.

## B1 — Real, by reusing the keys already on this machine  *(biggest credibility lever)*
- **Env loader**: read `NQQ_ENV_FILE`, else `nqq/.env`, else auto-discover the QuantDesk
  `.env` — reuse `ALPACA_PAPER_KEY/SECRET`, `ANTHROPIC_API_KEY`, `POLYGON_API_KEY`,
  `FINNHUB_KEY`, `ALPHAVANTAGE_KEY`, `FRED_KEY`. Read-only. Fail-safe to synthetic.
- **providers.py**: real equity quotes (Alpaca IEX), real crypto (Binance public), Stooq
  daily bars to extend the catalog on demand, FRED macro (real regime input). Each returns
  an honest `source` label; a Data Status badge shows which providers are live.
- **Real LLM** agents/council when `ANTHROPIC_API_KEY` present (already wired; verify).
- **Calibrated cost model** from alphaforge COSTS.md (taker/maker fees, adverse-selection
  floor, participation caps) — fills never cost-free, never beat NBBO.

## B2 — UI/UX best-in-class  *(user's #1 named concern)*
- A real **Home** (the Morning Brief becomes a rich landing with next-best-actions).
- **⌘K command palette** — jump to any screen / symbol / action instantly.
- **Tooltips on every honest metric** (deflated Sharpe, DSR, capacity, FDR, veto…).
- Consistent **loading + empty states**, keyboard nav, a coherent design system, a data-
  status badge in the header, breadcrumbs, and a light/dark toggle.

## B3 — Data portability + multi-tenancy
- **Workspace export/import**: the whole workspace (ideas, signals, decisions, rooms,
  trials) as ONE signed, verifiable bundle you can move between deployments.
- **Tenancy**: scope ideas/trials/rooms/decisions per user+org; an Org concept; the trial
  ledger is per-operator already — extend consistently.

## B4 — Deployment + demo + quality
- **Dockerfile** + readiness/liveness endpoints + config validation + `NQQ_HOST` bind.
- A rich, resettable **Tour demo** that showcases every feature end to end.
- More tests + doctor coverage for every new subsystem; an E2E smoke test.

Execution rule: build a whole batch with fast file edits, then ONE verification run
(doctor + tests + live-flow + screenshots). Commit per milestone. Repeat.
