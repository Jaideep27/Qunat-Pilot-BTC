# Selective strategy research (2026-10-20)

**Result: no strategy passed.** 186 configurations were tested across 10 rule families and 2 meta-labeled primaries, for intraday (hours) and swing (days–weeks) trading, exactly as pre-registered in [research/preregistration.md](research/preregistration.md). None met the pass rule on the development span, so, as pre-registered, **the holdout year was not used** and remains untouched for future tests.

## What was tested

- **Call scoring:** every call is scored as a real trade: entry at the bar close; take-profit and stop at ±1 expected move; a time limit; one position at a time.
- **Costs:** net of 16 bps round trip plus actual funding.
- **Accuracy:** "hit rate" is the share of calls that made money after costs, always shown with how often the rule calls.
- **Configuration choice:** chosen walk-forward over 6-month blocks, so each block's configuration is picked using only earlier data.
- **Pass rule:** needs every one of:
  - enough calls;
  - the hit rate's 95% lower bound above break-even;
  - a positive mean;
  - a deflated Sharpe ratio ≥ 0.95, given the number of trials;
  - PBO < 0.3;
  - most years positive.
- **Data:**
  - Swing: Binance spot daily from 2017.
  - Intraday: the Binance perpetual hourly panel from October 2021.
  - Both: Coinbase premium, Deribit DVOL, funding and open interest.

## Development results (walk-forward, out of sample)

Development span ends 2025-10-20. Trials registered: intraday 105, swing 81.

| Family | Track | Configs | Calls | Call rate | Hit rate (95% CI) | Break-even | Mean net/call | Profit factor | Deflated Sharpe | PBO | Years positive | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | swing | 9 | 119 | 4.65% | 54.6% (45.7%–63.3%) | 52.5% | 0.358% | 1.09 | 0.01 | 0.71 | 5/8 | fail: beats_break_even, deflated_sharpe, low_overfitting |
| S2 | swing | 9 | 247 | 9.66% | 55.5% (49.2%–61.5%) | 50.8% | 0.753% | 1.21 | 0.00 | 0.36 | 5/8 | fail: beats_break_even, deflated_sharpe, low_overfitting |
| S3 | swing | 18 | 25 | 1.71% | 44.0% (26.7%–62.9%) | 46.8% | -0.287% | 0.89 | 0.07 | 0.50 | 1/3 | fail: enough_calls, beats_break_even, positive_mean, deflated_sharpe, low_overfitting, most_years_positive |
| S4 | swing | 9 | 250 | 10.53% | 50.8% (44.6%–56.9%) | 54.0% | -0.200% | 0.88 | 0.00 | 0.77 | 2/7 | fail: beats_break_even, positive_mean, deflated_sharpe, low_overfitting, most_years_positive |
| S5 | swing | 18 | 88 | 3.44% | 53.4% (43.1%–63.5%) | 47.6% | 0.913% | 1.26 | 0.06 | 0.43 | 4/7 | fail: beats_break_even, deflated_sharpe, low_overfitting, most_years_positive |
| I1 | intraday | 27 | 38 | 0.17% | 47.4% (32.5%–62.7%) | 48.0% | -0.025% | 0.98 | 0.00 | 0.40 | 2/3 | fail: enough_calls, beats_break_even, positive_mean, deflated_sharpe, low_overfitting |
| I2 | intraday | 18 | 52 | 0.24% | 46.2% (33.3%–59.5%) | 49.9% | -0.086% | 0.86 | 0.00 | 0.80 | 1/3 | fail: enough_calls, beats_break_even, positive_mean, deflated_sharpe, low_overfitting, most_years_positive |
| I3 | intraday | 6 | 411 | 1.56% | 48.2% (43.4%–53.0%) | 48.8% | -0.015% | 0.98 | 0.00 | 0.67 | 3/4 | fail: beats_break_even, positive_mean, deflated_sharpe, low_overfitting |
| I4 | intraday | 18 | 105 | 0.40% | 28.6% (20.8%–37.8%) | 53.3% | -0.237% | 0.35 | 0.00 | 0.33 | 0/4 | fail: beats_break_even, positive_mean, deflated_sharpe, low_overfitting, most_years_positive |
| I5 | intraday | 18 | 92 | 0.35% | 57.6% (47.4%–67.2%) | 47.1% | 0.384% | 1.52 | 0.00 | 0.33 | 2/4 | fail: enough_calls, deflated_sharpe, low_overfitting, most_years_positive |
| M1 | swing | 18 | 295 | 11.54% | 58.3% (52.6%–63.8%) | 51.6% | 0.792% | 1.31 | 0.01 | 0.42 | 5/5 | fail: deflated_sharpe, low_overfitting |
| M2 | intraday | 18 | 402 | 1.53% | 43.3% (38.5%–48.2%) | 51.9% | -0.214% | 0.71 | 0.00 | 0.80 | 0/3 | fail: beats_break_even, positive_mean, deflated_sharpe, low_overfitting, most_years_positive |

## Robustness: rolling 2-year selection (reported, not used for the verdict)

| Family | Calls | Hit rate | Mean net/call | PBO |
|---|---|---|---|---|
| S1 | 118 | 58.5% | 0.878% | 0.57 |
| S2 | 233 | 55.8% | 0.788% | 0.43 |
| S3 | 26 | 50.0% | -0.705% | 0.38 |
| S4 | 173 | 52.0% | -0.098% | 0.54 |
| S5 | 133 | 55.6% | 0.916% | 0.57 |
| I1 | 34 | 50.0% | -0.245% | 0.60 |
| I2 | 52 | 46.2% | -0.086% | 0.80 |
| I3 | 411 | 48.2% | -0.015% | 0.67 |
| I4 | 177 | 33.3% | -0.247% | 0.50 |
| I5 | 92 | 57.6% | 0.384% | 0.33 |

Passing on development: none.

## Checks on the near-misses

**M1 (trend side, meta-labeled, 7-day hold)** came closest:
- 295 calls (11.5% of days);
- 58.3% hit rate (95% CI 52.6–63.8%) against a 51.6% break-even;
- +0.79% net per call;
- positive in 5 of 5 years.

It failed the deflated Sharpe (0.01) and overfitting (PBO 0.42) criteria. These checks confirm that verdict:

| Check | Result |
|---|---|
| Drift baseline: always long, same scoring and span | 7-day hold: 52.9% hit rate, +0.25% per call. 14-day hold: 55.2%, +0.91%. BTC's rise alone makes long-biased swing calls profitable. |
| M1 by side | Longs (59% of calls) +1.15% per call; shorts +0.28%. Most of the result is the market's drift. |
| Deflated Sharpe: how much the cross-trial variance choice matters (amendment A3) | 0.01 as registered; 0.10 with variance from trials with ≥ 30 calls only; about 0.97 with no deflation at all. M1 still fails, because its PBO is 0.42: the configuration picked on earlier data landed in the bottom half out of sample in 42% of blocks. |

The swing families with positive means behave the same way: S1 breakout, S2 momentum agreement and S5 Coinbase premium all make money on longs and little or nothing on shorts. Their Sharpe ratios are well inside what 81 swing trials would produce by luck.

**Intraday:** nothing came close.
- Session momentum (I3, 411 calls), flush-out rebounds (I1), crowding fades (I2) and the meta-filtered 24h momentum (M2, 402 calls) all lost money after costs.
- Taker-flow persistence (I4) lost on 71% of calls. This is not a reversal: gross returns were near zero (80 of 105 calls hit the time limit with small moves), so costs turned most calls into losers.
- The hourly Coinbase premium (I5) had a 57.6% hit rate but only 92 calls (fewer than the 100 required) and a PBO of 0.33.

An independent review of the code and run found no bug that could change any verdict. It noted that the deflated-Sharpe variance was not specified in advance; amendment A3 now records the choice and fixes it for future tests.

## What this means

- With this data, **no rule predicts BTC direction well enough to beat costs once the number of tries is accounted for**. That holds even when it is allowed to stay silent 90–99% of the time.
- The 55–58% hit rates seen on swing longs mostly reflect BTC's 2017–2025 uptrend, not timing skill.
- This agrees with the earlier multi-horizon research and the frozen release model.
- No backtest here came near 75–80% (the leakage canary did: 92%, which is why that level is treated as a red flag).

## What is still running

- **Forward-only data:** since 2026-10-20 the supervisor records liquidations (per minute, from Binance's forceOrder stream) and a 1000-level order-book snapshot every minute. These cannot be downloaded later.
- **Pre-registered tests for that data:** liquidation exhaustion (I6) and depth imbalance (I7) are registered in amendment A2. They will be evaluated once at least 6 months exist (no earlier than 2027-04-20).
- **Untouched holdout:** 2025-10-20 → 2026-10-19 is still unused.

## Reproduce

```powershell
uv run quantpilot research-data                      # fetch history and build the panels
uv run quantpilot research-strategies --output artifacts/strategy-research/<new dir>
# only if a family passes on development, and only once:
uv run quantpilot research-strategies --output artifacts/strategy-research/<same dir> --holdout
```

The trial registry (`trials.jsonl`), per-family walk-forward picks and calls (`families/`) and `report.json` are in `artifacts/strategy-research/run-20261020/`.
