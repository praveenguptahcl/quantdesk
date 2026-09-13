# nqq — comprehensive gap analysis vs QuantDesk / zingq / alphaforge

nqq v0.1 nailed the **honesty spine** (deflated Sharpe, FDR, EdgeCertainty, capacity,
signed bundles, council, evolve) but the three references have a much larger *workflow,
execution, discovery, and collaboration* surface. This is the full list of what's left,
and what nqq will build (each tier = a milestone, built fully + tested + screenshot-reviewed).

## Tier A — research → production workflow  *(N6)*
- **Research Journal + stage pipeline**: Idea → Research → Backtest → Parity → Paper → Live,
  each promotion **gated by real honest checks** (deflated-Sharpe floor, capacity, OOS,
  parity, forward record). *(QuantDesk journal + alphaforge promotion/safety/live_gate)*
- **Promotion gates / live-gate**: a strategy cannot advance unless the gate passes; the
  reason is always shown. Go-live needs a typed confirmation.
- **Parity gate**: a second independent recomputation must match the first within tolerance.

## Tier B — discovery & portfolio  *(N7)*
- **Alpha Discovery**: systematic search over families × params × symbols with **FDR control
  across the entire search** (the honest version of mining; most "discoveries" are rejected).
- **Portfolio construction**: combine strategies with **correlation-aware weighting** and a
  **portfolio-level capacity** ceiling; show diversification benefit.
- **Walk-forward + permutation test**: rolling OOS segments + a permutation/Monte-Carlo p-value
  that the edge isn't luck. *(alphaforge optimization/sweep + research/validation)*

## Tier C — execution & ops  *(N8)*
- **Paper execution + honest fill model**: decide@t → fill@t+Δ, square-root impact, spread;
  an order tape with realized slippage. *(alphaforge execution/fill_model + QuantDesk orders)*
- **Fill Honesty Certificate + TCA**: transaction-cost analysis proving fills are realistic.
- **Risk envelope + kill switch**: circuit breakers, per-asset limits, daily-loss halt.

## Tier D — the loop & learning  *(N9)*
- **Resolve & grade loop** (READ→TEST→DECIDE→ACT→RESOLVE): council decisions are graded
  after the outcome; each voice's **track record updates** so the record sharpens the next
  loop. *(zingq's core loop — makes voices earn "rated")*
- **Drift monitoring**: live-vs-backtest drift with a re-fit signal.
- **Per-regime breakdown**: performance and capacity split by regime.

## Tier E — collaboration & AI  *(N10)*
- **Copy-trading / shared strategies**: publish, copy-with-attribution, community board.
- **AI Strategy Builder**: verbal description → signal/strategy spec (builtin parser).
- **LLM arena agents + LLM council voice**: autonomous competitors + an AI voice (keyless
  heuristic; real model when a key is present).
- **CLI**: `python3 -m nqq.arena` run / --history / --bundle / --verify-bundle / --serve.

## Tier F — polish
- Data & Artifacts (catalog inventory), onboarding Journey, Morning Brief, richer Learn,
  doctor extended to cover every new subsystem.

Everything stays: zero third-party deps, real point-in-time data, honest-by-construction.
