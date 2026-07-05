# QuantDesk — Blueprint Status Matrix
Updated: 2026-07-05. Legend: ✅ done & verified · 🟡 partial (works, deeper version pending) · 👤 blocked on your action · ⏳ in progress

| Milestone | Status | What's real today | Remaining |
|---|---|---|---|
| M0 scaffold | ✅ | Repo, Makefile, env template, git history clean | — |
| M1 backend | ✅ | Zero-dep stdlib server, full API contract, SQLite persistence, all gates enforced server-side | FastAPI/Postgres upgrade optional (M3) |
| M2 GUI wiring | ✅ | GUI auto-connects, all mutations POST, offline `?mock=1` | — |
| M3 Docker stack | 🟡 | Docker verified (`hello-world` ✅); compose files ready; **Redis feed bridge live in server** — `docker run -d -p 127.0.0.1:6379:6379 redis:7` then `redis-cli PUBLISH feed '{"msg":"hi"}'` shows in GUI | Postgres migration deferred until FastAPI upgrade |
| M4 LEAN | 👤 | **LEAN CLI 1.0.227 installed** (`~/.quantdesk/venv/bin/lean`); strategy source ready | **You:** `~/.quantdesk/venv/bin/lean login` (QuantConnect credentials), then I take over: init, project, runner service |
| M5 Nautilus | 🟡 | Dual-engine parity gate fully working on real data; **nautilus_trader 1.230.0 installed** (python3.12 venv) | Port runner to real BacktestNode once installed |
| M6 paper trading | 👤 | Compose overlay + adapters config ready | **You:** IB paper credentials + exchange testnet keys into `.env` |
| M7 risk | ✅ | Order validation live (notional/band/per-class caps tested), breach simulation, kill switch with audit | Node-side flatten path needs M6 |
| M8 go-live | ✅ (by design 👤) | Server enforces checklist + phrase + parity; flip is staged-only, human restarts stack | Intentionally never automated |
| M9 feedback loop | 🟡 | `/api/drift` live — already flagged realized slippage 1.66bp > 1.0bp model ("re-fit slippage model") | Fills-derived paper Sharpe needs M6 |
| M10 symbol spine | 🟡 | Registry + real daily bars (Yahoo, 1,128 bars × 10 symbols) + **real L2 recorder** (Coinbase) | Minute-bar + long-horizon tick tiers need a paid vendor key (Databento/Polygon) |
| M11 HFT path | 🟡 | **Real order-book recording works**: 90 snapshots of live BTC-USD book+tape captured; `/api/micro` computes OFI/microprice from it; GUI shows real data. Binance is geo-blocked from your location — Coinbase used | Continuous capture + latency instrumentation need the live node (M6) |
| M12 AI plugin | ✅ | Server-side drivers for Anthropic/OpenAI/Ollama/LM Studio/custom, graceful builtin fallback (verified), spec schema enforced | Add a key to `.env` to activate a real provider (optional — builtin works) |
| M13 library | ✅ | 30 YAML specs served, status chips computed, add-to-journal | Real backtests per entry follow M4/M5 |
| M14 trader essentials | 🟡 | P&L calendar, costs, journal, alerts, exports, wash-sale detection, **auto daily backup (in-server)**, computed setup progress | Telegram bot + SMTP need tokens; dead-man switch needs live node |

## Your action list (everything else is done or waiting on these)
1. **QuantConnect**: run `~/.quantdesk/venv/bin/lean login` once → unlocks M4 fully.
2. **Broker/testnet keys** into `.env` (IB paper, Coinbase/Kraken sandbox) → unlocks M6, then M7's node flatten, M9 fills, M11 latency.
3. Optional: LLM key in `.env` (`ANTHROPIC_API_KEY=` or local Ollama) → real AI parsing.
4. Optional: paid tick-data key (`DATABENTO_KEY=`) → equity tick tiers in the spine.

## Honest scope notes
- Binance is geo-restricted from your network; crypto legs should use Coinbase/Kraken.
- The dual-engine parity gate is a faithful software stand-in for LEAN↔Nautilus until both are installed and logged in — same rules, same gate, swap-in interface.
- Colo-tier HFT (library entries marked COLO) remains out of scope on this hardware by definition.
