# Pre-registration: selective BTC prediction research

Registered 2026-10-20, before any of the strategies below was evaluated. Changes after this date are allowed only as new dated amendments at the end of this file. Earlier entries are never edited.

## 1. Question

Is there a rule that, **when it chooses to make a call**, predicts BTC's direction well enough to make money after fees and funding, out of sample? The rule may abstain most of the time.

## 2. Data and spans

| Track | Bars | Data | Development span | Holdout (evaluated once) |
|---|---|---|---|---|
| Intraday | 1 hour, indexed by bar close | Binance USD-M BTCUSDT perpetual (5m candles, taker buy volume, funding, open interest, long/short ratios, mark/index) from the PostgreSQL store; Coinbase BTC-USD hourly; Binance spot BTCUSDT hourly | first available hour → 2025-10-19 23:00 UTC | 2025-10-20 → 2026-10-19 |
| Swing | 1 day (UTC), indexed by day close | Binance spot BTCUSDT daily (2017-08 →); Binance USD-M funding (2019-09 →); open interest from the metrics archives; Coinbase BTC-USD daily; Deribit DVOL | first available day → 2025-10-19 | 2025-10-20 → 2026-10-19 |

Every input at a cutoff uses only data available by that cutoff. The holdout span was seen by earlier, unrelated research (the outlook confirmation year), but never by these strategies.

## 3. How a call is scored (triple barrier, one position at a time)

- **Entry:** at the cutoff bar's close, on the signal's side (long or short).
- **Barriers:** take-profit and stop both at **1.0 × σ_H** from entry, where σ_H is the expected move over the holding period:
  - intraday: σ_H = std of the last 168 hourly log returns × √H;
  - swing: σ_H = std of the last 30 daily log returns × √H.
- **Time limit:** H bars.
- **Path rules:** the path is checked with each bar's high and low. If one bar touches both barriers, the **stop is assumed hit first** (conservative). Exits at a barrier fill at the barrier price.
- **One position at a time:** signals that fire while a position is open are ignored. Every scored call is therefore independent of the others.
- **Costs:**
  - 5 bps fee + 3 bps slippage per side (16 bps round trip), as in the engine's `RiskConfig`.
  - Funding is applied at each 8-hour settlement crossed while open: longs pay positive funding, shorts receive it.
  - The swing track uses the perpetual funding history where it exists; before 2019-09 it charges a flat 0.01% per settlement (the Binance default).
- **Hit:** the call's net return after costs is > 0. "Accuracy" always means this hit rate, reported together with the call rate (calls per bar).

## 4. Hypotheses (signal families)

A signal outputs +1 (long), −1 (short) or 0 (no call) at each bar close. Parameters are listed with their only allowed values. The holding period H is an extra grid parameter unless fixed.

### Swing track (H ∈ {3, 7, 14} days)

| ID | Name | Rule | Parameters |
|---|---|---|---|
| S1 | Donchian breakout | +1 if close > highest high of the previous L days; −1 if close < lowest low of the previous L days | L ∈ {20, 55, 100} |
| S2 | Momentum agreement | +1 if every listed lookback return is > 0; −1 if every one is < 0 | lookbacks ∈ {(7, 30), (7, 30, 90), (14, 60, 120)} |
| S3 | Funding crowding | f = mean funding over the last 3 days; q = its percentile within the trailing 365 days. −1 if q ≥ 1−p and 7-day return > 0; +1 if q ≤ p and 7-day return < 0. With the OI filter, a call also needs the 7-day OI change > 0. | p ∈ {0.05, 0.10, 0.20}; OI filter ∈ {off, on} |
| S4 | Volatility compression | v = 20-day realised vol; r = its percentile within the trailing 365 days. If r ≤ q: +1 if close > SMA50, else −1 | q ∈ {0.10, 0.20, 0.30} |
| S5 | Coinbase premium | prem = Coinbase close / Binance spot close − 1; z = (prem − 30-day mean) / 30-day std. +1 if z > z0 on each of the last k days; −1 if z < −z0 on each of them | z0 ∈ {1.0, 1.5, 2.0}; k ∈ {1, 3} |

### Intraday track (H ∈ {4, 12, 24} hours unless fixed)

| ID | Name | Rule | Parameters |
|---|---|---|---|
| I1 | Flush-out rebound | +1 if the 4h OI change ≤ −x and the 4h return ≤ −y | x ∈ {0.02, 0.03, 0.05}; y ∈ {0.02, 0.03, 0.04} |
| I2 | Crowding fade | g, f = percentiles of the global long/short ratio and of funding within the trailing 30 days. −1 if g ≥ q and f ≥ q and abs(24h return) < s; +1 if g ≤ 1−q and f ≤ 1−q and abs(24h return) < s | q ∈ {0.80, 0.90, 0.95}; s ∈ {0.01, 0.02} |
| I3 | Session momentum | At the bar closing at UTC hour h0, r = return since 00:00 UTC; +1 if r > m, −1 if r < −m; **H fixed** = 24 − h0 (exit at 00:00 UTC) | h0 ∈ {8, 14, 16}; m ∈ {0.005, 0.01} |
| I4 | Taker-flow persistence | share = taker buy volume / volume over the hour; z = (share − 168h mean) / 168h std. +1 if z ≥ z0 for each of the last k hours; −1 if z ≤ −z0 for each | z0 ∈ {1.5, 2.0, 2.5}; k ∈ {2, 3} |
| I5 | Coinbase premium (hourly) | as S5, with hourly bars and a 168-hour window | z0 ∈ {1.5, 2.0, 2.5}; k ∈ {1, 3} |

Deribit DVOL is not a direction signal. It is used only as a meta-labeling feature (section 6).

## 5. Walk-forward selection (rule families)

- Test blocks are consecutive 6-month blocks. The first block starts once 1 year of usable history exists for the family. Blocks end at the development cutoff.
- **Primary selection (pre-registered):** for each block, every configuration of the family (parameters × H) is evaluated on all development data **before** the block (expanding window), with a purge of H bars before the block starts. The configuration with the highest mean net return per call is chosen, and only among configurations with ≥ 10 calls; if none qualifies, the block has no calls. The chosen configuration is then run on the block.
- **Robustness, reported but not used for the pass decision:** the same with a rolling 2-year selection window.
- A family's out-of-sample record is the pooled set of calls from all test blocks.

## 6. Meta-labeling

- **Applied to:** every family that passes section 7 on development data, **plus** two pre-registered high-recall primaries:
  - M1 (swing): side = sign(close / SMA50 − 1) every day, H = 7 days;
  - M2 (intraday): side = sign(24h return) every hour, H = 12 hours.
- **Label:** hit (net return > 0) of the primary's call under section 3.
- **Features:** the existing hourly state columns (`event_study.STATE`) for intraday; daily equivalents (returns 1/7/30d, realised vol 20d, vol percentile, distance to 20/55-day highs and lows, funding 3d mean and percentile, OI 7d change, Coinbase premium z, DVOL − realised vol) for swing.
- **Models:** L2 logistic regression (C = 0.1, standardised inputs) and XGBoost (depth 3, 200 trees, learning rate 0.05, min_child_weight 50, subsample 0.8, colsample 0.8).
- **Training scheme:** expanding; rolling 2 years; expanding with recency weights (half-life 180 days).
- **Target call rate:** 5%, 10% or 25% of the primary's calls. The probability threshold is set on the last 6 months before the test block, which act as validation.
- **Grid per primary:** 2 models × 3 training schemes × 3 call rates = 18 configurations, selected walk-forward exactly as in section 5.

## 7. Pass rule (all must hold on development out-of-sample calls)

1. **Enough calls:** at least 100 calls (intraday) or 50 calls (swing).
2. **Hit rate beats break-even:** the Wilson 95% lower bound of the hit rate is above the break-even hit rate. Break-even = mean loss / (mean win + mean loss), from the same calls' net returns.
3. **Mean net return per call > 0, and the deflated Sharpe ratio probability is ≥ 0.95.** The trial count N used is the total number of configurations of that track in the trial registry at the time.
4. **Low overfitting:** PBO < 0.3 across the family's walk-forward blocks.
5. **Not a one-regime result:** mean net return > 0 in at least 60% of the calendar years that contain ≥ 5 calls.

**Holdout (one run only, after all choices are frozen).** A family that passed development passes the holdout if:
- it has ≥ 10 calls;
- mean net return per call > 0;
- the hit rate is ≥ the development break-even hit rate.

## 8. Accounting
- Every configuration evaluated, including failures, is appended to `artifacts/strategy-research/trials.jsonl`.
- `docs/strategy-research.md` reports every family, including failures.
- A backtest hit rate ≥ 75% is investigated for leakage before it is reported as a result.

## 9. What a pass does and does not mean
A pass means the rule is recorded in the shadow forecaster and scored forward. It does **not** mean trading. Paper trading would need ≥ 3 months and ≥ 20 forward calls consistent with the backtest, and a separate decision by the owner.

## Amendments
(none yet)
