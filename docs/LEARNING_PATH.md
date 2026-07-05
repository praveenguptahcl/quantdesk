# Learning Path — Quant Trading, Taught by Doing
An 8-week curriculum for students and aspiring day traders, using this platform as the lab. Every week has reading, a hands-on exercise on YOUR running instance, and a check-yourself question. Prerequisite: none beyond basic Python. Time: ~4–6 h/week.

## Week 1 — What is a signal? (foundations)
**Do:** Open Strategy Lab → LIVE SIGNAL panel. Read `docs/SIGNALS.md` top to bottom with the panel open, matching each formula to its live number. Then `curl http://127.0.0.1:8700/api/signals | python3 -m json.tool`.
**Read:** Faber, *A Quantitative Approach to TAA* — https://mebfaber.com/timing-model/ — the regime filter's origin, 12 pages, very readable.
**Check:** Which ETF is ranked #1 today, and why is its *confidence* what it is? (Work the sigmoid by hand: `1/(1+e^(−3·mom/0.08))`.)

## Week 2 — Backtesting and its lies
**Do:** Backtest & Parity → ▶ Run dual backtest. Note Sharpe/DD/trades. Then Optimize & WF screen: find where the chosen parameters sit on the heatmap and articulate why the *plateau center* beats the best cell.
**Read:** Bailey & López de Prado, *Deflated Sharpe Ratio* — https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf — why your great backtest is probably luck.
**Check:** The strategy's real-data Sharpe is ~0.6 vs ~0.9 on synthetic data. List three reasons real markets are harder.

## Week 3 — The parity discipline (research ≠ production)
**Do:** ⚠ Run with injected bug. Study the trade-level diff table — trace WHY a 20-bar warm-up shortfall changes trade #1. Read STRATEGY_GUIDE.md §5 (the five parity traps). Fix-forward: re-run clean, confirm the gate passes, and note that go-live was *blocked* while parity failed (try the wizard during a failed state).
**Check:** Your friend's port shows 6% trade-count divergence. Name the two most likely causes before opening their code.

## Week 4 — Market microstructure
**Do:** Record fresh book data: `python3 scripts/record_l2.py BTC-USD 120`. Open Microstructure — watch OFI and microprice on YOUR recording. Compute by hand from one snapshot: microprice = (Pa·Qb + Pb·Qa)/(Qa+Qb).
**Read:** Cont, Kukanov & Stoikov (arXiv 1011.6402) §1–3 — the OFI paper behind library #6.
**Check:** Spread was ~0 bps on BTC-USD at Coinbase's touch. Why can a market maker still profit? (Queue position, adverse selection, fees/rebates.)

## Week 5 — Risk is the job
**Do:** Risk Controls → simulate breach; kill-switch dry-run then real activation (paper). Try to make the paper node violate a limit (`QD_CAPITAL=1000000 python3 scripts/paper_node.py`) and watch pre-route validation reject. Read `risk/limits.yaml` line by line.
**Read:** FINRA on pattern day trading — https://www.finra.org/investors/investing/investment-products/stocks/day-trading (the $25k PDT rule surprises many new traders).
**Check:** Why must the risk layer live OUTSIDE the strategy code?

## Week 6 — Paper trading operations
**Do:** Monday: `python3 scripts/paper_node.py` (dry-run), review, then `--execute`. Watch fills appear on the dashboard, positions populate, and the P&L calendar start filling from real Alpaca portfolio history. Log a journal note with tag `observation`.
**Check:** Your fill price vs the signal's close price — that difference is slippage. Where does the platform track it? (`/api/drift`.)

## Week 7 — Build YOUR first strategy
**Do:** Pick library #28 (VWAP-band reversion — RETAIL tier) or describe your own idea in the AI Builder. Get a spec with a kill criterion, implement the signal function following `current_signal()` as the template, backtest both engines, pass parity, paper trade it alongside momo.
**Check:** What is your kill criterion, and what will you actually DO when it triggers?

## Week 8 — The trader's life
**Do:** P&L & Journal daily for a week: notes with `discipline` tags when you override the system (you will). Set up alerts, run the dead-man drill, export trades CSV, check costs vs P&L. Read `docs/DAY_TRADER_REVIEW.md`.
**Check:** After costs ($~400/mo baseline here), what Sharpe do you need on $25k to beat simply holding SPY? Do that math honestly.

## Reference shelf (all links verified)
**Free/primary:** QuantConnect docs — https://www.quantconnect.com/docs/v2/ · NautilusTrader docs — https://nautilustrader.io/docs/latest/ · Alpaca docs — https://docs.alpaca.markets/ · QuantConnect Learning Center — https://www.quantconnect.com/learning · arXiv q-fin — https://arxiv.org/list/q-fin.TR/recent · Alpha Architect — https://alphaarchitect.com/
**Books (buy/borrow):** Ernie Chan, *Quantitative Trading* (the individual-trader classic); López de Prado, *Advances in Financial Machine Learning* (overfitting, labeling, backtest hygiene); Harris, *Trading and Exchanges* (microstructure bible); Aldridge, *High-Frequency Trading*.
**Community:** QuantConnect forum — https://www.quantconnect.com/forum/ · r/algotrading — https://www.reddit.com/r/algotrading/ · Quantitative Finance Stack Exchange — https://quant.stackexchange.com/

*This curriculum is educational. Paper trade for months, not days, before risking money you can't lose.*
