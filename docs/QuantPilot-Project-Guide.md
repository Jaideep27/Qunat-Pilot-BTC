# QuantPilot BTC: Complete Project Guide

Version 2026-10-21 · BTCUSDT perpetual futures research and paper trading · Never places real orders

## 1. Executive summary

**QuantPilot** is a local research platform for Bitcoin perpetual futures (BTCUSDT on Binance USD-M). It runs on one computer, collects market data around the clock, researches what (if anything) predicts Bitcoin, records forecasts and scores them against what actually happened, answers questions in plain language, and runs a simulated (paper) trading account.

**What it found, honestly:**

| Question | Answer |
|---|---|
| Can it predict whether BTC goes up or down in the next 1 to 24 hours? | **No.** Every model and 186 pre-registered strategies failed to beat a coin flip after costs. |
| Can it predict how much BTC will move? | **Yes.** 1h and 4h price ranges and next-day volatility beat simple baselines and hit their targets live. |
| Do support/resistance trade plans make money? | **No.** Every entry rule lost after costs; a coin-flip side did as well. |
| Has the paper account traded? | **No.** Its model was never confident enough. That is correct behaviour, not a bug. |

**Current accuracy (2026-10-21):**
- Direction: about 50%. The paper model scored 50.7% on its unseen test months, and the live forecasts match a coin flip.
- 80% price ranges: 87% (1h) and 84% (4h) of outcomes landed inside, against an 80% target.

**Why this project is valuable anyway:** it shows how to do quantitative research properly. It uses real data engineering, leakage-free validation, overfitting controls and pre-registration, and it honestly reports a negative result instead of an impressive but fake backtest.

## 2. What QuantPilot is and is not

**It is:**
- a 24/7 data collector;
- a research lab with strict anti-overfitting rules;
- a forecaster that scores itself;
- a question-answering assistant;
- a paper (simulated) trading account.

**It is not** a money-making bot. There is no code that can send an order to an exchange. Every "trade" is simulated in a local ledger, and research forecasts, strategy tests and chat answers never place or size trades.

## 3. How it works, end to end

| Step | What happens | Where |
|---|---|---|
| 1. Collect | Every 5 seconds the collector stores new 5-minute candles. History is backfilled to 5 years. Funding, open interest, long/short ratios, mark/index prices, Fear & Greed, macro releases and news are collected on their own schedules. Liquidations and order-book snapshots are recorded live. | `scripts/ops/data_supervisor.py`, `collector.py`, `history.py`, `news.py`, `macro.py`, `forward_data.py` |
| 2. Store | PostgreSQL holds market data. SQLite holds the paper ledger, forecasts, answers and chat. | `market_store.py`, `store.py` |
| 3. Compute features | 106 indicators are calculated from completed 5-minute bars, using only information known at that moment. | `features.py`, `context.py` |
| 4. Decide (paper) | Every 15 minutes the frozen XGBoost model predicts the 60-minute direction; the risk gates decide trade or no trade. | `worker.py`, `engine.py` |
| 5. Forecast (research) | Every 15 minutes the shadow forecaster records 1h/4h/24h direction probabilities, 80% ranges, drop risk and volatility. They are scored when their horizon passes. | `forecaster.py` |
| 6. Answer | Questions are parsed into a canonical form and answered with validated methods; Claude explains the answer; everything is scored later. | `questions.py`, `outlook.py`, `answers.py`, `ask_ai.py`, `chat.py` |
| 7. Serve | A FastAPI server feeds the dashboard, the MCP tools for Claude, and the command line. | `api.py`, `services.py`, `mcp_server.py`, `mcp_proxy.py`, `cli.py` |
| 8. Show | A React dashboard with seven pages. | `frontend/src/` |

**Background service threads (one process):**
- collector (every 5 s);
- history backfill (every 30 min);
- paper worker (minutes :02, :17, :32 and :47);
- news (every 5 min);
- macro calendar (hourly);
- shadow forecasts (every 15 min);
- outlook refresh and answer scoring (hourly / every 15 min);
- live liquidations and order book (every minute).

## 4. Technology stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 (uv for packages), TypeScript |
| API | FastAPI + uvicorn on 127.0.0.1:8000; write endpoints accept only this computer |
| Databases | PostgreSQL 18 (port 55432) for market data; SQLite for the ledger, forecasts, answers and chat; SQLAlchemy 2, psycopg 3, Alembic migrations |
| Data | pandas, numpy, pyarrow/Parquet, numba |
| Machine learning | scikit-learn (logistic regression, calibration, isotonic regression), XGBoost, SHAP explanations, SciPy statistics, custom EWMA and GARCH(1,1) |
| Networking | httpx (REST), websockets (live liquidations) |
| AI | Claude Code CLI (Claude Sonnet 5.5) and Codex CLI, both using subscription logins only (never billed API keys); MCP (Model Context Protocol) tools |
| Frontend | React 19, Vite 6, Tailwind CSS 4, TradingView Lightweight Charts, Recharts, lucide icons |
| Testing | pytest (334 tests), Playwright browser tests (26 passing, 2 skipped), Node structure checks |
| Platform | Windows 11 laptop, Node 22, PowerShell and Git Bash, git (branches `main` and `trading-agent`) |

## 5. Data

| Data | Source | Coverage |
|---|---|---|
| 5-minute candles (open, high, low, close, volume, taker buy volume) | Binance public archives + REST API | 2021-10 → now, 525,888 of 525,888 bars, no gaps |
| Mark and index price | Binance | same |
| Funding rate (every 8 hours) | Binance | same; archives from 2020-01 for research |
| Open interest; long/short ratios (all accounts, top accounts, top positions) | Binance metrics | same, about 81% of hours |
| Fear & Greed index | Alternative.me | daily |
| Macro calendar: CPI, jobs report, FOMC, with first-release values | FRED, ALFRED, Federal Reserve | 198 events from 2021 |
| News | Fed, BLS and CoinDesk RSS, analysed by Claude and Codex | from 2026-10-19 (never backfilled, to avoid hindsight) |
| Spot BTCUSDT daily/hourly, Coinbase BTC-USD, Deribit DVOL (implied volatility) | Binance, Coinbase, Deribit | daily from 2017-08; hourly from 2021-09 |
| Liquidations (live only) | Binance websocket | from 2026-10-20 |
| Order-book depth, 1,000 levels each minute (live only) | Binance REST | from 2026-10-20 |

## 6. Indicators and features (106 inputs)

All features use only completed bars and data available at that moment, so nothing peeks into the future.
- **Returns:** 5 minutes, 15 minutes, 1 hour, 4 hours, 24 hours.
- **Trend:**
  - EMA 9/21/50/200 and SMA 20/50/200, as distance and slope;
  - EMA crossover;
  - MACD and MACD histogram;
  - ADX.
- **Momentum:** RSI 7/14/21, Stochastic RSI, Stochastic, Williams %R, CCI, rate of change.
- **Volatility:** ATR, ATR as a fraction of price, realized volatility, Bollinger position and bandwidth, Keltner position, compression, range expansion.
- **Volume:** relative volume, volume change, volume z-score, OBV change, MFI, accumulation/distribution, VWAP distance, price-volume.
- **Candles:** body, upper and lower wick, close location, inside bar, engulfing.
- **Structure:**
  - 20- and 288-bar channel high/low, breakouts and retests;
  - swing high/low distance;
  - previous day and week high/low;
  - pivot, R1 and S1.
- **Multi-timeframe:** RSI, EMA 21/50 distance, ATR fraction, MACD and ADX on 15m, 1h and 4h, plus timeframe alignment.
- **Derivatives:** open interest and its change, price vs OI, the three long/short ratios, funding rate and its change, mark-index premium, taker imbalance, signed taker volume.
- **Sentiment:** Fear & Greed, its change, extreme fear or greed flags and how long they persist.
- **Time and regime:** hour of day (sine and cosine), weekend, regime.

## 7. Models and algorithms

### 7.1 The paper-trading model (frozen release `24c8238525d4`)
- **Algorithm:** XGBoost classifier with sigmoid calibration, so that "60%" really means 60%.
- **Question:** will BTC be higher in 60 minutes?
- **Training:**
  - every 5 minutes from 2024-10 to 2026-09;
  - 14 expanding walk-forward folds, with a 60-minute gap between training and test windows to stop overlapping labels leaking (a purge);
  - the final two months held back untouched.
- **Feature selection:** of the 106 inputs, training picked the 40 most useful. All 40 were price and technical indicators; open interest, ratios, funding and sentiment were offered but never chosen.
- **Largest recent influences (SHAP):** 24-hour return, 1-hour RSI, 288-bar channel low, EMA-200 slope, pivot distance, VWAP distance.
- **Result on the untouched holdout:** ROC-AUC 0.539, balanced accuracy 50.65%, zero trades.

### 7.2 The multi-horizon research harness
- **Direction (1h, 4h, 24h):** logistic regression and XGBoost on five feature groups (price, technical, derivatives, full, full + macro), with expanding or rolling-90-day training.
- **Return ranges:** ridge regression and quantile XGBoost (10th, 50th and 90th percentiles).
- **Large-drop risk (−5% within 24 hours):** XGBoost classifier.
- **Volatility:** EWMA vs GARCH(1,1), scored with QLIKE.
- **Selection:** on validation data only. The test data only confirms or rejects the choice.

### 7.3 Shadow forecaster
- Refits the winning ("champion") model for each horizon once a day on the latest two years of data.
- Records forecasts every 15 minutes, and scores each one when its horizon passes.
- It never trades.

### 7.4 Outlook question answering
- **Direction:** a "similar situations" estimator (nearest past market states). It failed validation, so the honest base rate is shown instead.
- **Touch probability** ("will BTC hit $X by T?"): a volatility barrier (reflection) formula, corrected with isotonic calibration. It passed validation but only weakly.
- **80% ranges:** volatility-scaled historical moves, validated at every horizon except 12 hours.
- **Claude's role:** explains the answer and gives its own probability, which is scored separately. Claude never changes the numbers.

### 7.5 Selective strategy research
- **What was tested:** 10 rule families plus 2 machine-learning filters (meta-labeling), for intraday (hours) and swing (days to weeks) trading:
  - breakouts;
  - momentum agreement;
  - funding crowding;
  - volatility squeeze;
  - Coinbase premium;
  - flush-out rebounds;
  - crowding fades;
  - session momentum;
  - taker flow.
- **How calls are scored:** every call is scored as a real trade: take-profit, stop or time limit, after fees and funding.

### 7.6 Trading agent: market context, trade plans, simulator
- **Market context:** one snapshot of everything at a moment in time: indicators on 4 timeframes, confirmed support and resistance, previous day and week levels, positioning, funding, the macro calendar and news.
- **Trade plans:**
  - stop just beyond the nearest support/resistance (1.5 to 4 ATR away);
  - target at the next opposing level, within the expected 24-hour range;
  - reward:risk of at least 1.5 after costs;
  - size risking 0.25% of the account.
- **Futures simulator (`futures-v1`):**
  - fills at the next bar's open;
  - if a bar touches both stop and target, the stop counts;
  - liquidation checked at the mark price, and funding applied;
  - stops can only tighten;
  - 24-hour time limit;
  - halts after a 2% daily loss or a 10% drawdown.

## 8. Paper trading rules

**When it trades.** Every 15 minutes, all of these must be true:
1. The model's probability that BTC rises in 60 minutes is **60% or more (long)** or **40% or less (short)**.
2. The expected move (probability × average gain − (1 − probability) × average loss) is larger than costs (0.16% round trip) + funding + a 0.05% buffer.
3. The data is fresh (under 10 minutes old) and complete.
4. No position is already open, the account hasn't lost 2% today, and it isn't paused.

**How a trade is run:**
- it risks 0.25% of the account;
- the stop is 1.5 × ATR away;
- the position is at most 25% of the account;
- it closes after 60 minutes or at the stop.

**What happens in practice:** the model's confidence stays between about 45% and 55%, so every check ends "no trade: weak forecast, insufficient edge".

## 9. How overfitting was controlled

| Control | What it prevents |
|---|---|
| Purged walk-forward splits | Training on the future, or on overlapping labels |
| Selection on validation only; constant base rate wins ties | Choosing models that only look good on the test |
| Pre-registration (`docs/research/preregistration.md`) | Changing the rules after seeing results |
| Trial registry (every configuration counted) | Hiding how many attempts were made |
| Deflated Sharpe ratio | Lucky winners among many attempts |
| PBO (probability of backtest overfitting) | Configurations that fall apart out of sample |
| Wilson confidence intervals and break-even hit rates | Mistaking noise for skill |
| One-shot holdout year (2025-10-20 → 2026-10-19, still untouched) | Testing until something works |
| Leakage canaries: a future-peeking signal must pass (92%), random ones must fail | Hidden leakage in the test machinery |
| No historical news backfill | Language models "remembering" what happened next |

## 10. Results in full

### 10.1 Direction research (221,213 five-minute cutoffs)

| Horizon | Champion | PBO | Verdict |
|---|---|---|---|
| 1h | base rate | 0.25 | no edge |
| 4h | base rate | 0.25 | no edge |
| 24h | base rate | 0.75 | no edge (one fold looked good, then failed: textbook overfitting) |

### 10.2 What did work

| Model | Result |
|---|---|
| 1h 80% range (with macro calendar features) | +3.8% better than the unconditional range, 4 of 4 folds, PBO 0.00 |
| 4h 80% range | +1.4%, passes at exactly half the folds |
| Volatility | EWMA best at 1h and 4h; GARCH(1,1) best at 24h |
| Touch probability | small but validated improvement (Brier 0.09320 vs 0.09351) |

### 10.3 Five-year 5% move event study
- 143 drops and 175 rallies of 5% or more.
- After a 5% drop, the next 24 hours had a median of +0.44%, vs −0.32% for comparable ordinary hours.
- High volatility, crowded longs and high funding made drops 2 to 4 times more likely, but not predictable enough to trade.
- Only 19 of the 143 drops followed a CPI, jobs or FOMC release, about what chance would give.

### 10.4 Selective strategies (186 configurations, pre-registered)
- **No strategy passed.**
- **Closest: trend + ML filter (swing, 7-day hold):**
  - 295 calls with a 58.3% hit rate, against a 51.6% break-even;
  - most of that is Bitcoin's 2017–2025 uptrend (always-long scored 52.9%);
  - failed the overfitting check (PBO 0.42).
- **Intraday:** every family lost money after costs.

### 10.5 Trading-agent trade plans (step B)

| Entry rule | Trades (4 years) | Hit rate | Break-even | Account |
|---|---|---|---|---|
| Trend (1h and 4h agree) | 66 | 42.4% | 44.6% | +0.4% |
| Best plan, either side | 380 | 33.9% | 38.9% | −12.1% |
| Pullback in trend | 11 | 27.3% | 40.7% | −0.9% |
| Coin flip (control) | 266 | 35.7% | 38.3% | −5.6% |

**Walk-forward:** 192 trades, a 30.2% hit rate against a 36.9% break-even, −0.23% per trade, PBO 1.0. **Fail.**

**Why trade management can't fix this:** only 13–14% of stopped-out trades were ever ahead by the full stop distance. When the direction can't be predicted, no exit rule can create profit.

### 10.6 Live accuracy so far

| What | Live result |
|---|---|
| Research direction forecasts (1h/4h/24h) | Brier score identical to the base rate (about 0.25): no edge |
| 80% ranges | 87% (1h) and 84% (4h) of outcomes inside: working |
| Chat up/down answers | 63.6% on 44 answers: luck from a rising day; will drift toward 50% |

## 11. Using QuantPilot

| Task | How |
|---|---|
| Start everything | `node scripts/start.cjs` (restart after code changes: `--restart-services`) |
| Dashboard | http://localhost:5173 |
| API documentation | http://127.0.0.1:8000/docs |
| Ask a question | Dashboard top bar (press `/`), the Ask page, or `quantpilot ask "Chance BTC touches 90k this week?"` |
| Use from Claude Code | `claude mcp add quantpilot -- "C:\Projects\BTC Quant\.venv\Scripts\python.exe" -m quantpilot.ai.mcp_proxy` |
| Run the tests | `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`, then `cd frontend && npx playwright test` |
| Re-run research | `quantpilot research-data`, `quantpilot research-strategies --output DIR`, `quantpilot research-agent --output DIR` |

**Dashboard pages:**

| Page | What's on it |
|---|---|
| Overview | price, chart, outlook (1h/4h/24h toggle), market pulse, next macro event, headlines, research verdict, system status |
| Chart | 86 indicator studies, 7 timeframes, drawing tools, live stream |
| Ask | Claude chat and answer scoreboard |
| Research | findings, forecasts, strategy and trading-agent results, original model evidence |
| Paper | account, current decision and gates, ledger, audit |
| News | macro calendar and news with AI analysis |
| System | readiness, collectors, worker runs, data coverage |

**API:**
- Read endpoints: candles, indicators, positioning, sentiment, coverage, predictions, backtests, portfolio, decisions, news, macro, forecasts, strategy-research, answers, chat threads.
- Write endpoints, this computer only: ask, outlook, chat, AI view.

**MCP tools (16):**
- market snapshot, indicators, positioning, sentiment, coverage;
- prediction, decision explanation, backtest comparison, paper portfolio;
- news, macro calendar, shadow forecasts, strategy research;
- ask outlook, record AI view, answer scoreboard.

## 12. Project history

| When | Who | What |
|---|---|---|
| Before 2026-10-20 | Codex | Built the platform: data pipeline and audits, 106 features, walk-forward training, the frozen release model, paper engine, chart workspace, first MCP tools, recovery drills |
| 2026-10-20 | Claude | News pipeline with two AI reasoners; macro calendar; 5% event study; multi-horizon research harness; shadow forecaster; outlook Q&A and in-app chat |
| 2026-10-20 | Claude | Pre-registered selective strategy research (no edge found); live liquidation and order-book collectors |
| 2026-10-20/21 | Claude | Trading agent steps A (context, trade plans, simulator, 7 defect fixes) and B (trade plans tested: no edge) |
| 2026-10-21 | Claude | Codebase cleanup; faster API; dashboard redesign; this guide |

Every piece of work was reviewed by an independent reviewer, and its findings were fixed test-first.

## 13. Known limitations
- **Memory:** the laptop (16 GB RAM) runs near its limit; long jobs can be killed and live data has gaps when it sleeps. An always-on machine fixes both.
- **Scope:** one market (BTCUSDT), so no cross-market strategies.
- **Paper engine:** bar-based fills and approximate margin.
- **News:** only days of history, so it can't be evaluated yet.
- **Liquidation data:** a lower bound (the exchange reports at most one per second); about 5.5 hours were missed on 2026-10-20.
- **Backup:** no git remote, so code exists only on this computer.
- **Authorship:** most code was written by AI assistants (Codex, Claude), so the owner should learn the core concepts before presenting it.

## 14. What's next
1. **Live-data tests (from 2027-04-20):** liquidation-exhaustion and order-book-imbalance tests, already pre-registered.
2. **Reliability:** Docker Compose on an always-on machine; a GitHub repository with automated tests (CI).
3. **MLOps:**
   - MLflow experiment tracking and model registry;
   - Grafana monitoring and alerts for data freshness, drift and calibration.
4. **Optional:** an experimental live paper account running the trade plans (step D), clearly labelled as having no edge.
5. **If seeking a real edge:** expand to many coins (relative-strength rotation), options data, or market making, rather than single-asset direction prediction.

**Important:** MLOps and better infrastructure improve reliability and speed, not prediction accuracy. Accuracy is limited by the information in the data.

## 15. Using this project on a resume

**Frame it as rigorous research, not as a profitable bot.**

Suggested bullets:
- Built an end-to-end BTC futures research platform (Python, PostgreSQL, FastAPI, React) with 5 years of exchange data, live websocket collectors and automated data-quality audits.
- Tested 190+ pre-registered trading strategies with purged walk-forward validation, deflated Sharpe ratios and PBO; found no direction edge after costs, but validated volatility and range forecasts (80% ranges at 84–87% live coverage).
- Integrated Claude-powered agents via MCP with read-only tools, and automatically scored every AI and model prediction against a base rate.

**Be ready to explain:**
- walk-forward validation and purging;
- data leakage;
- Brier score and calibration;
- PBO and the deflated Sharpe ratio;
- why transaction costs kill most strategies;
- why "no trade" is the correct outcome here.

## 16. Glossary

| Term | Meaning |
|---|---|
| Base rate | How often something happened historically; the honest answer when no model beats it |
| No edge | No method beat the base rate out of sample |
| Walk-forward | Train on the past, test on the next period, roll forward |
| Purge | A gap between training and test data so overlapping outcomes can't leak |
| Holdout | Data kept unseen until one final test |
| Pre-registration | Writing rules and pass criteria down before testing |
| Brier score | Penalty for probability forecasts; lower is better; a coin flip scores 0.25 |
| Calibration | Making a "60%" forecast come true about 60% of the time |
| PBO | Probability of backtest overfitting: how often the best-looking setting falls to the bottom half later |
| Deflated Sharpe | Chance a result is real after counting every attempt |
| Hit rate / break-even | Share of profitable calls / share needed to make zero |
| Triple barrier | A trade ends at take-profit, stop or time limit, whichever comes first |
| Meta-labeling | A rule picks the direction; a second model decides whether to act |
| 80% range | Interval expected to contain the outcome 8 times out of 10 |
| Funding | Payment between longs and shorts every 8 hours on perpetual futures |
| Open interest | Total open positions |
| Liquidation | A position forcibly closed by the exchange |
| MCP | Model Context Protocol: how Claude calls QuantPilot's tools |
| Shadow forecast | Recorded and scored but never traded |

## 17. Where everything is

| What | Where |
|---|---|
| Live status document | `progress.md` |
| This guide | `docs/QuantPilot-Project-Guide.md` and `.pdf` |
| Research write-ups | `docs/research/forecast-research.md`, `docs/research/strategy-research.md`, `docs/research/trading-agent-research.md`, `docs/research/event-study.md` |
| Rules registered before testing | `docs/research/preregistration.md` |
| Designs and plans | `docs/design/specs/`, `docs/design/plans/`, `docs/research/prediction-plan.md` |
| Python code | `src/quantpilot/` |
| Dashboard code | `frontend/src/` |
| Tests | `tests/`, `frontend/tests/` |
| Research artifacts | `artifacts/` |
| Data caches and backups | `data/` |
