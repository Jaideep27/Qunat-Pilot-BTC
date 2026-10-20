# Prediction plan: selective BTC prediction research

## Context

QuantPilot's research so far is honest and negative. Over 1h/4h/24h, no feature group and no model beat the base rate for direction. The released paper-trading model (XGBoost, 60-minute sign, threshold 0.60) made **0 trades** on the holdout and 36 forward decisions with zero fills. Ranges and volatility are the only validated skills.

The user wants better prediction accuracy (they mentioned 70–80%) and asked me to research what serious quants and traders actually do. Their choices:
- **Hold time:** test intraday (hours) **and** swing (days–weeks) side by side.
- **Calling frequency:** rare but more accurate. The model abstains most of the time.

They also want a complete `progress.md` describing the project and everything done so far.

**Honest target:** 80% hit rate on BTC direction, calling often, is not achievable, and published claims of it come from leakage or overfitting. What *can* be real is a high hit rate on a small, selective subset of situations, plus positive expectancy after costs. Every number this plan produces is therefore reported as **hit rate at a stated call rate, with a confidence bound, net of fees and funding**. That is the only form of "accuracy" that cannot be gamed.

Why the current setup can't get there, and what changes:

| Today | Change |
|---|---|
| Must predict every bar | **Selective prediction:** call only in pre-defined unusual states; otherwise "no call" |
| Label = sign of fixed-horizon return | **Triple-barrier labels** (take-profit / stop / time limit), matching how a trade actually exits |
| Brier vs base rate | Hit rate + Wilson lower bound + net expectancy per call + call count; deflated for number of trials |
| 5m/hourly only, 2021–2026 | Adds a daily swing track with spot history from 2017 (more regimes) and new data families |
| Only Binance perp candles/positioning | Adds taker long/short ratio (already downloaded, unused), Deribit DVOL, Coinbase premium; forward collection of liquidations and order book |
| One model per horizon | Simple pre-registered rule signals first; then **meta-labeling** decides when to act |

## What was researched (summary)

- **Trend/time-series momentum:** BTC showed 1-day to 1-month momentum, but the effect weakened after futures launched (Swinkels, 2013–2018). Quantpedia's out-of-sample re-test (2022–2024) is mixed: the MAX breakout variant held up, the MIN variant did not. 1–8-week return persistence is reported. → **Swing track, testable on existing data.**
- **Funding/OI crowding and flush-outs:** large OI drops via liquidations often mark pivots (Glassnode counted 10 in 12 months). Our own event study: after 5% drops the median next 24h was +0.44% vs −0.32%; crowded longs and high funding raise drop frequency (12.7–15% vs 3–4%). → **Intraday track, testable on existing OI/funding history.**
- **Intraday momentum:** the first half-hour predicts the last half-hour (Reading paper). → Intraday track.
- **Order-flow imbalance:** real predictive power at seconds, but the predicted move is below the spread, so it is not tradeable without market-making infrastructure. → Collect forward, low priority.
- **Reinforcement learning:** published crypto DRL profits are largely backtest overfitting. Gort et al. had to reject overfitted agents explicitly, and their test was 2 months on 10 coins. → **Not used for prediction.** With ~1,800 daily samples and non-stationary regimes, RL memorises. This plan doesn't size trades, so RL has no role.
- **LLM agents as predictors:** can't be backtested, because the models already know past prices (look-ahead). → Used only as research assistants, never as signal sources.

## Approach

### Phase 0 — progress.md and guardrails (first)
1. **Write `C:\Projects\BTC Quant\progress.md`**: the complete project record (outline at the end of this plan).
2. **`docs/research/preregistration.md`**: every hypothesis below, written before any result is seen, with its exact signal definition, small fixed parameter grid, label, holding period and pass rule. Adding a hypothesis later is allowed only as a new dated entry.
3. **Trial registry** `artifacts/strategy-research/trials.jsonl`: one line per configuration ever evaluated (including failures). Overfitting corrections use this count.
4. Copy this plan to `docs/prediction-plan.md`.

### Phase 1 — evaluation upgrade (new `src/quantpilot/selective.py`)
- `triple_barrier(panel, side, tp, sl, max_hold)` → +1/−1/0 per call, plus the realised net return.
- **Cost model reused from `engine.py`:** 5 bps fee + 3 bps slippage per side, actual funding over the hold. Expectancy is always net.
- `selective_report(calls)` returns:
  - **Call metrics:** coverage (share of bars called), hit rate, Wilson 95% lower bound, break-even hit rate given barrier sizes and costs, mean and median net return per call, profit factor.
  - **Overlap-aware sample size:** number of *independent* calls (non-overlapping holds).
  - **Overfitting checks:** deflated Sharpe ratio using the registry trial count, and PBO via the existing `event_research.backtest_overfitting()`.
- Reuse `event_research.split()` (purged walk-forward) and `forecaster.promote()` gate logic.
- **Leakage canary tests:**
  - A signal computed from *future* prices must pass. This proves the harness can detect skill.
  - The same signal shifted to strictly past data, plus a random signal, must fail.

### Phase 2 — data
Existing history (backfill, months to minutes):
- **Spot BTCUSDT daily/hourly klines, 2017→now** from data.binance.vision. This gives the swing track 9 years and more regimes.
- **Binance `metrics` taker long/short volume ratio:** already in the downloaded daily archives, currently unused.
- **Deribit DVOL** (implied vol index, public API history): implied minus realised volatility (the variance risk premium).
- **Coinbase BTC-USD hourly candles** (public API) → Coinbase premium vs Binance, a US-demand proxy.

Forward-only (start the clock on day 1, testable after ~3 months):
- **Liquidations:** Binance `forceOrder` websocket. History isn't available.
- **Order book:** `/fapi/v1/depth` top-20 snapshot every minute → imbalance and depth.

**Storage:** existing `market_observations` (family + JSON), so no new tables. Collectors are added in `live.py`/`data.py` following the existing family pattern and run by the data supervisor.

### Phase 3 — pre-registered strategy families (`src/quantpilot/signals.py`)
Each signal is a few lines with ≤ 3 parameters × ≤ 3 values, and outputs long / short / no call.

**Swing track (daily bars, hold 1–14 days, spot 2017+, perp 2019+):**
1. **Trend / breakout:** 20/55-day Donchian breakout, 7–90-day momentum sign agreement, MA-slope filter. Call only when lookbacks agree.
2. **Crowding reversal / squeeze:** funding in the top or bottom decile of its trailing year, combined with OI change and price trend direction.
3. **Volatility compression → breakout:** realised-vol percentile < 20%, direction from the trend filter; DVOL–realised gap as a modifier.
4. **Coinbase premium persistence:** sustained premium or discount → follow it.

**Intraday track (hourly bars, hold 1–24h, 2021+):**
5. **Flush-out rebound:** OI drop > X% in ≤ 4h together with a price drop > Y% → long, with a stop.
6. **Crowded-long fade:** global long/short ratio and funding both in the top decile, price stalling → short or avoid longs.
7. **UTC-session intraday momentum:** first-hours return predicts the rest-of-day direction.
8. **Taker-flow persistence:** taker long/short ratio extremes that persist across several hours.
9. *(After 3 months of forward data)* Liquidation-cascade exhaustion and order-book imbalance.

**Training window** (the user's "maybe 5 years doesn't make sense"): each family is tested with expanding, rolling-1y and rolling-2y windows, and with exponential recency weighting (half-life 180d). The choice is made on validation only. The structural breaks (2017 futures, 2020–21 bull, 2022 crash, 2024 ETFs) become the yearly sub-period check: a signal must not depend on a single regime.

### Phase 4 — meta-labeling (only for signals that pass Phase 3 on development data)
- The rule signal decides the side.
- A shallow model (L2 logistic, then XGBoost depth ≤ 3) decides **whether to act**, using the existing feature groups from `features.feature_groups` plus the new families.
- This is the "rare but accurate" mechanism. The threshold is chosen on validation for the target call rate (about 2–10% of days or hours). Report the coverage–accuracy curve.
- An equal-weight ensemble of the surviving signals is tested as one extra trial.

### Phase 5 — one-shot holdout and pass rule (fixed in the pre-registration)
- **Development span:** start → 2025-10-19. **Holdout:** 2025-10-20 → 2026-10-19, evaluated **once**, after all choices are frozen.
- This span was seen by earlier, unrelated tests (outlook confirmation year), never by these strategies. `progress.md` will state that.
- **A strategy passes only if all of these hold:**
  - pooled out-of-sample walk-forward calls: ≥ 100 independent calls, and Wilson lower bound above the break-even hit rate;
  - holdout: net expectancy > 0, same side of break-even;
  - PBO < 0.3;
  - deflated Sharpe > 0 at 95%, using the registry trial count;
  - positive in most yearly sub-periods.
- The report shows **every** trial, including failures, in `docs/strategy-research.md`.

### Phase 6 — forward shadow and UI
- Passing signals are added to the shadow forecaster (`forecaster.run_cycle`). They are recorded only and never trade.
- They are scored on the existing scoreboard machinery (`answers.evaluate` / forecast scoring).
- New "Signals" panel on the Prediction tab: each call, its outcome, running hit rate with confidence bound, and net result vs buy-and-hold.
- **Promotion to paper trading** needs ≥ 3 months forward and ≥ 20 independent calls consistent with the backtest. That would be a separate decision, asked for at the time.

## Files

| New | Purpose |
|---|---|
| `progress.md` | Full project record (Phase 0) |
| `docs/prediction-plan.md`, `docs/research/preregistration.md`, `docs/strategy-research.md` | Plan, hypotheses, results |
| `src/quantpilot/selective.py` | Triple-barrier labels, cost-aware selective metrics, Wilson/deflated Sharpe, trial registry |
| `src/quantpilot/signals.py` | Pre-registered signal functions (both tracks) |
| `src/quantpilot/strategy_research.py` | Walk-forward runner + meta-labeling; CLI `research-strategies --track {swing,intraday} --output` (resumable via `progress.jsonl`, like `risk_experiment`) |
| `tests/test_selective.py`, `tests/test_signals.py`, `tests/test_strategy_research.py` | Unit and leakage-canary tests |

| Modified | Change |
|---|---|
| `data.py`, `live.py`, `history.py` | New families: spot klines, metrics taker ratio, DVOL, Coinbase candles, liquidations (ws), depth snapshots |
| `scripts/data_supervisor.py` | Run new forward collectors |
| `event_study.py` | Daily panel builder alongside `hourly_panel` |
| `forecaster.py`, `services.py`, `api.py`, `mcp_server.py`/`mcp_proxy.py` | Record and serve shadow signals |
| `frontend/src/` (Prediction tab) + Playwright spec | Signals panel |

**Reused as is:**
- `event_research.split`, `champions`, `backtest_overfitting`
- `forecaster.promote`
- `engine` cost constants
- `features.feature_groups`
- `event_study.hourly_panel` / `load_market`
- `outlook.refresh_panel` caching pattern
- `answers` scoring

## Constraints carried forward
- Nothing places or sizes trades. Signals are recorded and scored only.
- Claude/Codex via subscription logins only, never API keys.
- Laptop RAM is near its limit: long runs go detached with checkpoints; no aggTrades/tick downloads; daily and hourly panels only.
- TDD on every module.
- The branch stays `news-pipeline` (unmerged) unless the user decides otherwise.

## Verification
1. **Unit tests:** triple-barrier hits on synthetic paths; Wilson bound values; break-even computation with costs; deflated Sharpe falls as the trial count rises.
2. **Leakage canaries:** the future-peeking signal passes the gate; the random and lagged-noise signals fail. Run on the real panel.
3. **Baselines reproduce:** buy-and-hold and the always-long/always-flat signals produce the expected net numbers. A 50/50 random caller sits near 50% with the lower bound below break-even.
4. **Full Python suite** green (`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`). Frontend build and Playwright green.
5. **End-to-end:** `quantpilot research-strategies --track swing` and `--track intraday` on development data; the report lists every trial. Then the single holdout run. Then shadow signals appear at `/api/v1/forecasts` and in the Signals panel after a supervisor cycle.
6. **Forward collectors:** after 24h, liquidation and depth rows exist with no gaps longer than 5 minutes (checked by the existing coverage audit).

## Expected outcome (stated up front)
- The most likely result is that most families fail.
- At best, one or two survive with something like a **55–65% hit rate on a few percent of days or hours**, with modest positive net expectancy.
- If a backtest shows 75–80%, that is treated as a red flag and investigated for leakage before anything else.
- Whatever passes still needs the 3-month forward record before anyone should trust it.

## progress.md outline (Phase 0 deliverable)
1. What QuantPilot is: purpose, never-trades rule, how to start it (`node scripts/start.cjs`), URLs.
2. Architecture: services, supervisor threads, PostgreSQL/SQLite, API, MCP, frontend tabs.
3. Data: every family, source endpoint, date range, coverage status and known gaps.
4. Timeline: Codex build (initial import) → handover 2026-10-20 → each commit group (news pipeline, macro calendar, event study, research harness, shadow forecaster, outlook Q&A/chat, review fixes).
5. Research results with numbers:
   - direction has no edge (PBO);
   - ranges deployed at 1h/4h;
   - drop risk has no skill;
   - volatility: EWMA/GARCH;
   - event study;
   - outlook validation;
   - released paper policy: 0 holdout trades, buy-and-hold +31.44%.
6. Overfitting controls in place.
7. Outlook Q&A and Chat: how to use them, the scoreboard, limits.
8. Tests: counts and commands; latest results (211 Python / 21 browser).
9. Rulings made and deferred minor issues (from the final review).
10. Known limitations and operational notes (RAM, reaped jobs, unmerged branch, no remote, leftover `fix*.cjs` scripts).
11. Next steps: this prediction plan.

## References
- Swinkels et al. seminar abstract: https://utilities.tinbergen.nl/?p=66235
- Quantpedia, trend vs mean reversion in Bitcoin (out-of-sample re-test): https://quantpedia.com/revisiting-trend-following-and-mean-reversion-strategies-in-bitcoin/
- Bitcoin intraday time-series momentum: https://reading-9.eprints-hosting.org/100181/3/21Sep2021Bitcoin%20Intraday%20Time-Series%20Momentum.R2.pdf
- Gort et al., DRL crypto backtest overfitting: https://arxiv.org/abs/2209.05559v1
- Glassnode on perpetual flush-outs: https://insights.glassnode.com/the-week-onchain-week-30-2024
- Order-book imbalance in crypto: https://towardsdatascience.com/price-impact-of-order-book-imbalance-in-cryptocurrency-markets-bf39695246f6
