# QuantPilot BTC: project progress

Last updated: 2026-10-20. This file is the single place that describes what the project is, what has been built, what the research found, and what comes next. It is updated whenever a body of work is finished.

## 1. What QuantPilot is

QuantPilot is a local research workstation for **BTCUSDT perpetual futures** on Binance USD-M. It:

- collects market data continuously into PostgreSQL;
- researches what (if anything) predicts BTC, with strict overfitting controls;
- records forecasts and answers and scores them later against what actually happened;
- runs a **paper** (simulated) trading account.

It **never places real orders**. There is no order API. Nothing in research, forecasting or chat sizes or places trades.

### How to run it

| Action | Command |
|---|---|
| Start everything (PostgreSQL, API, dashboard, data supervisor) | `node scripts/start.cjs` |
| Restart services after code changes | `node scripts/start.cjs --restart-services` |
| Check services without restarting | `node scripts/start.cjs --check` |
| Dashboard | http://localhost:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Readiness report | `uv run quantpilot doctor` |

Requires Python 3.11 (`.venv`), Node 22 and the PostgreSQL 18 Windows binaries. The market database runs on port 55432.

## 2. Architecture

| Part | What it does | Where |
|---|---|---|
| Market database | PostgreSQL: 5m candles (`market_candles`) and every other family as JSON rows (`market_observations`), plus news, macro events, shadow forecasts, outlook answers and chat | `src/quantpilot/market_store.py`, `migrations/versions/0001`–`0008` |
| Ledger database | SQLite paper-trading ledger, snapshots, worker runs | `data/quantpilot.db`, `store.py` |
| API | FastAPI on 127.0.0.1:8000; write endpoints accept loopback clients only | `api.py`, `services.py` |
| Data supervisor | One process with seven threads: collector (5 s), history backfill, paper worker, news, macro calendar, shadow forecasts (every 15 min), outlook cycle (hourly) | `scripts/data_supervisor.py` |
| MCP tools | Read-only tools for Claude Code (plus logging outlook answers); a fast proxy forwards to the API | `mcp_server.py`, `mcp_proxy.py`, `docs/mcp-setup.md` |
| Dashboard | React + Vite. Tabs include Chart (86 studies, 7 timeframes), Prediction, Chat and operations views | `frontend/src/` |
| CLI | `quantpilot <command>` (argparse) | `cli.py` |

## 3. Data

| Family | Source | Range in the store | Notes |
|---|---|---|---|
| 5m perpetual candles (OHLCV, trades, taker buy volume) | data.binance.vision archives + `fapi.binance.com` REST | 2021-10-19 → now; 525,888 of 525,888 bars present | 15m–1w built from 5m |
| Mark and index price (5m) | Binance | same | premium = mark/index − 1 |
| Funding rate | Binance | same | actual settlement times |
| Open interest + three long/short ratios (global accounts, top-trader accounts, top-trader positions) | Binance `metrics` archives + `/futures/data/*` | same, about 81% of hours present | Live calls fetch 500 rows (~41 h), so a longer outage leaves a gap. 62 rows quarantined in the release audit. |
| Fear & Greed | Alternative.me | daily | |
| Macro calendar (CPI, jobs, FOMC) | FRED, ALFRED first-release vintages, Federal Reserve | 198 events, 2021 → scheduled future dates | no consensus forecasts, so no surprises are computed |
| News | Fed, BLS, CoinDesk RSS; analysed by Claude Sonnet 5.5 (`claude -p`) and Codex | from 2026-10-19 only | forward-collected on purpose: old headlines could leak hindsight |

**Not collected (yet):** liquidations, order book, options, on-chain, other exchanges. Phase 2 of the prediction plan adds some of these (section 11).

## 4. Timeline of work

| When | Who | What |
|---|---|---|
| Before 2026-10-20 | Codex | Built the platform: data acquisition and audit, 106 causal features, walk-forward training, released model `24c8238525d4`, paper engine and worker, PostgreSQL market store, chart workspace, MCP tools, recovery drills, operations monitoring. Imported as commit `821b45c` on `main`. |
| 2026-10-20 | Claude | Took over. All later work is on branch `news-pipeline` (not merged; no git remote). |
| | | News pipeline: continuous RSS, Claude and Codex reasoners with per-provider budgets and usage-limit backoff, claims checked against verbatim quotes |
| | | Macro calendar with first-release values; five-year 5% event study |
| | | Multi-horizon research harness (direction, ranges, drop risk, volatility) with validation-only champions and PBO |
| | | Shadow forecaster: refits the gated champions daily, records every 15 minutes, scores itself forward |
| | | Market outlook Q&A (questions parser, similar-situations estimator, touch probabilities, ranges, scoreboard), API/MCP/CLI, in-app Chat tab with a Claude agent restricted to QuantPilot tools |
| | | Independent final review; one fix pass covering the critical and important findings (commit `95d6274`) |
| | | Prediction plan approved (section 11); execution started |

Full commit list: `git log --oneline main..news-pipeline` (29 commits at the time of writing).

## 5. Research results (honest summary)

### Released paper-trading model (Codex era)
- Calibrated XGBoost, sign of the 60-minute return, entry threshold 0.60, costs 5 bps fee + 3 bps slippage per side plus funding.
- Holdout (Aug–Sep 2026): ROC-AUC 0.539, Brier 0.2511, balanced accuracy 50.65%. **Zero trades**, 0% return. Buy-and-hold made +31.44% over the same research span.
- Forward: 36 decisions in about 8.9 hours, zero fills.

### Multi-horizon research (221,213 five-minute cutoffs, 24 months)

| Question | Result |
|---|---|
| Direction, 1h / 4h / 24h | **No edge.** The base rate wins at every horizon (PBO 0.25 / 0.25 / 0.75) |
| 80% return range, 1h | Real skill: +3.8% vs unconditional, 4/4 folds, PBO 0.00; macro calendar helps. Deployed. |
| 80% return range, 4h | +1.4%, passes the gate at exactly half the folds. Deployed. |
| 80% return range, 24h | No skill; the unconditional range is used |
| Chance of a −5% touch in 24h | No skill (3 of 30 configuration-folds beat the base rate) |
| Volatility | EWMA at 1h and 4h; GARCH(1,1) at 24h |

### Five-year 5% move event study
- 143 drops and 175 rallies (Oct 2021 – Oct 2026).
- After a 5% drop, the median next-24h return was +0.44% vs −0.32% for matched ordinary hours; 43% kept falling.
- High volatility, crowded long positioning and high funding raise the chance of a 5% drop (12.7–15.1% vs 3.2–3.6% in the calmest fifth; base rate 7.4%).
- Only 19 of 143 drops followed a CPI, jobs or FOMC release, about what chance would give.

### Outlook validation (44,064 hours)
- Similar-situations direction estimate: not promoted at any horizon.
- Touch probability: promoted but weak (Brier 0.09320 calibrated vs 0.09351 historical frequency).
- 80% ranges: coverage 82.5% / 83.6% / 74.5% / 79.9% / 82.3% / 83.2% at 1h / 4h / 12h / 24h / 72h / 7d.

**Bottom line so far:** this data predicts **how much** BTC moves (ranges, volatility), not **which way**.

## 6. Overfitting controls in place
- Purged walk-forward splits; identical timestamps across configurations.
- Scaling, calibration and feature selection fitted inside training windows only.
- Regularised models (strong L2; shallow XGBoost with large leaves and subsampling).
- Champions chosen on validation only; a constant base rate wins when nothing beats it.
- PBO (probability of backtest overfitting) reported; promotion gate needs confirming test results.
- Historical news deliberately not backfilled.
- The historical months have been inspected; the true out-of-sample test is the **forward** record.

## 7. Outlook Q&A and Chat
- **Chat tab** (dashboard): ask about up/down, price levels, ranges, big moves or "why is BTC moving". Quick buttons give instant quant-only answers. Typed questions go to Claude Sonnet (subscription login only), which can use only QuantPilot's read-only tools.
- **CLI:** `.venv\Scripts\quantpilot ask "Chance BTC touches 120k this week?"` (add `--no-ai` for numbers only).
- **Claude Code:** `claude mcp add quantpilot -- "C:\Projects\BTC Quant\.venv\Scripts\python.exe" -m quantpilot.mcp_proxy`.
- **Scoreboard:** every answer is stored with a timestamp and scored once its horizon passes. QuantPilot's probability, Claude's probability and the base rate are compared by Brier score. A verdict appears only after 30 independent scored answers per question type and horizon. Shown below the chat and at `GET /api/v1/answers`.
- **Limits:** 100 asks and 100 chats per day; one Claude call at a time; a numbers-only answer when the limit is reached.

## 8. Tests
- Python: 211 tests (`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`, about 9.5 minutes on this laptop).
- Browser: Playwright, 21 passed and 2 skipped (`cd frontend && npm run build && npx playwright test --reporter=line`).
- Everything: `node scripts/verify.cjs`.

## 9. Decisions and deferred issues from the outlook work

**Rulings:**
- EWMA(48) volatility at every horizon for touch and range answers, because that is what was validated.
- The news module is imported as `news_cli` because `services.news()` already existed.
- `~/.claude/settings.json` has no API key helper, so chat can't be billed.
- The single-chat lock relies on the API running as one process (it does).
- Forged "ASSISTANT:" lines in the chat transcript aren't guarded against: only the owner can send messages.
- `hours_to_next_macro = 168` is correct: all releases are more than 7 days away.
- The daily limit gives a numbers-only answer instead of an error.
- The 3-second quick-answer target was not measured end to end.

**Deferred minor issues:**
- The scoreboard scans every answer each time.
- A chat timeout drops its partial reply, and the usage-limit message has no reset time.
- The proxy reports timeouts as "services not running".
- Similar-situations neighbours are spaced one per day rather than ≥ 24 h apart.
- The touch-probability gate is pooled across horizons.
- 1h/4h ranges show the wrong method label.
- The stale flag can flap at the 2-hour boundary.
- No TrustedHost middleware.
- Quick-button answers aren't saved to threads, and `testclient` is in the loopback set.
- The base rate has no Wilson interval.

## 10. Known limitations and operational notes
- The laptop (Core Ultra 7 155H, 16 GB) is near its RAM limit; long jobs get killed. Long runs go detached with checkpoints. An always-on machine (for example a Mac mini with 24 GB) would also stop gaps in forward-only data.
- Branch `news-pipeline` is not merged into `main`, and there is no git remote.
- No public deployment. Fills in the paper engine are bar-based and margin is approximate.
- 18 leftover `scripts/fix*.cjs` one-off patch scripts from the Codex era could be removed.
- News has only days of history, so it can't be evaluated yet.

## 11. Current work: selective prediction research

Plan: [docs/prediction-plan.md](docs/prediction-plan.md). Pre-registered hypotheses: [docs/research/preregistration.md](docs/research/preregistration.md).

The aim is better accuracy by **calling rarely**: predict only in unusual, pre-defined situations, score each call as a real trade would exit (take-profit, stop or time limit), net of fees and funding, and test intraday (hours) and swing (days–weeks) side by side. Every configuration tried is logged, the last 12 months are evaluated **once**, and anything that passes must then build a forward record before it is trusted.

| Phase | Status |
|---|---|
| 0. progress.md, plan, preregistration | in progress |
| 1. Selective evaluation (triple barrier, costs, Wilson bound, deflated Sharpe, trial registry) | pending |
| 2. Data: spot history 2017+, Deribit DVOL, Coinbase premium; forward liquidations and order book | pending |
| 3–4. Strategy families and meta-labeling | pending |
| 5. One-shot holdout and report | pending |
| 6. Shadow signals and Signals panel | pending |
