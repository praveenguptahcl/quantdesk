# Platform Review: QuantDesk vs zingq vs alphaforge

*Structured review of `/Users/kg/zingq` and `/Users/kg/alphaforge` against the current
QuantDesk platform. Based on architecture, design docs (ARENA.md, ARCHITECTURE.md,
POSITIONING.md, plan.yaml), and module-level source/docstrings — not a line-by-line
audit of all ~50k LOC each. Ratings are 1–100 per feature dimension.*

---

## 1. What each project is

**QuantDesk** (this repo) — a zero-dependency, stdlib-only quant *learning + practice*
platform. Its centre of gravity is **engagement and teaching**: the AlphaPulse Conviction
Console (gamified speedometers, execution tape), a signal library with IC diagnostics, a
weekly leaderboard where humans and LLM agents compete, copy-trading, Alpaca paper
execution, and a dual-engine backtest+parity gate. Beautiful front-end, light statistical
spine.

**alphaforge** (~50k LOC) — a rigorous, honesty-first **quant arena engine**. Its centre of
gravity is **statistical truth**: deterministic competition → honest scoring → verifiable
artifacts → replayable proof. Modules for scoring (EdgeCertainty, composite veto),
research (FDR, trial ledger, deflated Sharpe, bootstrap, lookahead), capacity/crowding,
execution cost models (square-root impact, fill honesty certificate, TCA), safety
(live-gate, envelope), reproducible signed bundles, and a `doctor --deep` that proves
replay==live. Plainer UI.

**zingq** (~47k LOC, embeds alphaforge as `_seed`) — the newer evolution. Adds the
**Council** ("where humans and AIs decide together"): a pre-trade decision review where
every voice (signal families + LLM agents) testifies FOR/AGAINST/NEUTRAL with a track
record and an evidence link, plus plain-English hygiene checks, and refuses a naked
combined probability while voices are unrated. Also adds `evolve` ("alive software":
agents propose / humans dispose, honesty core amendment-protected), a `kernel` with
stamps / trial-ledger / signed bundles / tenancy, and per-family signal decay.

The three form a clear maturity gradient on *rigor*: **alphaforge ≈ zingq ≫ QuantDesk**.
On *UX / engagement / deployability*: **QuantDesk ≫ alphaforge ≈ zingq**.

---

## 2. Feature-by-feature ratings (1–100)

| # | Feature dimension | QuantDesk | zingq | alphaforge |
|---|---|:--:|:--:|:--:|
| 1 | **Leaderboard statistical honesty** (deflated Sharpe, not raw PnL) | **15** | 90 | **96** |
| 2 | **Multiple-testing / FDR / trial ledger** (Sybil & variance-farming defence) | **5** | 92 | 95 |
| 3 | **Point-in-time data / lookahead prevention** | 25 | 90 | 92 |
| 4 | **Capacity & crowding ceiling** ("edge holds to $X AUM") | **5** | 85 | 95 |
| 5 | **Transaction-cost model** (impact, funding, borrow, exec delay) | 30 | 80 | 93 |
| 6 | **Fill realism / fill-honesty certificate + TCA** | 40 | 75 | 92 |
| 7 | **Bootstrap confidence intervals / robustness** | 10 | 85 | 92 |
| 8 | **Regime analysis** | 55 | 80 | 88 |
| 9 | **Signal library / feature store** | 70 | 82 | 85 |
| 10 | **Signal diagnostics** (IC, decay, hit rate, turnover) | 72 | 85 | 88 |
| 11 | **Human+AI decision council / evidence-linked voices** | 30 | **96** | 70 |
| 12 | **Pre-trade hygiene checks that teach** | 20 | 92 | 65 |
| 13 | **LLM agents / autonomous traders** | 65 | 85 | 82 |
| 14 | **Multi-agent arena / competition framework** | 60 | 88 | 94 |
| 15 | **Reproducibility** (signed bundles, replay==live doctor) | 20 | 90 | 95 |
| 16 | **Determinism / seeded runs** | 40 | 90 | 92 |
| 17 | **Governance / "alive software" proposals ledger** | 5 | 90 | 40 |
| 18 | **Multi-tenancy / identity isolation** | 45 | 85 | 70 |
| 19 | **Live/paper execution (broker adapters)** | 60 | 70 | 85 |
| 20 | **Drift monitoring (live vs backtest)** | 40 | 80 | 88 |
| 21 | **Safety** (kill switch, live-gate, risk envelope) | 62 | 80 | 88 |
| 22 | **Real market-data ingestion** | 55 | 78 | 80 |
| 23 | **Backtest rigor / dual-engine parity** | 65 | 82 | 85 |
| 24 | **API / programmability** (CLI, JSON API, exports) | 55 | 80 | 90 |
| 25 | **Test coverage / verification** | 55 | 85 | 88 |
| 26 | **Onboarding / teaching / docs-with-every-claim** | 70 | 88 | 80 |
| 27 | **Copy-trading / social layer** | **75** | 60 | 55 |
| 28 | **Web UI polish (visual design)** | **88** | 65 | 70 |
| 29 | **Visual gamification / engagement** | **92** | 50 | 55 |
| 30 | **Zero-dependency / deployability** | **95** | 65 | 60 |
| | **Unweighted average** | **≈47** | **≈80** | **≈81** |

**Where QuantDesk already wins (keep and lean into):** visual gamification (#29, the
Conviction Console is genuinely best-in-class here), UI polish (#28), zero-dep
deployability (#30), copy-trading/social (#27), onboarding (#26).

**Where QuantDesk is dangerously behind:** leaderboard honesty (#1), multiplicity/FDR
(#2), capacity (#4), reproducibility (#15), bootstrap CIs (#7), governance (#17).

The strategic takeaway is sharp: **QuantDesk has the best face and the weakest spine.**
The mature projects have a world-class spine and a plain face. The single most valuable
thing you can do is transplant the alphaforge/zingq honesty spine *into QuantDesk's
already-great UI.* That combination — rigorous truth rendered beautifully and taught
simply — is something neither side has today.

---

## 3. The crown-jewel features to port (ranked by value)

### ★1 — Honest leaderboard: deflated Sharpe + operator multiplicity, net of costs
*(alphaforge `arena/scoreboard.py` SPEC-05, `research/validation.deflated_sharpe_ratio`,
`research/trial_ledger.py`, `research/fdr.py`)*

QuantDesk today crowns the weekly winner by **raw 5-day return**. That is the exact trap
these projects exist to solve: with N competitors and free re-tries, the weekly winner is
almost always luck or variance-farming. alphaforge ranks by **deflated Sharpe with
operator-level multiplicity**, net of all costs, excess-vs-baseline, each row carrying a
bootstrap CI and a mandatory *"weekly is spectacle, not evidence"* banner; low-confidence
and 5-day samples rank below-fold; last-hour-concentrated returns are penalised. **This is
the highest-value change available** and it directly fixes QuantDesk's biggest
credibility hole.

### ★2 — EdgeCertainty: a probability you *earn*, with hard honesty ceilings
*(alphaforge `scoring/certainty.py`, `scoring/composite.py`)*

Instead of an ad-hoc "confidence", certainty is fused from six independent axes
(worst-regime deflated Sharpe, bootstrap p-positive, luck gap, FDR survival, OOS
consistency, live-paper track record) and **capped hard** when the data is synthetic
(≤0.50) or there's no forward track record (≤0.70). The composite is **veto-gated**: any
single fatal axis caps the verdict at "Inconclusive". This is exactly what the Conviction
Console's "fills at ≥60% certainty" *should* mean — right now that 60% is a sample number.

### ★3 — The Council on the Conviction Console
*(zingq `app/council.py`)*

Before a fire/invest, render every voice — signal families on current data + LLM agents —
as FOR/AGAINST/NEUTRAL, each with a plain-English reason, a **track-record chip**, and an
**evidence link** (ruling 73: no testimony without a link). Add the H1–H5 **hygiene
checks that teach**, and refuse a naked combined probability while voices are unrated.
This turns QuantDesk's beautiful-but-decorative gauges into a genuine decision aid and is
the single biggest *teaching* upgrade.

### ★4 — Capacity & crowding ceiling on every strategy/signal
*(alphaforge `capacity/`, `execution/cost_model.SquareRootImpactModel`)*

"Sharpe without capacity is vanity." Show, next to every strategy and signal, the AUM at
which its own square-root market impact eats the edge, shrunk by a crowding factor. A
one-number ceiling ("holds to ≈ $X") that reframes every result honestly.

### ★5 — Reproducibility: signed decision bundles + replay==live doctor
*(alphaforge signed bundles, `--verify-bundle`, `doctor --deep`; zingq `kernel` bundle codec)*

Every leaderboard result should be a **verifiable artifact**: a signed bundle
(events+leaderboard+manifest+certificate) that anyone can replay and get bit-identical
numbers. This is what makes a competition trustworthy rather than a claim.

### ★6 — Point-in-time discipline + lookahead guard
*(alphaforge `data/pit.py`; zingq `_seed/research/lookahead_guard.py`)*

QuantDesk backtests over full arrays. A PIT guard that refuses to let a feature at bar *t*
see data from *t+1* (and flags it loudly) closes a whole class of silent optimism.

### ★7 — Cost/fill upgrade + execution delay
*(alphaforge `execution/fill_model.py`, `broker_fills.py`, `--execution-delay`, `--funding`)*

Replace the flat 5 bps with square-root impact + funding/borrow + a decided-at-*t*,
fills-at-*t+Δ* delay. Small change, large honesty gain.

### ★8 — Governance / "alive software" (longer-horizon)
*(zingq `evolve/`)*

Agents propose, humans dispose; honesty-core changes need two signatures + red-team; test
soak before backport; every proposal states a metric claim. A north star for how the
platform itself should evolve safely once community-run.

---

## 4. Most valuable proposed changes to QuantDesk (prioritized)

1. **Re-rank the weekly leaderboard by deflated Sharpe (net of costs) with a
   multiplicity/trial penalty and a "spectacle, not evidence" banner.** Keep the raw-return
   number visible as the fun headline, but make the *durable* ranking honest. Add a
   bootstrap CI to every row and sink 5-day / low-confidence rows below the fold.
   *(Ports ★1; touches `api/community.py:standings/maybe_finalize_week`.)*

2. **Add a Trial Ledger.** Count every backtest, optimizer sweep cell, and adopted variant
   as a trial per user/strategy; expose "N trials → deflation applied" on the leaderboard
   and signal library. This is the Sybil / variance-farming defence and it's cheap to add
   on top of the existing SQLite store. *(Ports ★1's ledger.)*

3. **Wire EdgeCertainty behind the Conviction Console's "≥60% certainty".** Compute a real
   earned-certainty per strategy (worst-regime DSR + bootstrap p + OOS + live-paper), cap
   at 0.50 for sample/synthetic symbols, and show the axes on the strategy slide-over.
   Makes the console's headline claim true. *(Ports ★2.)*

4. **Add the Council panel** to the symbol drill-down and the invest flow: FOR/AGAINST/
   NEUTRAL voices with record chips + evidence links + H1–H5 hygiene. *(Ports ★3.)*

5. **Show a capacity ceiling** on every strategy card and signal (one line). *(Ports ★4.)*

6. **Add signed, replayable result bundles** for each finalized week + a `verify` endpoint.
   *(Ports ★5.)*

7. **Add a PIT/lookahead guard** to the signal evaluator and backtest engines. *(Ports ★6.)*

8. **Upgrade the cost model** (square-root impact + funding + exec delay). *(Ports ★7.)*

The first three are the outsized wins: they make QuantDesk's most visible surfaces (the
leaderboard and the console) statistically honest, which is the one thing that currently
undermines an otherwise excellent product.

---

## 5. Issues in the current QuantDesk to fix

**Statistical-integrity issues (high — these are the crux):**
- **Leaderboard ranks by raw weekly return.** Rewards luck and variance-farming; a user who
  spins 30 optimizer variants and invests the best will "win" by chance. No deflation, no
  multiplicity, no CI. *(This is the #1 issue.)*
- **Signal IC is computed in-sample** without FDR/deflation or OOS split → systematically
  optimistic; the "verdict" can call noise "promising".
- **No bootstrap CIs anywhere** — every Sharpe/return is a point estimate presented as fact.
- **Console "fills at ≥60% certainty"** implies an earned edge; the 60% is currently a
  sample threshold on sample conviction. Needs the certainty layer or a clearer honesty
  label on the headline (the per-symbol SAMPLE tag helps but the HUD claim doesn't).
- **Flat 5 bps cost model, no capacity ceiling, no slippage realism** beyond Alpaca paper →
  edges that only exist at tiny size look real.
- **No point-in-time guarantee** in the backtest engines (full-array indicators).

**Product / UX issues (medium):**
- The Conviction Console gauges are decorative — they show conviction but don't *justify*
  it (no council/voices/evidence), so a learner can't see *why* to trust a fire.
- Weekly winner is frozen with no verifiable/replayable artifact — can't be audited.
- LLM traders pick via a builtin plateau-Sharpe policy that itself ignores multiplicity.

**Already addressed in recent milestones (noted for completeness):** DELETE/auth gating,
shared-account admin gating, honest DEMO/SAMPLE labelling on Orders/Risk/Micro, backend
connectivity of Risk/Orders/Library, XSS escaping, session expiry — these were the M18/M21
fixes and are in good shape.

---

## 6. Recommended positioning

Don't try to out-rigor alphaforge/zingq from scratch — **absorb their spine**. QuantDesk's
durable advantage is that it makes quant *legible and fun*. If it renders **honest**
numbers (deflated Sharpe, earned certainty, capacity, evidence-linked council) in its
already-beautiful, gamified, zero-dependency shell, it becomes the teaching-and-practice
front-end that the rigorous engines never had — and the only place where correct quant
statistics are also *engaging*. That is the highest-value direction for the platform.
