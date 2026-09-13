# nqq changelog

## v0.3 — hardening arc (multi-persona audit → fix → verify → commit loops)
Every claim the UI makes is now backed by what the code actually enforces, and the
concurrency/security/robustness edges are closed. All batches verified (40 unit tests +
23-check doctor + live flows) before commit.

- **Honesty labels tightened.** Arena/Strategy "Deflated Sharpe" → **P(edge)** (it's a
  probability in 0–1, not a Sharpe). Console session P&L/hit-rate/equity now carry a
  **SAMPLE SESSION** badge (seeded demo, not real fills). Microstructure depth is **always
  labelled synthetic** (only the mid can be live). Drift is **"recent window vs full
  backtest"** (in-sample), never "live". Council record chips say **"replay"**, not "live".
- **Real gates, not decorative ones.** Parity gate is a genuine **Welford vs two-pass
  reconciliation** of the same Sharpe (agrees ~1e-15; fails only on a real numerical bug).
  Lookahead **`assert_causal`** recomputes signals on truncated history and is wired into
  the scorecard hot path. IC p-values now **deflate the effective sample size** for forward-
  return overlap and signal autocorrelation before feeding FDR/"rated".
- **Security.** Password change **verifies the current password**; workspace import keeps a
  **kind whitelist** *and* **sanitizes** imported proposals/ideas (ownership → importer,
  lifecycle reset) so a bundle can't forge signed/entrenched governance or admin creds.
  POST **body-size cap**; cheap **/api/health**; admin-only controls hidden by CSS+role.
- **Concurrency & robustness.** `Store.mutate()` gives **atomic read-modify-write** (kills
  lost-update races in the order tape and evolve signatures/transitions). Provider cache
  **bounded + locked**; swallowed provider/LLM/council exceptions are **logged**. Limit &
  stop **order types** (next-bar H/L trigger, working state, maker-vs-taker fees) and
  **positions()/P&L** derived from real fills via average-cost accounting. Deterministic
  synthetic data now uses a **process-independent sha256 seed** (replayable evidence).
- **Accessibility.** Sidebar is keyboard-navigable (role=button, tabindex, Enter/Space,
  `aria-current`, focus outline); blocking `prompt()/alert()` replaced by an in-app
  **promise-based modal** (Enter/Escape, focus management).
- **Quant-methodology corrections** (deep stats audit; the honesty-core probabilities —
  PSR/DSR SE, E[max] deflation, Benjamini-Hochberg, annualization — were verified
  *correct*). Fixed the feeders: **`edge_bps` was a Sharpe·1e4** (dimensionless) instead
  of mean-return·1e4, which over-stated **capacity** by up to 1e4× and meant the capacity
  veto never bit — now a true per-trade return in bps (capacity dropped from ~$2B to a
  realistic ~$200k on the synthetic strategy). DSR now deflates against the operator's
  **empirical cross-trial Sharpe variance** when ≥5 trials are recorded (Bailey-LdP),
  with the sampling-SE fallback documented. Block bootstrap resamples are **truncated to
  n** with symmetric 5/95 CI quantiles; the impact coefficient is an explicit
  `IMPACT_COEF_BPS=10`; the permutation test is **relabeled** as a sign-flip mean test
  (it never tested ordering). Degenerate zero-variance series return an **honest Sharpe of
  0** (was leaking ~1e16 via a 1e-9 sd floor).
- **Route smoke test** — `tests/test_routes.py` boots the real server and asserts every
  GET route + safe POST routes return no 5xx, the GUI serves, and an oversized body is
  refused without crashing. **48 tests total** via `make test`.
- **UX fixes** — symbol drill-down no longer leaves the console poll running (was throwing
  on a null `#gauges` every 6s); the narrative bold regex actually renders `**bold**`
  (was matching literal backslashes); unconfigured data providers read as visually "off".
- **Signal/engine correctness** (dedicated signal+backtest audit; no lookahead/off-by-one
  found — the engine is verified causal). **Bollinger was byte-identical to
  mean-reversion** (double-counted as a distinct trial, inflating multiplicity; `cap_z`
  was dead code) → now a real band-touch signal, silent inside ±cap_z·σ. The backtest
  **charged a round-trip cost on one-way turnover** (spread ~2× too high) → now a one-way
  charge. RSI is now a standard Wilder RSI (SMA-seeded).
- **Security & multi-tenancy** (dedicated security audit; 10 findings). Fixed **3 HIGH
  multiuser tenant leaks**: the order tape/positions/P&L were one global doc (every user
  saw everyone's) → per-operator scoping; `journal.promote` had no ownership check (any
  user could promote another's idea to Live) → owner/admin-only; rooms had no
  read/membership isolation and silently auto-joined posters → member-filtered reads +
  explicit join. **Session/crypto**: password change now revokes all prior sessions
  (sub-second `pw_changed_at`) and re-mints the caller's cookie; constant-time login with a
  dummy-hash on unknown users; `Secure` cookie under `NQQ_TLS=1`; `NQQ_DEPLOY_KEY` env so
  multi-node deployments share one HMAC key. **Access**: login brute-force backoff (5
  fails/5 min → 429); `must_change` enforced (default `admin/changeme` blocked from data
  routes until changed); CSRF Origin check on state-changing POSTs. **Deployment**: sqlite
  WAL + `busy_timeout`; README now states the single-writer / shared-key horizontal-scaling
  constraints honestly (the hash-chained ledger forks under concurrent appends).

## v0.2 — comprehensive (pulled the full surface of the reference platforms)
Added the workflow, discovery, execution, learning-loop, AI, collaboration and data
layers, all honest-by-construction:
- **Research Journal** with the Idea→Research→Backtest→Parity→Paper→Live pipeline and
  real promotion gates (dual-recompute parity, deflated-Sharpe/capacity floors, forward
  paper record, typed Live confirm).
- **Alpha Discovery** (family×param×symbol search + FDR across the whole search),
  **Portfolio** construction (correlation-aware, diversification, portfolio capacity),
  **walk-forward** segments + Monte-Carlo **permutation** test.
- **Paper execution** (honest fill model: t+1, half-spread + sqrt impact) + **TCA / fill
  certificate** + **risk envelope** & **kill switch**.
- **Resolve-and-grade loop** — voices earn their live record; **drift** monitor;
  **per-regime** breakdown; auto **narrative** report.
- **AI Strategy Builder** (verbal→spec), **LLM arena agents** + **AI council voice**
  (keyless honest policy; real provider when `NQQ_LLM_KEY`), **live market-data** feed.
- **Rooms** (auditable collaboration), **Morning Brief**, **Journey** onboarding,
  **Data & Artifacts**, **Template Library** (honest — states each edge's failure mode),
  **Microstructure** (labelled synthetic), **Compare** (side-by-side + conflict flags),
  **CLI** (arena/history/verify/doctor/serve).
- **doctor** extended to 23 checks; 30+ unit tests. 20 nav screens. Still zero deps.


## v0.1 — honest quant, rendered beautifully
The honesty spine of a rigorous quant arena inside a gamified, zero-dependency shell.

**Kernel** — sqlite store; PBKDF2 auth + roles + cookie sessions + multi-user mode;
append-only hash-chained **stamp ledger**; **trial ledger** (multiplicity substrate);
HMAC **signed bundles** (write/verify; tamper fails).

**Honesty spine** (`stats.py`, pure Python) — Probabilistic & **Deflated Sharpe**
(Bailey–López de Prado); **block-bootstrap** Sharpe CI; **Benjamini–Hochberg FDR**;
**EdgeCertainty** (six axes, hard caps for synthetic ≤0.50 / no-forward ≤0.70) with a
**veto composite verdict**. `data.py` point-in-time cursor + **lookahead guard**.
`costs.py` **square-root market impact** + funding/borrow + **capacity ceiling**.
`signals.py` causal families with decay half-lives + honest IC / OOS split.
`backtest.py` engine (real costs, exec delay, regime, OOS) → **EdgeCertainty scorecard**.

**Arena** — deflated-Sharpe ranking with **operator multiplicity**, bootstrap CI bars,
capacity, trial counts, below-fold gating, the **"spectacle, not evidence"** banner, and
**signed, replayable week bundles** (even "no evidence-grade winner" is frozen & verifiable).

**Council** — hygiene H1–H5 (teach) + FOR/AGAINST/NEUTRAL voices with track record +
**evidence link** (no testimony without a link); **refuses** a combined probability while
voices are unrated; journal decisions to the ledger.

**Evolve** — governance proposals ledger: agents propose / humans dispose; legal
transitions only; **honesty-core changes need two signatures**; every proposal states a
falsifiable metric claim.

**Screens** — gamified **Conviction Console** (badge = real per-symbol EdgeCertainty),
symbol drill-down + council, honest **Arena**, **Signal Lab** (FDR across families, symbol
picker), **Strategy** EdgeCertainty scorecard, **Council**, **Evidence** (verify seal +
ledger integrity), **Learn**, **Admin/Governance**. Login + multi-user.

**Tests** — 24 invariant tests: DSR ≤ PSR, deflation falls with trials, bootstrap CI order,
FDR monotonicity, certainty caps + veto, lookahead guard, bundle roundtrip/tamper, ledger
chain, trial counts, scorecard honesty, council refusal + hygiene, arena ranking + signed
bundle, evolve legal transitions + two-signature honesty-core rule.

Zero third-party runtime dependencies. `make run` (or `make multiuser` / `make test`).
