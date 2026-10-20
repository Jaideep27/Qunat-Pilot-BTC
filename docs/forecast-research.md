# Forecast research: what is predictable, what is not, and what is deployed

Research run on 2026-10-20 over 221,213 five-minute BTCUSDT perpetual cutoffs (24 months plus warm-up). The last two months were reserved and not used. These months were inspected in earlier work, so the historical tests are **previously observed data, not an untouched holdout**. The only untouched test is the forward record now accumulating (see the end of this report).

## How overfitting was controlled

| Control | What it does |
|---|---|
| Purged walk-forward splits | Train, calibrate, validate and test windows in time order, separated by a 24-hour purge so overlapping labels cannot leak across windows |
| Identical timestamps | Every configuration is compared on the same eligible cutoffs |
| Fit-inside-window | Scaling, calibration, regime weights and quantile models are fitted on training/calibration windows only |
| Regularized models | Logistic regression with strong L2; XGBoost with depth 3, `min_child_weight` 50, row and column subsampling |
| Thinned training | Training uses every third cutoff because adjacent cutoffs share almost the same future label |
| Validation-only selection | The champion for each component and horizon is the configuration with the best mean **validation** skill; test results are only reported |
| No-skill fallback | If nothing beats the simple baseline on validation, the champion *is* the baseline |
| PBO | Probability of backtest overfitting: the share of folds whose validation pick ranks at or below the test median |
| Promotion gate | A champion is deployed only if its test results confirm it (positive mean skill, at least half the folds better). The gate can only reject |
| Trial count | Ten configurations per horizon (five feature groups × expanding or rolling-90-day training) are reported with every result |

Skill always means *baseline loss minus model loss*. The baselines are: the training up-rate for direction (Brier score), unconditional training quantiles for ranges (pinball loss), the training drop rate for large-drop risk (Brier score), and EWMA for volatility (QLIKE).

## Results

### Direction: not predictable at these horizons

| Horizon | Champion | PBO | Notes |
|---|---|---|---|
| 1h | base rate | 0.25 | 30% of configuration-folds beat the base rate on validation; mean test skill −0.0016 |
| 4h | base rate | 0.25 | 17% beat it on test; mean test skill −0.0067 |
| 24h | base rate | 0.75 | One configuration showed +0.053 validation skill in a single fold, yet only 30% beat the base rate on test: a textbook overfit |

No feature group (price, technical, derivatives, full, or full plus macro) and no training scheme beat a constant probability on validation. Forecasting **which way** BTC moves over the next hour, four hours or day does not work with this information. That agrees with the frozen release model (ROC-AUC 0.539).

### Return ranges: predictable at short horizons, and macro events help

| Horizon | Champion | Test gain vs unconditional | Test folds better | PBO | Robustness | Deployed |
|---|---|---|---|---|---|---|
| 1h | full + **macro calendar**, expanding | 3.8% | 4/4 | 0.00 | 39/40 configuration-folds beat the baseline on validation and test | **Yes** |
| 4h | full, expanding | 1.4% | 2/4 | 0.25 | 26/40 beat it on test | Yes (passes the gate at exactly half) |
| 24h | full + macro, expanding | −0.5% | 1/4 | 0.25 | 8/40 beat it on test | No: unconditional range |

The 1h range model's 80% intervals covered 73–86% of outcomes across configurations, close to the intended 80%. Adding the official CPI, jobs and FOMC calendar (lead time to the next release, release windows, first-release values) made the best 1h range model. In other words, scheduled macro events help predict **how much** BTC moves, not **which way**.

### Large-drop risk (touching −5% within 24 hours): no validated skill

Only 3 of 30 evaluable configuration-folds beat the base rate on validation, so the champion is the base rate (PBO 0.33). Many two-week test windows contained no 5% drop at all, so this evidence is thin. The event study (below) still describes which conditions preceded drops; it does not yet translate into a forecasting edge.

### Volatility

| Horizon | Validation pick | Test windows better | Deployed |
|---|---|---|---|
| 1h | EWMA | 3/4 | EWMA |
| 4h | GARCH(1,1) | 1/4 | **EWMA** (GARCH rejected by the gate) |
| 24h | GARCH(1,1) | 3/4 | GARCH(1,1) |

## The 5% move event study (five years, hourly)

From [event-study.md](event-study.md): 143 drop and 175 rally episodes from October 2021 to October 2026, with 954 matched ordinary hours.

- After a 5% drop, the median next-24h return was +0.44% versus −0.32% for matched ordinary hours; 43% of drops kept falling. The 95% interval for the mean difference only just excludes zero.
- Conditions associated with a 5% drop following within 24 hours: high recent volatility (15.1% in the top fifth vs 3.2% in the bottom fifth), crowded long positioning (global long/short ratio, 12.7% vs 3.6%) and high funding (14.5% in the top fifth). The base rate is 7.4%.
- Only 19 of 143 drops followed within 24 hours of a CPI, jobs or FOMC release, about what chance alone would produce.

These are descriptive associations across all hours. They did not become a validated drop forecast (above). That is consistent: the conditions raise drop frequency, but not enough to beat a base rate after calibration out of sample.

## News

News is collected from the Fed, BLS and CoinDesk and analysed by Claude Sonnet 5.5 (`claude -p`) and Codex. Every claim is checked against verbatim source quotes. Collection started on 2026-10-19, so only about one day of forward-collected news exists (25 items, 8 completed analyses). **News cannot be evaluated yet, and no news feature is used by any deployed model.** Historical news was deliberately not backfilled, because a model analysing old headlines may already know what happened next.

Each shadow forecast stores the news features at its cutoff. Once a few months of forward news have accumulated, a news-augmented model can be tested against the deployed champions on data neither has seen.

## What is deployed: the dynamic shadow forecaster

Every 15 minutes the supervisor runs `quantpilot shadow-forecast`. It refits the gated champions once a day on the latest two years of PostgreSQL market history, so it adapts to new data, and within a day it re-weights its models by market regime. It records:

- **Upward probability** for 1h, 4h and 24h. Currently the base rate everywhere, labeled "No validated skill".
- **80% return range**: model-based at 1h and 4h, unconditional at 24h.
- **Chance of touching −5% within 24h**: the recent base rate.
- **Volatility**: EWMA at 1h and 4h, GARCH at 24h.
- **Market condition** and the **news features** at the cutoff.

Each forecast is scored when its horizon passes (Brier score vs base rate, 80% range coverage), and the forward record appears on the Prediction tab, `GET /api/v1/forecasts` and the `get_shadow_forecasts` MCP tool. The forecaster never trades, sizes positions or changes the released paper-trading policy.

## Bottom line

The best model this data supports is a **risk model, not a direction model**. It forecasts how wide the next one to four hours are likely to be (with macro events helping), uses GARCH volatility for the next day, and reports a coin-flip probability for direction, because nothing beat that out of sample. Claims of reliable BTC direction prediction from indicators, volume, positioning or sentiment should be treated with suspicion. A real edge will show up first in the forward record, not in a backtest.

Reproduce: `uv run quantpilot research-events --output <dir> --folds 4`, `uv run quantpilot research-risk --output <dir> --folds 4`, then `uv run quantpilot shadow-forecast`.
