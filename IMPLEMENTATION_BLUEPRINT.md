# Implementation Blueprint — QuantDesk → Working App
**Audience:** Claude Fable 5 (coding agent). Execute one milestone per session. Each milestone has a Goal, Tasks, Files, and a **Definition of Done (DoD)** — do not start the next milestone until the DoD passes. Copy the "Prompt to Fable" block verbatim to start each session.

**Ground rules (apply to every milestone):**
- Never flip paper→live or place a live order without explicit human confirmation at that moment.
- Secrets only in `.env` (gitignored from M0). Never print keys in logs/commits/output.
- Everything binds to `127.0.0.1`. No port exposed beyond localhost.
- Commit at every DoD pass with message `M<n>: <summary>`.
- The GUI (`gui/index.html`) already exists. Its `API` object is the contract — the backend must match it, not the other way around.

---

## Milestone 0 — Repo scaffold + tooling

**Goal:** Clean repo skeleton, env, CI-lite checks.

**Tasks**
1. Create structure:
   ```
   quant-platform/
     gui/index.html            # move existing file here
     api/                      # FastAPI backend
       main.py  routers/  services/  models.py  store.py
     research/                 # LEAN workspace (created by lean init in M4)
     strategies/lean/  strategies/nautilus/
     data/catalog/
     infra/docker-compose.yml  infra/docker-compose.paper.yml  infra/docker-compose.live.yml
     risk/limits.yaml
     logs_and_artifacts/
     tests/
     .env.example  .gitignore  Makefile  requirements.txt
   ```
2. `.gitignore`: `.env`, `lean.json`, `data/catalog/*`, `logs_and_artifacts/*`, `__pycache__`, `.venv`.
3. `requirements.txt`: `fastapi uvicorn[standard] pydantic sqlalchemy psycopg[binary] redis pyyaml pytest httpx`.
4. `Makefile` targets: `dev` (uvicorn reload), `test` (pytest), `stack-up`/`stack-down` (compose).
5. Python venv, install deps.

**DoD:** `make test` runs (0 tests OK); `git log` shows initial commit; `.env` absent, `.env.example` present.

**Prompt to Fable:** *"Execute Milestone 0 of IMPLEMENTATION_BLUEPRINT.md. Create the scaffold exactly as specified, verify DoD, commit."*

---

## Milestone 1 — FastAPI backend with the GUI's API contract (mock-backed)

**Goal:** Backend serving `gui/` and every endpoint the GUI needs — backed by an in-memory/SQLite store first so the app is fully working before any trading infra exists.

**API contract (mirrors the GUI's `API` object):**

| Method | Path | Returns |
|---|---|---|
| GET | `/api/state` | `{mode:"paper"\|"live", equity, day_pnl, total_pnl, max_dd, open_risk}` |
| GET | `/api/venues` | `[{name, env, lat_ms, ok}]` |
| GET | `/api/positions` | `[{symbol, venue, qty, avg_px, unrl_pnl, strategy}]` |
| GET | `/api/strategies` | `[{name, stage, sharpe_paper, state}]` |
| GET | `/api/feed?limit=50` | `[{ts, level:"info"\|"fill"\|"warn"\|"err", msg}]` |
| GET/POST | `/api/ideas` | journal CRUD; idea = `{id, name, stage:1-6, hyp, uni, sharpe, kill, gate:"pass"\|"block", gate_note}` |
| POST | `/api/ideas/{id}/promote` | 409 if `gate!="pass"`; 409 if stage 5→6 (must use wizard) |
| GET | `/api/parity/{strategy}` | `{lean:{...}, naut:{...}, tol:[{metric,observed,limit,ok}], pass}` |
| POST | `/api/parity/{strategy}/run` | triggers Nautilus re-run (stub until M5) |
| GET | `/api/risk` | limits + breaker states from `risk/limits.yaml` |
| POST | `/api/risk/simulate-breach` | trips breaker in store, emits feed event |
| POST | `/api/kill` | body `{confirm:"FLATTEN"}` else 400; flattens (stub until M6), returns `{flattened, halted, ms}` |
| POST | `/api/golive` | body `{venue, strategy, confirm:"<VENUE> GO LIVE", checklist:[...all true]}`; validates ALL items true + exact phrase, writes audit row, 4xx otherwise |
| GET | `/api/audit` | go-live/kill audit log |

**Tasks:** implement with routers per domain; SQLite via SQLAlchemy for ideas/audit/feed; seed with the GUI's current mock data; serve `gui/` as static at `/`; CORS off (same origin).

**DoD:** `pytest tests/test_api.py` covers: promote blocked when gate=block (409), golive rejects missing checklist item, golive rejects wrong phrase, kill rejects wrong confirm. `uvicorn api.main:app` → `http://127.0.0.1:8700` serves the GUI.

**Prompt to Fable:** *"Execute Milestone 1. Implement the full API contract table with SQLite + seed data, serve gui/ statically, write the four gating tests, verify DoD, commit."*

---

## Milestone 2 — Wire the GUI to the backend

**Goal:** GUI runs on real endpoints; zero behavior change on mock parity data.

**Tasks**
1. In `gui/index.html`, replace each `API.*` mock with `fetch('/api/...')` (keep method names/shapes identical; add a thin `async` wrapper + loading/error toast).
2. Poll `/api/state`, `/api/positions`, `/api/feed` every 5s; others on screen entry.
3. Kill switch → `POST /api/kill`; wizard final step → `POST /api/golive`; promotion → `POST /api/ideas/{id}/promote` and render 409 messages as blocked-gate toasts.
4. Add `?mock=1` query flag that falls back to the embedded mock `API` (offline demo preserved).

**DoD:** With backend up: create an idea in the GUI → appears in SQLite; promote a blocked idea → toast shows server 409 reason; complete wizard → row in `/api/audit` and banner goes LIVE from server state; kill switch → feed event persisted. `?mock=1` still works offline.

**Prompt to Fable:** *"Execute Milestone 2. Convert gui/index.html's API object to fetch calls against the M1 backend per spec, keep ?mock=1 fallback, verify DoD manually and describe results, commit."*

---

## Milestone 3 — Docker stack (Postgres, Redis, backend)

**Goal:** One-command reproducible stack.

**Tasks**
1. `infra/docker-compose.yml`: `api` (uvicorn, port 127.0.0.1:8700), `postgres:16` (volume), `redis:7`. `.env` supplies credentials.
2. Migrate store from SQLite → Postgres (SQLAlchemy URL from env; keep SQLite for `make test`).
3. Redis pub/sub channel `feed` — backend subscribes and persists to `feed` table; anything publishing to Redis appears in the GUI feed.
4. Healthchecks on all services; `make stack-up` waits for healthy.

**DoD:** `make stack-up` → GUI live at 127.0.0.1:8700; `redis-cli PUBLISH feed '{"level":"info","msg":"hello"}'` appears in GUI feed within 5s; `docker compose down && up` retains ideas/audit (Postgres volume).

**Prompt to Fable:** *"Execute Milestone 3. Compose stack with postgres+redis+api, migrate persistence, wire Redis feed channel, verify DoD, commit."*

---

## Milestone 4 — LEAN integration (research + backtests)

**Goal:** Real LEAN backtests feed the parity screen's LEAN column.

**Tasks**
1. `pip install lean`; `lean init` in `research/`; `lean login` is HUMAN action — pause and request it.
2. `lean project-create momo-etf-v3`; implement a real simple `QCAlgorithm` (e.g., 12-1 momentum on sector ETFs, weekly rebalance) in `strategies/lean/momo_etf_v3/`.
3. `api/services/lean_runner.py`: runs `lean backtest <project>` via subprocess, parses the output JSON (statistics: Sharpe, return, DD, trades, fees) into the parity `lean` shape, stores per-strategy result.
4. `POST /api/backtests/lean/{strategy}/run` + status polling; GUI Backtest screen "Re-run LEAN leg" button.
5. Export bar data used by the backtest to `data/catalog/` staging (CSV/parquet) for M5.

**DoD:** From the GUI, trigger a LEAN backtest of momo-etf-v3; parity screen's LEAN column shows real statistics parsed from LEAN's result file; exported data present in `data/catalog/`.

**Prompt to Fable:** *"Execute Milestone 4. Set up LEAN CLI (pause for human lean login), implement momo-etf-v3 QCAlgorithm, build the lean_runner service and endpoints, wire the GUI button, verify DoD, commit."*

---

## Milestone 5 — Nautilus backtest + real parity gate

**Goal:** The parity screen compares two real backtests and computes the verdict server-side.

**Tasks**
1. `pip install nautilus_trader`; build `ParquetDataCatalog` in `data/catalog/` from M4's export (write a `scripts/import_catalog.py`).
2. Port momo-etf-v3 to `strategies/nautilus/momo_etf_v3.py` (`Strategy` subclass: `on_start`/`on_bar`; same indicator params, same fee model as LEAN config).
3. `api/services/naut_runner.py`: runs `BacktestNode` over the identical window, extracts return/Sharpe/DD/trades/fees.
4. `api/services/parity.py`: computes diffs vs tolerances from `risk/parity_tolerances.yaml` (return ≤1pp, Sharpe ≤0.10, trades ≤2%, DD ≤1pp — editable); persists verdict; **promotion endpoint now reads this verdict** — an idea cannot pass stage 4 without a stored `pass`.
5. `POST /api/parity/{strategy}/run` now real.

**DoD:** Both legs run from the GUI on the same window; tolerance table shows computed diffs; deliberately break the Nautilus port (e.g., shift warm-up by one bar), re-run, verify the gate FAILS and promotion returns 409; fix, verify PASS.

**Prompt to Fable:** *"Execute Milestone 5. Build the catalog import, port momo-etf-v3 to Nautilus, implement naut_runner + parity service with YAML tolerances, make promotion depend on the stored verdict, run the break/fix DoD test, commit."*

---

## Milestone 6 — Paper trading node + venue adapters

**Goal:** Real paper trading; dashboard shows live paper state.

**Tasks**
1. Compose additions: `ib-gateway` (e.g., `ghcr.io/gnzsnz/ib-gateway` paper mode, credentials from `.env`) and `trading-node` (Nautilus `TradingNode` container).
2. `TradingNode` config: IB adapter → dockerized gateway; Binance/Bybit/Coinbase adapters with testnet/sandbox env flags. Start with momo-etf-v3 only.
3. Bridge node → backend: node publishes fills/positions/status to Redis channels (`fills`, `positions`, `node_status`); backend consumes → Postgres → existing GUI endpoints. Venue health from adapter connection events.
4. `POST /api/node/start|stop` (paper only at this milestone — live start must 403).
5. HUMAN actions to pause for: IB paper credentials, exchange testnet keys into `.env`.

**DoD:** Stack up → venue list shows real connection states; place/fill/cancel a test order per venue (Phase 5 checkpoint) visible in GUI feed and positions; node runs a full session unattended; `POST /api/node/start {"mode":"live"}` returns 403.

**Prompt to Fable:** *"Execute Milestone 6. Add ib-gateway and trading-node services, configure paper adapters (pause for human credentials), bridge Redis→backend→GUI, enforce paper-only start, verify DoD, commit."*

---

## Milestone 7 — Risk engine + real kill switch

**Goal:** Enforced pre-route risk layer; kill switch actually flattens.

**Tasks**
1. `api/services/risk_engine.py` + Nautilus-side risk config: per-asset-class limits from `risk/limits.yaml` (position caps, gross %, contract limits, price sanity band, max order notional). Use Nautilus's built-in `RiskEngine` config where possible; add a pre-route validator for the rest.
2. Circuit breakers: daily loss limit and max-DD auto-halt monitored by the backend against node P&L stream; on trip → publish `halt` command to node, disable order submission, mark breaker state, feed event.
3. Kill switch: `POST /api/kill` now sends flatten-all + halt to the node via Redis command channel; node cancels working orders, closes positions market, ACKs; backend measures elapsed ms and audits.
4. `POST /api/risk/simulate-breach` injects a fake P&L breach end-to-end.
5. Rejected orders logged to the GUI's rejection feed.

**DoD:** Kill switch from GUI flattens real paper positions and halts within target (<2s) — measured and audit-logged; simulated daily-loss breach auto-halts; an order exceeding limits is rejected pre-route and appears in the rejection log. (Phase 7 checkpoint.)

**Prompt to Fable:** *"Execute Milestone 7. Implement the risk engine, breakers, and real kill-switch command path per spec, run all three DoD tests against paper venues, commit."*

---

## Milestone 8 — Go-live path (guarded)

**Goal:** The wizard's flip does something real — under maximal guards.

**Tasks**
1. `infra/docker-compose.live.yml` override: live env flags per venue, live keys from separate `.env.live` (gitignored), one venue enabled at a time via env var.
2. Backend `POST /api/golive` (already validating checklist + phrase) now additionally requires: parity verdict pass, paper acceptance flag (manually set via `POST /api/ideas/{id}/accept-paper` with typed confirmation), risk breakers armed, kill switch live-tested flag. Missing any → 409 with reason.
3. On success: writes audit, sets venue+strategy live in config, requires HUMAN to restart stack with the live override — the API must NOT hot-flip; it stages the change and prints the exact command for the human to run.
4. First-trade watchdog: in live mode, first order capped to configured minimum size regardless of strategy sizing.

**DoD:** Wizard completes only when all server-side preconditions hold; the flip stages config and instructs the human; simulated live session (with keys absent) fails safe with clear error, no order attempted. **Do not test with real live keys in this session.**

**Prompt to Fable:** *"Execute Milestone 8. Implement the guarded go-live path exactly as specified — staged config, human-run restart command, first-trade cap, full precondition checks. Never use real live credentials. Verify DoD, commit."*

---

## Milestone 9 — Feedback loop + hardening

**Goal:** Post-trade analytics and operational polish.

**Tasks**
1. Nightly job (APScheduler in backend): compute realized-vs-backtest drift (slippage, realized Sharpe) per strategy from fills table; surface on dashboard as "vs backtest expectation".
2. Alpha-decay check: scheduled walk-forward re-backtest trigger (LEAN) with result diff vs frozen baseline; flag decayed strategies in the journal (gate → block, note).
3. Alerting: webhook (Slack-compatible) for err-level feed events, breaker trips, node disconnects.
4. Backups: nightly Postgres dump to `logs_and_artifacts/`; parameter changelog table written on any limits/tolerance edit.
5. `tests/`: end-to-end pytest (spin stack via compose, run gating scenarios) + `make e2e`.

**DoD:** Drift numbers render on dashboard from real fills; forced decay flag blocks a journal idea; test webhook fires; `make e2e` green.

**Prompt to Fable:** *"Execute Milestone 9. Implement drift analytics, decay checks, alerting, backups, and the e2e suite. Verify DoD, commit, then produce a final SYSTEM_OVERVIEW.md summarizing the running system."*

---

## Milestone 10 — Symbol Spine (universe service)

**Goal:** "Add symbol" in the GUI actually provisions data end-to-end.

**Tasks**
1. `api/services/spine.py` + `spine` table: symbol, asset class, tiers (ref/daily/minute/L1/L2), per-tier status + progress.
2. Tier workers (async, Redis-queued): `daily/minute` → `lean data download` + catalog export; `L1/L2` → venue/vendor capture jobs writing Parquet to `data/catalog` (crypto: venue websocket recorder container; equities: vendor API, e.g. Databento/Polygon — key in `.env`).
3. Endpoints: `POST /api/spine` (queue symbol+tiers), `GET /api/spine` (status for the chip bar), `DELETE /api/spine/{sym}` (deregister, keep data).
4. GUI symbol bar reads real spine state; progress dot per chip; feed events on tier completion.
5. Every list endpoint from M1 gains a `?symbol=` filter param — the GUI's global scope maps to it.

**DoD:** Add a new symbol in the GUI → daily+minute tiers land in the catalog automatically; chip turns green; all screens filter by it; L2 capture container writes depth deltas for a crypto symbol for 1h and the Microstructure screen replays them.

## Milestone 11 — HFT path (tick/L2 engine + latency instrumentation)

**Goal:** honest tick-level backtests and paper trading for the library's LOW-LAT strategies. Scope honestly: colocated COLO-tier is out of scope for this stack; crypto LOW-LAT is the realistic target.

**Tasks**
1. Catalog: L2 delta storage (`OrderBookDelta`) + L1 (`QuoteTick`/`TradeTick`) import pipelines; Nautilus `BacktestNode` configs running strategies on full book reconstruction.
2. Implement library #13 (book-anomaly filter) as a shared Nautilus `Actor` publishing a trade/no-trade gate on the message bus; then #6 (OFI momentum) and #16 (cross-venue arb) as the first two HFT strategies, consuming the gate.
3. Latency instrumentation: timestamp every hop (data-in → signal → order-out → ack → fill); persist tick-to-trade histograms; `GET /api/micro/{sym}` feeds the Microstructure screen (book, tape, OFI, latency percentiles, markouts, cancel ratio).
4. HFT-specific risk additions: max message rate, max open orders, cancel-ratio budget, per-strategy markout kill (auto-halt if 1s markout < −X bp over N fills).
5. Paper trade #6/#16 on Binance testnet from the stack; measure real RTT and record slippage vs backtest.

**DoD:** OFI strategy backtests on recorded L2 data with full book reconstruction; Microstructure screen shows recorded+live data; markout auto-halt fires in a simulated toxic-flow test; testnet paper run produces latency histograms.

## Milestone 12 — AI Strategy Plugin (multi-LLM)

**Goal:** verbal strategy → validated `quantdesk.strategy.v1` spec → code scaffold, with pluggable LLM providers.

**Tasks**
1. `api/services/ai/`: provider abstraction with drivers: `anthropic` (Messages API), `openai` (chat completions), `ollama` + `lmstudio` + `custom` (OpenAI-compatible local endpoints). Config per provider in `.env` / runtime; keys never logged or persisted to disk in plaintext beyond `.env`.
2. `POST /api/ai/parse {text, provider}` → LLM prompted with the JSON schema (few-shot from STRATEGY_GUIDE + 3 library entries) → validate with pydantic against `schemas/strategy_v1.json`; on validation failure, one automatic repair round-trip; fall back to the built-in rule parser (already in the GUI) when no provider configured.
3. Spec consumption: `POST /api/ideas/from-spec` creates a journal entry with the spec attached; **codegen**: render LEAN `QCAlgorithm` and Nautilus `Strategy` scaffolds from the spec via Jinja templates (`templates/lean_strategy.py.j2`, `templates/nautilus_strategy.py.j2`) — generated code is a starting scaffold flagged `requires_review: true`, never auto-deployed.
4. Guardrails: spec must contain kill_criterion and universe; generated strategies enter the pipeline at Idea stage and pass every existing gate like hand-written ones. The AI plugin gets no special path to paper or live.

**DoD:** Same example prompt parses identically via built-in parser and one real LLM (schema-valid both ways); codegen produces a LEAN scaffold that compiles and a Nautilus scaffold that instantiates; journal entry created from spec; a spec without kill criterion is rejected with 422.

## Milestone 13 — Library seeding

**Goal:** the 30 library strategies are first-class platform objects, ready to light up with real data.

**Tasks**
1. `library/strategies/*.yaml` — one spec file per library entry (schema v1, fields from HFT_STRATEGY_LIBRARY.md), version-controlled.
2. `GET /api/library` serves them (replacing the GUI's embedded copies); "Add to journal" instantiates a spec-linked idea.
3. For every RETAIL-tier entry (#18, #20, #28, #29): generate scaffolds via M12 codegen and run a real LEAN backtest on existing spine data so the library shows live tearsheets, not placeholders.
4. For LOW-LAT crypto entries (#5, #16, #17, #6-crypto): scaffolds + Nautilus tick backtests on recorded testnet data from M11.
5. Library screen gains per-entry backtest status chips (none / scaffolded / backtested / in-journal).

**DoD:** All 30 YAML specs validate against the schema; the 4 RETAIL entries have real LEAN tearsheets visible from the library; adding any entry to the journal carries its spec and enters the standard gate pipeline.

## Milestone 14 — Individual Trader Essentials (final phase)

**Goal:** everything a solo trader needs to *live with* the system — found by a 10-persona review (part-timer, tax-conscious, cost-conscious, journal-keeper, risk-averse, multi-account, event-aware, beginner, tinkerer, performance-obsessive).

**Tasks**
1. **P&L ledger + calendar** — `fills → daily_pnl` aggregation; per-day/strategy attribution; the GUI's P&L calendar and net-of-costs KPIs read from it.
2. **Costs tracking** — `costs` table (subscriptions, infra, commissions auto-summed from fills); "net after costs" is the headline number everywhere P&L is shown.
3. **Benchmark service** — SPY buy-and-hold equity curve on the same capital/period, computed server-side; alpha-vs-benchmark on the desk screen.
4. **Trade journal** — notes CRUD (date, strategy, tag: observation/incident/discipline), attached to days/trades; export with backups.
5. **Tax reporting** — realized P&L from fills (FIFO lots), wash-sale detection flags (US rules), CSV export per account/year. Clearly labeled "not tax advice — verify with your accountant."
6. **Accounts panel** — per-broker equity/buying-power/margin via adapters; margin-usage alert threshold.
7. **Dead-man's switch** — node-side watchdog: if heartbeat from the operator machine is lost > N min in live mode → flatten + halt + alert. Broker-side resting stop orders as backstop (configurable). Quarterly drill task auto-created.
8. **Alerts center** — rules table (price move, P&L threshold, disconnect, Sharpe decay) evaluated server-side; channels: email (SMTP), Telegram bot (python-telegram-bot; `/status`, `/pnl`, `/flatten` with typed confirmation), in-app.
9. **Backups/restore** — nightly Postgres dump + configs to `backups/` and optional S3/rclone target; `POST /api/backup`, restore script + documented drill; GUI export/download already works (client-side JSON/CSV).
10. **Economic calendar** — ingest a free econ-calendar source; strategies declare event sensitivities in their spec (`halt_on: [CPI, FOMC]`); risk layer enforces pre-event halts.
11. **Setup progress** — `/api/setup` computes real completion (accounts connected, spine tiers, backtests run, drills done) feeding the safety screen's progress bars.
12. **Market clock** — server-side session calendar (NYSE/CME/crypto) with holidays, exposed to strategies and GUI (GUI's ET clock already live client-side).

**DoD:** daily P&L calendar shows real aggregated fills; net-after-costs differs from gross by the costs table total; wash-sale flag triggers on a crafted test sequence; dead-man drill in paper mode flattens within N+1 min of killed heartbeat; Telegram `/flatten` requires typed confirmation and works end-to-end; nightly backup file appears and restore drill passes on a clean container; CPI event in the calendar halts a flagged strategy's entries.

**Prompt to Fable:** *"Execute Milestone 14 of IMPLEMENTATION_BLUEPRINT.md. Implement the twelve individual-trader essentials with their DoD tests, honoring all ground rules. Pause for human action on: SMTP credentials, Telegram bot token, backup target choice."*

## Human-action checklist (Fable must pause and ask at these points)
- M4: `lean login` (QuantConnect credentials)
- M6: IB paper credentials + crypto testnet keys into `.env`
- M8: everything — go-live is human-driven end to end
- M10: market-data vendor choice + API key (equity tick data costs money)
- M12: LLM provider keys (or confirm local-only via Ollama/LM Studio)
- M14: SMTP credentials, Telegram bot token, off-machine backup target
- Any time a real order (even paper) is first placed on a venue: announce before doing it.

## Sequence summary
M0 scaffold → M1 API (mock) → M2 GUI wired → M3 Docker stack → M4 LEAN real → M5 parity real (hard gate) → M6 paper trading real → M7 risk/kill real → M8 guarded go-live → M9 feedback loop → M10 symbol spine → M11 HFT/tick path → M12 AI plugin → M13 library seeding → M14 individual-trader essentials. The app is usable end-to-end from M2 onward; each later milestone swaps a mock for the real thing without touching the GUI contract.
