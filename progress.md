# QuantPilot BTC: complete project status

Last updated: 2026-10-20 (after the selective prediction research). This is the single document a person or AI agent should read first: what the project is, how it is built, what has been done, what was found, the rules for working on it, and what comes next. Update it whenever a body of work is finished.

## Contents
1. [What QuantPilot is](#1-what-quantpilot-is)
2. [Rules for anyone working on this project (read before changing anything)](#2-rules-for-anyone-working-on-this-project)
3. [Tech stack](#3-tech-stack)
4. [How to run, test and use it](#4-how-to-run-test-and-use-it)
5. [Architecture](#5-architecture)
6. [Code map](#6-code-map)
7. [Interfaces: dashboard, API, MCP tools, CLI](#7-interfaces)
8. [Data](#8-data)
9. [Timeline of work](#9-timeline-of-work)
10. [Research results](#10-research-results)
11. [Overfitting controls](#11-overfitting-controls)
12. [Decisions, rulings and deferred issues](#12-decisions-rulings-and-deferred-issues)
13. [Known limitations and operations](#13-known-limitations-and-operations)
14. [Current state and next steps](#14-current-state-and-next-steps)
15. [Glossary](#15-glossary)
16. [Where to find everything](#16-where-to-find-everything)

---

## 1. What QuantPilot is

QuantPilot BTC is a **local research workstation for Bitcoin perpetual futures** (BTCUSDT on Binance USD-M). It runs on the owner's Windows laptop and does five things:

1. **Collects market data continuously** into PostgreSQL: 5-minute candles, mark and index prices, funding, open interest, long/short ratios, sentiment, a macro calendar, news, and, since 2026-10-20, liquidations and order-book depth.
2. **Researches what, if anything, predicts BTC**, with strict overfitting controls. Every experiment reports failures as well as successes.
3. **Forecasts and answers questions** ("Will BTC go up in 4 hours?", "Chance it touches $120k this week?"). Numbers come only from methods that passed walk-forward validation; otherwise the answer is the honest base rate.
4. **Scores itself forward:** every forecast and answer is stored with a timestamp and scored once its horizon passes, against a base rate. Claude's own opinions are scored separately.
5. **Runs a paper (simulated) trading account** with chronological accounting of fees, slippage and funding.

**It never places real orders.** There is no order API. Research, forecasts, chat and signals never place or size trades.

**The honest headline so far:** the data predicts **how much** BTC moves (return ranges, volatility) but **not which way**. No direction strategy beat costs out of sample, including rules allowed to stay silent 90–99% of the time (section 10).

**Owner context:** the owner wants honest results with heavy overfitting control rather than impressive backtests. They chose to research both intraday (hours) and swing (days–weeks) trading, and preferred rare but accurate calls over frequent ones.

---

## 2. Rules for anyone working on this project

These are standing instructions from the owner and decisions already made. Follow them unless the owner says otherwise.

**Safety and money**
- Nothing may place real orders or size trades. Paper trading only.
- Claude and Codex reasoning must use **subscription logins only, never billed API keys**. Strip `ANTHROPIC_*`, `CLAUDE_CODE_*`, `QUANTPILOT_*` and `OPENAI*` keys from the environment of any `claude`/`codex` subprocess. `~/.claude/settings.json` has been checked: no `apiKeyHelper`, no env keys.
- Calls to `claude -p` must use:
  - the native executable `C:\Users\jaide\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe` (the `.cmd` shim mangles JSON);
  - the flags `--tools ""`, `--strict-mcp-config`, `--settings` with `{"disableAllHooks":true}`, `--disable-slash-commands`, `--no-session-persistence`;
  - an empty working directory, with untrusted text passed only through stdin.
- **Never use `--bare`**: it disables the subscription login. Model: `claude-sonnet-5-5`, effort low. Codex model: `gpt-6-luna`.
- API write endpoints accept loopback (127.0.0.1) clients only.

**Research integrity**
- Every new strategy or hypothesis must be **pre-registered** before it is evaluated: a new dated amendment in `docs/research/preregistration.md` giving its rule, parameter grid and pass rule. Earlier entries are never edited.
- Log every configuration tried in the trial registry. The overfitting corrections depend on the true count.
- The **holdout year 2025-10-20 → 2026-10-19 is untouched**. Evaluate it only once, and only for something that already passed development (`quantpilot research-strategies --holdout` refuses to run twice).
- Treat a backtest hit rate of 75–80% or more as a leakage red flag until proven otherwise.
- Historical news is not backfilled on purpose: a language model reading old headlines may already know what happened next.

**Engineering**
- Test-driven: write the failing test first, watch it fail, then implement. Run the full suite before claiming done.
- Match the surrounding code style. The code is compact, with few comments that explain *why*.
- Commits end with two attribution lines:
  - `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01FSBKv59fXns6hpnLLK8Aq8`
- Do **not** merge to `main`, push, or publish without asking the owner.

**The laptop**
- The machine is a Samsung Galaxy Book4 Pro (Core Ultra 7 155H, 16 GB RAM). It runs near its memory limit, usually with about 0.7–1.5 GB free.
- Long background jobs get killed by Claude Code's memory-pressure reaper. Make long jobs **resumable** (checkpoint results as they finish), stream database reads in chunks, and ask before restarting a reaped research job.
- **Never close VS Code or Chrome** (possible unsaved work). The owner approved closing Steam, Spotify and the Codex desktop app.
- When restarting services from an agent, launch them detached, e.g. PowerShell `Start-Process node scripts/start.cjs`. A restart from the Bash tool was reaped within minutes on 2026-10-20. The owner can also restart from their own terminal.

---

## 3. Tech stack

| Layer | Technology |
|---|---|
| Language (backend) | Python 3.11.11, managed with **uv** (`pyproject.toml`, `uv.lock`); virtual environment in `.venv` |
| API | **FastAPI** + **uvicorn** on 127.0.0.1:8000 |
| Settings | **pydantic-settings** (`QUANTPILOT_` environment prefix; risk settings `QUANTPILOT_RISK_`) |
| Databases | **PostgreSQL 18** (market data, port 55432, private instance configured by the launcher) and **SQLite** (`data/quantpilot.db`: paper ledger, forecasts, answers, chat). **SQLAlchemy 2** + **psycopg 3**; migrations with **Alembic** (`migrations/versions/0001`–`0008`) |
| Data | **pandas**, **numpy**, **pyarrow**/Parquet, **numba** |
| Machine learning | **scikit-learn** (logistic regression, isotonic calibration, pipelines), **XGBoost** (shallow, regularised), **SHAP** (explanations), **SciPy** (statistics), own EWMA and GARCH(1,1) |
| Networking | **httpx** (REST), **websockets** (Binance liquidation stream) |
| AI integration | **MCP** Python SDK (Model Context Protocol server and proxy); Claude Code CLI (`claude -p`, Sonnet 5.5) and Codex CLI (`codex exec`), both subscription logins |
| Other | **joblib** (model files), **filelock** (single supervisor, single chat) |
| Frontend | **React 19** + **TypeScript 5.9**, built with **Vite 6**; **Tailwind CSS 4**; **lightweight-charts 5** (trading chart), **Recharts 3** (research plots), **lucide-react** (icons) |
| Tests | **pytest** (36 test files, 257 tests), **Playwright** (10 browser specs), Node test for startup readiness |
| Runtime and tooling | Node 22 (`scripts/start.cjs` launcher, verify scripts), Windows 11, PowerShell and Git Bash |
| Version control | git, local only (no remote). `main` holds everything: the Codex snapshot `821b45c` plus all later work, fast-forward merged from `news-pipeline` on 2026-10-20 |

---

## 4. How to run, test and use it

| Action | Command |
|---|---|
| Install | `uv sync --locked --extra dev` then `npm --prefix frontend ci` |
| Start everything (PostgreSQL, API, dashboard, data supervisor) | `node scripts/start.cjs` |
| Restart services after code changes | `node scripts/start.cjs --restart-services` |
| Check services without restarting | `node scripts/start.cjs --check` |
| Dashboard | http://localhost:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Readiness report | `uv run quantpilot doctor` |
| Python tests | `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (about 3–10 minutes) |
| Browser tests | `cd frontend && npm run build && npx playwright test --reporter=line` |
| Everything | `node scripts/verify.cjs` |
| Ask a question | `.venv\Scripts\quantpilot ask "Will BTC go up in the next 4 hours?"` (add `--no-ai` for numbers only) |
| Connect Claude Code | `claude mcp add quantpilot -- "C:\Projects\BTC Quant\.venv\Scripts\python.exe" -m quantpilot.mcp_proxy` (needs the API running; `-m quantpilot.mcp_server` works without it) |

Latest results (2026-10-21): **333 Python tests pass; 26 browser tests pass, 2 skipped.** The Python tests include leakage canaries: a signal that peeks at the future must pass the strategy gate (it shows a 92% hit rate), and random or lagged signals must fail.

---

## 5. Architecture

```
 Binance REST/archives/websocket, Coinbase, Deribit, Alternative.me, FRED/ALFRED, Fed/BLS/CoinDesk RSS
                                   │
              scripts/data_supervisor.py  (one process, eight threads)
   collector · history backfill · paper worker · news · macro · shadow forecasts · outlook · forward data
                                   │
          PostgreSQL :55432 (market data)        SQLite data/quantpilot.db (ledger, forecasts, answers, chat)
                                   │
                    FastAPI 127.0.0.1:8000  (services.py → api.py)
             │                         │                              │
   React dashboard :5173      MCP proxy / MCP server            CLI: quantpilot <command>
   (Chart, Market, Prediction,   (Claude Code, in-app Chat agent)
    Research, Chat, News, Paper)
```

| Part | What it does | Where |
|---|---|---|
| Market database | 5m candles (`market_candles`) and every other family as JSON rows (`market_observations`, keyed by market, family and time) | `market_store.py`, migrations 0001–0008 |
| Ledger database | Paper ledger, snapshots, worker runs, news, macro events, shadow forecasts, outlook answers, chat threads | `store.py`, `data/quantpilot.db` |
| Data supervisor | **Eight threads:** collector (every 5 s), history backfill (every 30 min), paper worker, news (every 5 min), macro calendar (hourly), shadow forecasts (every 15 min), outlook cycle (hourly panel refresh + scheduled questions, scoring every 15 min), forward research data (liquidation stream + depth every minute) | `scripts/data_supervisor.py` |
| API | Read endpoints plus loopback-only write endpoints (ask, outlook, chat, AI view) | `api.py`, `services.py` |
| MCP | 16 tools; read-only except logging outlook answers and AI views | `mcp_server.py` (full), `mcp_proxy.py` (fast, forwards to the API) |
| Chat agent | Claude Sonnet via `claude -p` (stream-json, max 8 turns), allowed only `mcp__quantpilot__*` tools | `chat.py` |
| Dashboard | React + Vite, redesigned 2026-10-21: Overview home plus Chart, Ask, Research, Paper, News and System pages; top bar with live price and an Ask box (`/` to focus); hash links (`#overview`, `#chart`…) | `frontend/src/main.tsx`, `routes.ts`, `ui.tsx`, `pages/` |

---

## 6. Code map

All Python modules are in `src/quantpilot/`. "Codex" marks modules from the original build; the rest were added or extended by Claude on 2026-10-20.

| Module | Purpose |
|---|---|
| `config.py` | `Settings` (paths, database URLs, limits, news reasoners) and `RiskConfig` (fees 5 bps, slippage 3 bps, buffer 5 bps, risk per trade 0.25%, 2% daily loss limit) |
| `data.py` *(Codex)* | Public archive download with SHA256 checks; coverage audit (fails closed); family definitions |
| `history.py`, `collector.py`, `live.py` *(Codex)* | Resumable 5-year backfill; continuous REST collector; live ingestion into Parquet and PostgreSQL |
| `market_store.py` | Market tables, replay-safe upserts, coverage; `FORWARD_VALUES` for liquidations and depth |
| `store.py`, `snapshots.py`, `operations.py`, `diagnostics.py`, `recovery.py`, `repair.py` *(Codex)* | Ledger and locks, read projections, worker evidence, `doctor`, backups and restore drills, gap repair |
| `features.py` *(Codex)* | 106 causal 5-minute features (technical, derivatives, sentiment, regime) |
| `research.py`, `replay.py`, `benchmarks.py` *(Codex)* | Original walk-forward training and the frozen release `24c8238525d4`; holdout replay; buy-and-hold comparator |
| `engine.py`, `worker.py` *(Codex)* | One event reducer for backtests and forward paper trading; scheduled paper worker |
| `charting.py` *(Codex)* | 86 chart studies on completed bars |
| `news.py` | RSS ingestion; Claude and Codex reasoners with per-provider budgets and usage-limit backoff; quote-checked claims; shared `claude_json()` |
| `macro.py` | CPI, jobs and FOMC calendar with first-release (ALFRED) values; macro features |
| `event_study.py` | Market loader (`load_market`, monthly chunks), hourly panel builder, five-year 5% move study |
| `event_research.py` | Multi-horizon research harness: direction, ranges, drop risk, volatility; purged splits; validation-only champions; PBO |
| `forecaster.py` | Shadow forecaster: promotion gate, daily refit, forecasts every 15 minutes, forward scoring |
| `questions.py`, `analogs.py`, `outlook.py`, `answers.py`, `ask_ai.py` | Outlook Q&A: question parsing, similar-situations estimator, validated answer engine, answer storage and scoreboard, Claude explanation layer |
| `chat.py` | In-app chat agent restricted to QuantPilot tools |
| `services.py`, `api.py`, `cli.py` | Shared read services and envelopes; FastAPI routes; command line |
| `mcp_server.py`, `mcp_proxy.py` | MCP tools (full server and fast proxy) |
| `selective.py` | Selective-call scoring: triple barrier, costs, funding, Wilson interval, break-even, deflated Sharpe, pass rule (`gate`), trial registry |
| `signals.py` | The 10 pre-registered rule families and 2 meta-labeling primaries |
| `strategy_research.py` | Walk-forward selection over 6-month blocks, meta-labeling, `gate_all`, report, one-shot holdout behind a lock |
| `research_data.py` | Spot, Coinbase, Deribit DVOL and funding history fetchers; incremental Parquet caches; hourly and daily research panels |
| `forward_data.py` | Forward-only collectors: per-minute liquidations (websocket) and 1000-level depth snapshots |
| `context.py` | Trading agent, step A: causal multi-timeframe MarketContext (indicators 5m/15m/1h/4h, confirmed swing support/resistance, prior day/week levels, positioning, funding, 24h volatility, input ages, macro blackout, news and forecasts known by the cutoff) |
| `tradeplan.py` | Trading agent, step A: deterministic TradePlan (structural stop and target, net reward:risk ≥ 1.5, 0.25% risk sizing, exposure, margin, liquidation and exchange-minimum checks, evidence for and against, revalidation at fill) |
| `futures.py` | Trading agent, step A: versioned futures simulator `futures-v1`, one pure reducer for history and the forward paper account (next-open fills, stop-first ambiguity, mark liquidation, funding, tighten-only stops, 24h exit, daily and drawdown halts) |

Frontend (`frontend/src/`): `main.tsx` (tabs), `api.ts`, `MarketChart.tsx`, `TradingWorkspace.tsx`, `TradingViewPanel.tsx`, `ShadowForecasts.tsx`, `StrategyResearch.tsx`, `Chat.tsx`, `NewsPanel.tsx`, `MacroCalendar.tsx`, `PaperOperations.tsx`, `ResearchPlots.tsx`, `liveFeed.ts`, `preview.ts`, styles.

Scripts (`scripts/`):
- `start.cjs`: launcher.
- `data_supervisor.py`, `serve_api.py`, `stop_data_supervisor.py`, `configure_postgres.py`.
- `verify*.cjs|py`, `smoke_release.py`, `package_*.py`, `report_release.py`, `export_release.py`.
- 18 leftover one-off `fix*.cjs` patch scripts from the Codex era.

---

## 7. Interfaces

**Dashboard pages (redesigned 2026-10-21):**
- **Overview:**
  - price strip and live 5m chart;
  - outlook with a 1h/4h/24h toggle, the base rate and the 80% range;
  - market pulse: RSI, Fear & Greed, ADX, funding, open interest, long/short ratios;
  - next macro event with a countdown, latest headlines, the research verdict and system status.
- **Chart:** trading workspace with 86 studies, 7 timeframes and drawings.
- **Ask:** chat with Claude plus quick quant answers and the answer scoreboard. The top-bar Ask box sends a question here and answers it instantly.
- **Research:** what the research found, research forecasts, strategy research with the trading-agent results, and the original release model (collapsed).
- **Paper:** account stats, the frozen model's current decision and gates, feature contributions, execution ledger and decision audit.
- **News:** the macro calendar, plus news and AI analysis.
- **System:** readiness, data collection, forward-only collectors, paper-worker monitoring and historical coverage.

Interactions:
- every jargon term has a hover/focus tooltip (`glossary.ts`);
- sections are collapsible;
- an "updated Xs ago" indicator refreshes on click;
- other pages' code is prefetched after load;
- an error boundary keeps a malformed response from blanking the app;
- phones get a bottom tab bar.

**API** (`/api/v1/…`). The ones marked POST are loopback-only.

| Kind | Routes |
|---|---|
| Health and operations | `health`, `ready`, `operations` |
| Market | `candles`, `indicators`, `positioning`, `sentiment`, `coverage`, `market/storage`, `market/candles`, `market/studies` |
| Frozen model and paper account | `predictions`, `backtests`, `portfolio`, `decisions` |
| Context | `news` (`?compact=`), `macro` |
| Research | `forecasts`, `strategy-research` |
| Outlook Q&A | POST `ask`, POST `outlook`, POST `answers/{id}/ai-view`, GET `answers` |
| Chat | POST `chat`, GET `chat/threads`, GET `chat/threads/{id}` |

**MCP tools (16):**
- **Market:** `get_market_snapshot`, `get_indicators`, `get_market_positioning`, `get_sentiment`, `check_data_coverage`.
- **Frozen model:** `get_prediction`, `explain_decision`, `compare_backtests`.
- **Paper account:** `get_paper_portfolio`.
- **Context:** `get_news_events`, `get_macro_calendar`.
- **Research:** `get_shadow_forecasts`, `get_strategy_research`.
- **Outlook:** `ask_outlook`, `record_ai_view`, `get_answer_scoreboard`.

**CLI** (`quantpilot <command>`):

| Area | Commands |
|---|---|
| Data | `audit`, `repair-data`, `manifest`, `import-market`, `backfill-history`, `market-coverage`, `collect-live`, `research-data` |
| Original release | `train`, `backtest`, `publish-research` |
| Research | `event-study`, `research-events`, `research-risk`, `research-strategies [--output DIR] [--holdout]`, `research-agent --output DIR`, `validate-outlook`, `outlook-refresh [--full]`, `shadow-forecast` |
| News and macro | `collect-news`, `analyze-news`, `news-status`, `macro-calendar` |
| Q&A | `ask "<question>" [--no-ai]` |
| Operations | `init-db`, `status`, `doctor`, `backup-db`, `restore-check`, `worker`, `pause`, `resume` |

---

## 8. Data

| Family | Source | Range | Notes |
|---|---|---|---|
| 5m perpetual candles (OHLCV, trades, taker buy volume) | data.binance.vision archives + `fapi.binance.com` REST | 2021-10-19 → now; 525,888 of 525,888 bars | 15m–1w built from 5m |
| Mark and index price (5m) | Binance | same | premium = mark/index − 1 |
| Funding rate | Binance | same | actual settlement times (00/08/16 UTC) |
| Open interest + 3 long/short ratios | Binance `metrics` archives + `/futures/data/*` | same, about 81% of hours | Live calls fetch about 41 h, so a longer outage leaves a gap. 62 rows quarantined in the release audit; archive rows with OI ≤ 0 treated as missing in research |
| Fear & Greed | Alternative.me | daily | |
| Macro calendar (CPI, jobs, FOMC) | FRED, ALFRED first-release vintages, Federal Reserve | 198 events | no consensus forecasts, so no surprises |
| News | Fed, BLS, CoinDesk RSS; analysed by Claude and Codex | from 2026-10-19 only | forward only, on purpose |
| Research history (`data/research/*.parquet`) | Binance spot BTCUSDT daily/hourly; USD-M funding archives; Coinbase BTC-USD daily/hourly; Deribit DVOL hourly | spot daily 2017-08 →; funding 2020-01 →; hourly 2021-09 →; DVOL 2021-03 → | `quantpilot research-data` refreshes them and builds the hourly (44,293 rows) and daily (3,351 rows) panels |
| Liquidations (forward only) | Binance all-symbol `!forceOrder@arr` websocket on the `/market/ws/` path, BTCUSDT only, per minute | from about 2026-10-20 19:30 UTC; the legacy path delivered nothing before that (amendment A5) | a lower bound (Binance pushes at most one per second per symbol); rows exist only for minutes with liquidations |
| Order-book depth (forward only) | `/fapi/v1/depth` 1000 levels, every minute | from 2026-10-20 | size and imbalance within 5 and 15 bps of the mid, spread, how far the book reaches (about 16 bps) |

**Not collected:** options chains, on-chain flows, other exchanges' order books.

---

## 9. Timeline of work

| When | Who | What |
|---|---|---|
| Before 2026-10-20 | Codex | Built the platform: data acquisition and audit, 106 features, walk-forward training, released model `24c8238525d4`, paper engine and worker, PostgreSQL market store, chart workspace, the first nine MCP tools, recovery drills, operations monitoring. Imported as commit `821b45c` on `main`. |
| 2026-10-20 | Claude | Took over; repository initialised; work done on branch `news-pipeline`, then fast-forward merged into `main` the same day |
| | | **News pipeline:** continuous RSS, Claude and Codex reasoners, budgets and usage-limit handling |
| | | **Macro calendar** and the **five-year 5% event study** |
| | | **Multi-horizon research harness** with validation-only champions and PBO; the **shadow forecaster** with a promotion gate |
| | | **Market outlook Q&A and in-app Chat** (11-task plan), independent review, fix pass (commit `95d6274`) |
| | | **Fixes to seven verified defects:**<br>- direction "down" intent;<br>- the live price from completed candles only;<br>- scoring against the recorded price over gap-free windows (scoring v2);<br>- stale labels;<br>- the Claude-timeout fallback;<br>- Codex environment stripping;<br>- the smoke check and doc counts.<br>Commit `6dd7430`, branch `trading-agent` |
| | | **Trading agent, step A:** market context, trade plans, futures simulator (commit `5ef0cae`, branch `trading-agent`). Design: `docs/superpowers/specs/2026-10-20-trading-agent-step-a-design.md` |
| 2026-10-21 | Claude | **Cleanup:**<br>- unused imports and variables removed (side-effect imports kept and marked);<br>- `/backtests` 4.8 MB → summary;<br>- evidence JSON moved to `docs/evidence/`;<br>- Codex `fix*.cjs` scripts archived and deleted.<br>**Dashboard redesign:** Overview home, focused pages, tooltips, live price and Ask box (commits `50fafc7`, `f40e57d`) |
| | | **Selective prediction research**: progress record, pre-registration, trade-style scoring, new data, forward collectors, 10 rule families + 2 meta-labeled primaries, report, dashboard panel, independent review, fixes (commits `5453862` → `c030629`) |

`git log --oneline 821b45c..main` lists the 36 commits since the Codex import.

---

## 10. Research results

### Released paper-trading model (Codex era)
- Calibrated XGBoost on the sign of the 60-minute return; entry threshold 0.60; costs 5 bps fee + 3 bps slippage per side plus funding.
- Holdout (Aug–Sep 2026): ROC-AUC 0.539, Brier 0.2511, balanced accuracy 50.65%. **Zero trades**, 0% return. Buy-and-hold made +31.44% over the research span.
- Forward: 36 decisions in about 8.9 hours, zero fills.

### Multi-horizon research (221,213 five-minute cutoffs, 24 months)

| Question | Result |
|---|---|
| Direction, 1h / 4h / 24h | **No edge.** The base rate wins at every horizon (PBO 0.25 / 0.25 / 0.75) |
| 80% return range, 1h | Real skill: +3.8% vs unconditional, 4/4 folds, PBO 0.00; the macro calendar helps. Deployed. |
| 80% return range, 4h | +1.4%, passes at exactly half the folds. Deployed. |
| 80% return range, 24h | No skill; the unconditional range is used |
| Chance of a −5% touch in 24h | No skill |
| Volatility | EWMA at 1h and 4h; GARCH(1,1) at 24h |

### Five-year 5% move event study (Oct 2021 – Oct 2026)
- 143 drops and 175 rallies.
- After a 5% drop, the median next-24h return was +0.44% vs −0.32% for matched ordinary hours.
- High volatility, crowded longs and high funding raise the chance of a 5% drop (12.7–15.1% vs 3.2–3.6%; base rate 7.4%).
- Only 19 of 143 drops followed a CPI, jobs or FOMC release, about what chance would give.

### Outlook validation (44,064 hours)
- Similar-situations direction estimate: not promoted at any horizon.
- Touch probability: promoted but weak (Brier 0.09320 vs 0.09351).
- 80% ranges well calibrated: 82.5% / 83.6% / 74.5% / 79.9% / 82.3% / 83.2% at 1h / 4h / 12h / 24h / 72h / 7d.

### Selective strategy research (pre-registered, 2026-10-20)
- **Setup:**
  - 186 configurations across 10 rule families and 2 meta-labeled primaries, intraday and swing.
  - Every call is scored as a trade (take-profit, stop or time limit) after 16 bps round trip and funding, one position at a time.
  - Configurations are chosen walk-forward over 6-month blocks.
  - Pass rule: enough calls, the hit rate's 95% lower bound above break-even, positive mean, deflated Sharpe ≥ 0.95, PBO < 0.3, most years positive.
- **Result: no strategy passed.** The holdout year was therefore not used.
- **Closest: M1, trend side + meta-label (swing, 7-day hold).**
  - 295 calls (11.5% of days), 58.3% hit rate (95% CI 52.6–63.8%) vs 51.6% break-even, +0.79% per call.
  - Mostly BTC's uptrend: always long scores 52.9% and +0.25% per call, and M1's longs earned +1.15% vs +0.28% for shorts.
  - Fails PBO (0.42). Its deflated Sharpe is 0.01 as registered; it depends on the variance choice (amendment A3), but PBO fails regardless.
- **Intraday:** every family lost money after costs or had too few calls.
- **Independent review:** found no bug that could change the verdict.
- Full tables: [docs/strategy-research.md](docs/strategy-research.md).

### Trading agent step B (pre-registered, 2026-10-21)
- **Setup:** structure-based trade plans (stop behind support/resistance, target at the next level, reward:risk ≥ 1.5 or 2.0, 0.25% risk), entered by trend, best-plan or pullback rules plus a coin-flip control. Decisions hourly from 2021-10 to 2025-10, run in the futures simulator after all costs.
- **Configurations:**
  - Trend at reward:risk 1.5: 66 trades, 42.4% hit vs 44.6% break-even, +0.4% on the account.
  - Best plan at 1.5: 380 trades, −12.1% on the account.
  - Coin flip at 1.5: 266 trades, −5.6% on the account.
- **Walk-forward (out of sample):** 192 trades, 30.2% hit vs 36.9% break-even, −0.23% per trade, PBO 1.0. **Fail.** The holdout stays untouched.

**Bottom line:** this data predicts **how much** BTC moves, not **which way**, even when a rule may stay silent most of the time, and structure-based trade plans don't change that.

---

## 11. Overfitting controls
- Purged walk-forward splits, with identical timestamps across configurations.
- Scaling, calibration and feature selection fitted inside training windows only.
- Regularised models (strong L2; shallow XGBoost with large leaves and subsampling).
- Selection on validation only; the constant base rate wins when nothing beats it.
- PBO (probability of backtest overfitting) and promotion gates that need confirming results.
- **For strategies:**
  - pre-registration;
  - a trial registry of every configuration;
  - a deflated Sharpe ratio for the number of trials;
  - Wilson intervals;
  - per-year consistency;
  - drift baselines;
  - leakage canaries;
  - a holdout behind a lock.
- No historical news backfill. The historical months have been inspected; the true out-of-sample test is the **forward** record.

---

## 12. Decisions, rulings and deferred issues

### Outlook Q&A work
**Rulings:**
- EWMA(48) volatility at every horizon for touch and range answers (that is what was validated).
- The news module is imported as `news_cli` because `services.news()` already existed.
- No API key helper in Claude settings.
- The single-chat lock relies on one API process.
- Forged chat transcript lines aren't guarded against (only the owner can send messages).
- `hours_to_next_macro = 168` is correct.
- The daily limit gives a numbers-only answer instead of an error.
- The 3-second quick-answer target was not measured end to end.

**Deferred minor issues:**
- The scoreboard scans every answer each time.
- A chat timeout drops its partial reply, and the usage-limit message has no reset time.
- The proxy reports timeouts as "services not running".
- Similar-situations neighbours are spaced per day rather than ≥ 24 h apart.
- The touch gate is pooled across horizons.
- 1h/4h ranges show the wrong method label.
- The stale flag can flap at the 2-hour boundary.
- No TrustedHost middleware.
- Quick-button answers aren't saved to threads, and `testclient` is in the loopback set.
- The base rate has no Wilson interval.

### Selective strategy research
Pre-registration amendments (`docs/research/preregistration.md`):
- **A1, data facts found before evaluation:**
  - funding history starts 2020-01; the default rate is charged before that;
  - OI from 2021-10; OI ≤ 0 is treated as missing;
  - the taker share comes from candles instead of the metrics ratio;
  - the Coinbase premium includes USDT/USD moves.
- **A2, after the run:**
  - the holdout is not used;
  - liquidation exhaustion (I6) and depth imbalance (I7) are registered for evaluation no earlier than 2027-04-20.
- **A3, after the independent review:**
  - records the deflated-Sharpe variance choice, fixed for future tests as trials with ≥ 30 calls;
  - records the extra intraday meta features;
  - records the development cutoff;
  - adds depth reach fields.
- **Other rulings:**
  - Research caches are kept as Parquet under `data/research/` rather than in PostgreSQL.
  - Nothing passed, so Phase 6 became a verdict panel instead of shadow signals.

**Deferred minor issues:**
- PBO is coarse with 5–6 intraday folds.
- Funding at the exit bar is charged even when a barrier is hit inside it (conservative).
- Latent: meta-labeling a *passing rule family* would start from an in-sample first pick, and its holdout path raises `NotImplementedError`.

---

## 13. Known limitations and operations
- **Memory:** the laptop runs near its RAM limit; long jobs get killed (section 2). An always-on machine (for example a Mac mini M6 with 24 GB) would free memory and stop gaps in forward-only data.
- **Forward-only data:** liquidation and order-book data exist only while the supervisor runs. Gaps while the laptop sleeps or is off are permanent. About 25 minutes were lost on 2026-10-20 when a restarted supervisor was reaped.
- **Git:** everything is on `main`; there is no git remote, so nothing is backed up off this laptop except the PostgreSQL backups in `data/backups/`.
- **Deployment and fills:** no public deployment. Paper fills are bar-based and margin is approximate.
- **News:** only days of history, so it can't be evaluated yet.
- **Leftovers:** 18 `scripts/fix*.cjs` one-off patch scripts could be removed.
- **Flaky test:** `test_history_collector::test_collector_recovers_paginated_downtime…` reads the wall clock and can fail when a 5-minute boundary passes mid-test. That happened once, on 2026-10-20, under heavy memory pressure, when the full suite took 27 minutes; the test passes alone.
- **Logs:** services log to `.verification/services/{api,data-supervisor,dashboard}.log`.

---

## 14. Current state and next steps

**State on 2026-10-20:**
- All planned work is complete and merged into `main`. The tests are green.
- The services are running and recording depth every minute and liquidations as they happen.
- The Prediction tab shows the strategy verdict.

**Open choice for the owner:** add a git remote (for example a private GitHub repository) so the code is backed up off the laptop.

**Trading agent (owner request, 2026-10-20).** The goal is an experimental RL paper-futures account that bridges indicators, structure, forecasts, positioning and news. It runs in this order:

| Step | What | Status |
|---|---|---|
| A | Verify and fix the defects; MarketContext; deterministic TradePlans; versioned futures simulator | **done** (branch `trading-agent`, not merged) |
| B | Pre-register the trade-plan rules plus simple entry rules (amendment A4); evaluate walk-forward with the same costs and pass rule | **done: no edge.** Every rule lost after costs, and a coin flip did as well. See [docs/trading-agent-research.md](docs/trading-agent-research.md) |
| C | RL for trade management only (hold, exit, tighten stop, pull in target), compared against fixed exits | waiting for the owner's decision: management cannot create an edge the entries lack (only 13–14% of stopped trades were ever 1R ahead) |
| D | Experimental forward paper account ($10,000 or $1,000, 2× isolated), worker, persistence, dashboard and API/MCP inspection | after C |

**Rule engine (owner request, 2026-10-21; branch `rule-engine` from `trading-agent`, not merged).** This replaces steps C and D with the owner's rulebook as event-triggered playbooks plus ML, news and RL layers, in five milestones. Full write-up: [docs/rule-engine.md](docs/rule-engine.md). Spec: `docs/superpowers/specs/2026-10-21-rule-engine-design.md`. Amendments A6 and A7.

| Milestone | What | Status |
|---|---|---|
| M1 | Pattern features, triggers, rulebook (YAML + Excel), arbiter, outcome simulator, pre-registered historical test | **done: no playbook passed.** v1 −1.77% (50 trades); v2 −10.1% (180 trades), −0.35R per setup |
| M2 | Live engine (1m cycle), experimental `futures-v1` account, PostgreSQL journal, diagnosis codes, API/MCP, Rules page, chart levels | **running since 2026-10-21 13:23 UTC** (first decision at the 13:30 close) |
| M3 | ML filter (meta-labeling) + forecasting inputs | **done: shadow** (no playbook had the 200 earlier setups needed to train). By the owner's decision the XGBoost/forecaster record was removed from the rule engine; each setup instead shows what happened the last times it appeared |
| M4 | Crawl4AI (isolated `.venv-crawl`), Claude impact classification, NEWS_SHOCK triggers, veto | **built and tested: shadow** (forward-only data) |
| M5 | Bandit learner; trade-management RL (tabular Q + PPO) | learner **done: shadow**; it learned to skip almost every setup and now also learns from the live journal. Management RL **done: shadow** (tabular −0.23R and PPO −0.24R vs −0.35R for break-even; 95% interval includes 0) |

**Four paper accounts (owner request, 2026-10-22; amendment A8).** Four $10,000 paper futures accounts (A Careful … D Aggressive) take the best 10/30/40/50% of confirmed v2 setups by a frozen score.
- Leverage is 2–5× and risk is 1–2.5% per trade.
- Every decision and trade is recorded with its checklist ticks, a market snapshot and a Claude-written reason.
- Each account pauses after 30 trades for a review with the owner.
- In history every tier still lost on average (A −0.11R, D −0.15R per trade), so this is data collection.
See [docs/rule-engine.md](docs/rule-engine.md#four-paper-accounts-amendment-a8-2026-10-22).

Live check of step A on 2026-10-20: the context built 60 days of real data in 6 s. At 15:50 UTC both the long and short plans were correctly rejected: the price sat between nearby 4h levels, so reward:risk was below 1.5.

**Next steps:**
1. Keep the supervisor running as continuously as possible to build forward data.
2. **From 2027-04-20** (6 months of data): run the pre-registered I6 (liquidation exhaustion) and I7 (depth imbalance) tests exactly as in amendment A2, with the variance rule from A3. Implement the signals in `signals.py`, add the families with their own block settings, and run `research-strategies` into a new output directory.
3. After a few months of forward news: pre-register and test a news-augmented model against the deployed champions.
4. Watch the forward scoreboards (`/api/v1/answers`, `/api/v1/forecasts`) as they reach 30+ independent scored answers.
5. Any new idea: pre-register first; the holdout year is still available for one final test.
6. Optional cleanup: the deferred minor issues in section 12, and removing the `fix*.cjs` scripts.

---

## 15. Glossary
- **Base rate:** how often something happened historically (for example, BTC up over 4 hours about 50.6% of the time). The honest answer when nothing beats it.
- **Brier score:** penalty for a probability forecast (lower is better). Used to compare QuantPilot, Claude and the base rate.
- **Walk-forward:** train on the past, test on the next block, roll forward. Nothing from the future is used.
- **Purge:** a gap between training and test so overlapping outcomes can't leak.
- **PBO (probability of backtest overfitting):** how often the configuration that looked best in training ranks in the bottom half when tested. Below 0.3 is required.
- **Deflated Sharpe ratio:** the chance a result's Sharpe ratio is real after accounting for how many configurations were tried. At least 0.95 is required.
- **Triple barrier:** a call ends at a take-profit, a stop or a time limit, whichever comes first, like a real trade.
- **Hit rate / call rate:** the share of calls that made money after costs / how often the rule makes a call.
- **Break-even hit rate:** the hit rate needed to make zero, given the average win and loss.
- **Wilson interval:** a confidence interval for a hit rate.
- **Meta-labeling:** a simple rule picks the direction; a second model decides whether to act on it.
- **Pre-registration:** writing down the hypothesis, parameters and pass rule before seeing results.
- **Holdout:** data kept unseen until the final, one-time test.
- **Shadow forecast:** a recorded, scored forecast that never trades.

---

## 16. Where to find everything

| What | Where |
|---|---|
| This status | `progress.md` |
| Overview and commands | `README.md` |
| Selective research plan, pre-registration, results | `docs/prediction-plan.md`, `docs/research/preregistration.md`, `docs/strategy-research.md` |
| Earlier research | `docs/forecast-research.md`, `docs/event-study.md`, `docs/evaluation-report.md`, `docs/model-card.md` |
| Outlook Q&A design and plan | `docs/superpowers/specs/2026-10-20-market-outlook-qa-design.md`, `docs/superpowers/plans/2026-10-20-market-outlook-qa.md` |
| MCP setup | `docs/mcp-setup.md` |
| Operations, data platform, recovery | `docs/operations.md`, `docs/market-platform.md`, `docs/live-data.md`, `docs/recovery.md`, `docs/coverage-report.md`, `docs/data-dictionary.md`, `docs/architecture.md` |
| Research artifacts | `artifacts/strategy-research/run-20261020/` (report, trial registry, per-family calls); `artifacts/research-champion-20261020/`, `artifacts/research-risk-20261020/`, `artifacts/event-study/`, `artifacts/outlook/`, `artifacts/shadow/`; frozen release files `artifacts/released.joblib`, `holdout.lock` |
| Data caches | `data/` (Parquet, SQLite, backups), `data/research/` (research panels and caches) |
