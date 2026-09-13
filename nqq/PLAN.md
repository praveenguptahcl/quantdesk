# nqq — honest quant, rendered beautifully

**One line:** the honesty spine of alphaforge/zingq (deflated Sharpe, multiplicity control,
capacity, EdgeCertainty, the human-AI Council) inside a QuantDesk-quality gamified,
zero-dependency shell. Correct quant statistics that are also *engaging and taught*.

**Principles (constitutional, enforced in code):**
1. **No naked number.** Every Sharpe/return/probability ships with a confidence interval,
   a sample-size gate, and an evidence link. A bare rank is impossible.
2. **You earn certainty, you can't set it.** EdgeCertainty is fused from independent axes
   and hard-capped for synthetic data (≤0.50) and no-forward-record (≤0.70).
3. **Rank by deflated Sharpe, not raw PnL.** Weekly raw return is *spectacle* (banner-gated);
   the durable ranking is multiplicity-corrected.
4. **Every trial counts.** A trial ledger tracks all backtests/variants per operator; the
   more you try, the more your best result is deflated (Sybil / variance-farming defence).
5. **Point-in-time or it doesn't count.** A lookahead guard refuses features that peek.
6. **Reproducible or it isn't real.** Every result is a signed bundle anyone can replay.
7. **Zero third-party runtime deps.** Pure Python stdlib + a single-file SPA. Deployable
   anywhere QuantDesk is.

---

## Modules (backend, `nqq/`)

| module | responsibility | lineage |
|---|---|---|
| `kernel.py` | sqlite store, PBKDF2 auth + roles + tenancy, append-only **stamp ledger**, **trial ledger**, HMAC **signed bundles** (write/verify) | zingq kernel + QuantDesk store/community |
| `data.py` | catalog OHLCV loader, **point-in-time accessor**, **lookahead guard** | alphaforge data/pit + zingq lookahead_guard |
| `signals.py` | signal families (momentum, mean-rev, RSI, bollinger, breakout, volume-thrust) with **decay half-lives**; PIT-safe values; IC / IC-decay / hit / turnover | QuantDesk signals + zingq council families |
| `costs.py` | **square-root market-impact** model, funding/borrow, **execution delay** (decide@t, fill@t+Δ); **capacity & crowding** ceiling | alphaforge capacity + cost_model |
| `backtest.py` | signal→strategy engine using real costs + capacity + **regime split** + **OOS split** | QuantDesk backtest + alphaforge research |
| `stats.py` | the honesty core: **deflated Sharpe**, **block bootstrap CI**, **Benjamini-Hochberg FDR**, luck gap, **EdgeCertainty** (6 axes + hard ceilings), **composite veto verdict** | alphaforge scoring/certainty + research/validation |
| `arena.py` | agents (rule + LLM) & operators; **deflated-Sharpe ranking with operator multiplicity**, sample gating, below-fold, **not-evidence banner** | alphaforge arena/scoreboard |
| `council.py` | pre-trade review: **hygiene H1–H5** (teach), **FOR/AGAINST/NEUTRAL voices** with record chip + **evidence link**, honest bottom line (refuses naked combined p) | zingq app/council |
| `console.py` | gamified Conviction Console data, now backed by real EdgeCertainty + council | QuantDesk M22–24 |
| `evolve.py` | governance **proposals ledger** (agents propose / humans dispose; honesty core protected) | zingq evolve |
| `server.py` | stdlib HTTP routes; auth gate; admin gate | QuantDesk server |

---

## Screens (single-file SPA `gui/index.html`)

1. **Conviction Console** *(default)* — gamified speedometer per symbol; the needle is
   signed conviction, but the **card badge is the earned EdgeCertainty** (with its veto
   verdict colour). A "fire" opens the **Council** rather than blindly executing. Rich
   execution tape. *Engagement + honesty in one view.*
2. **Symbol drill-down** — strategy dials, price/volume/fill chart, signal gauges, per-symbol
   tape, **and the Council panel**: FOR/AGAINST/NEUTRAL voices with record + evidence links,
   H1–H5 hygiene.
3. **Arena** *(leaderboard)* — ranked by **deflated Sharpe**, each row a **CI bar** + baseline
   excess + trial count + operator; a permanent **"weekly raw return is spectacle, not
   evidence"** banner; low-confidence/short-sample rows below the fold; agent detail pages.
4. **Signal Lab** — signal families with IC / IC-decay / hit / turnover, **FDR-corrected
   verdict**, **capacity ceiling**, decay half-life, PIT-safe badge; publish to shared library.
5. **Strategy / Research** — compose a strategy from signals; backtest with real costs +
   capacity + regime + OOS; an **EdgeCertainty scorecard** (6 axes) with a **veto verdict**
   ("a strong average can't hide a fatal weakness").
6. **Council** *(standalone)* — declare a TradePlan → hygiene + voices + honest bottom line +
   journal snapshot to the ledger.
7. **Evidence** — signed result bundles; **verify** (replay==stored) with a green/red seal.
8. **Learn** — every statistical claim explained plainly (what is deflated Sharpe? why
   multiplicity? what is capacity?).
9. **Admin / Governance** — users & roles, the **trial ledger**, the **evolve proposals
   ledger**, sample-data reset.

---

## Honesty invariants (asserted by tests)
- Deflated Sharpe ≤ raw Sharpe always; grows with sample, shrinks with trial count & skew.
- EdgeCertainty on synthetic data ≤ 0.50; with no forward record ≤ 0.70; monotone in each axis.
- Composite verdict never exceeds "Inconclusive" if any veto axis fails.
- Every leaderboard row carries CI + trials + banner; 5-day samples flagged not-evidence.
- Lookahead guard raises on any t→t+1 peek.
- A signed bundle re-verifies byte-identically; a tampered one fails.

---

## Build milestones (autonomous, each: build → tests → headless screenshot review → fix → commit)
- **N0** plan + scaffold + kernel + kernel tests.
- **N1** honesty spine: data/PIT, signals, costs/capacity, backtest, stats (DSR/bootstrap/FDR/
  EdgeCertainty/veto) + heavy tests.
- **N2** server + SPA shell + Conviction Console (real certainty).
- **N3** honest Arena leaderboard.
- **N4** Council + Signal Lab + Strategy scorecard + Evidence + Learn + final review loop.
