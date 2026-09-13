# Collaboration — Study Groups, Strategy Sharing, Code Review
The platform is a git repository, which makes collaboration the same muscle professional quant teams use. This doc gives you three working workflows today plus the etiquette that keeps shared research honest.

## 1. Share the whole platform (study group / classroom)
Push to a private GitHub repo — secrets stay local by design (`.env`, `.env.live`, `data/catalog/*`, `backups/` are gitignored; verify with `git status` before your first push):
```bash
git remote add origin git@github.com:YOURNAME/quantdesk.git
git push -u origin master
```
Each member clones, runs `python3 scripts/gen_synthetic_data.py` (or `fetch_data.py` for real bars), adds their OWN Alpaca paper keys to their local `.env`, and double-clicks `Start QuantDesk.command`. Everyone gets the identical platform with their own paper account — same code, independent money. GitHub setup reference: https://docs.github.com/en/get-started/quickstart/create-a-repo

## 2. Share a single strategy
A strategy is fully described by three files — send them, or PR them:
- `library/strategies/NN-your-strategy.yaml` — the spec (schema `quantdesk.strategy.v1`): signal, entry/exit, sizing, risk, kill criterion
- `strategies/lean/<name>/main.py` and `strategies/nautilus/<name>.py` — both implementations
- the journal entry exports with **Full backup (JSON)** on the P&L & Journal screen

The receiver drops the YAML into `library/strategies/`, restarts the server, and it appears in their HFT Library screen with "Add to journal". Specs are the collaboration currency: precise enough to reimplement, small enough to code-review.

## 3. Review each other's work (the highest-value habit)
The parity gate is a built-in review protocol. When a member claims a strategy works:
1. They share the spec + both implementations + their parity screenshot.
2. YOU run both backtests on YOUR machine (`▶ Run dual backtest`) — same code, same data → results must reproduce. If they don't, the data provenance differs (check the window header) or the code was tuned after freezing.
3. Review checklist: Does the spec have a kill criterion? Are parameters on a plateau (Optimize screen) or a lucky peak? Is the warm-up ≥ the longest lookback? Does OOS/walk-forward exist, or only in-sample? What's the capacity claim based on?
Pull-request reviews on GitHub work well for this —
 the YAML diff IS the strategy diff. PR guide: https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests

## Competition mode (optional, fun)
Same repo, same strategy rules, everyone runs their own paper account for a month: compare `/api/drift` and P&L calendars at the end. Differences reveal execution skill (slippage, discipline overrides in the journal) rather than signal luck — a lesson no backtest teaches. QuantConnect also runs public competitions if you want a bigger arena: https://www.quantconnect.com/league

## Etiquette (what keeps shared research honest)
Never share `.env` or keys — each member uses their own paper account. Never commit tuned-after-freeze results: the git history is the tamper log; a strategy's backtest commit must precede its paper-trading commits. Report negative results — a strategy that failed parity or died OOS saves everyone else the week. And credit sources: if a strategy came from a paper (library entries cite theirs), keep the citation in the spec.

*Group learning multiplies progress, but every member must run their own risk drills. Shared code, individual accountability.*
