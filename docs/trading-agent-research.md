# Trading agent, step B: do the trade plans make money? (2026-10-21)

**Result: no.**
- Every pre-registered entry rule lost money after costs.
- The pooled walk-forward record failed every criterion of the pass rule except having enough trades.
- The coin-flip control performed about as well as the real rules (slightly better per trade), so the structure-based plans add no edge over a random side.
- As pre-registered (amendment A4), the holdout year (2025-10-20 → 2026-10-19) was **not** used and stays untouched.

## What was tested

- **Plans:** the step A trade plans (rules v1):
  - stop behind the nearest support/resistance (1.5–4 ATR);
  - target at the nearest opposing level within the 24h range;
  - reward:risk ≥ 1.5 or ≥ 2.0 after costs and funding;
  - 0.25% risk per trade, 2× isolated, macro blackouts.
- **Decisions and exits:**
  - decisions at each completed 1h close while flat, over 35,064 hourly decisions from 2021-10-20 to 2025-10-19;
  - fills at the next 5m open in the `futures-v1` simulator, $10,000 account;
  - exits only by stop, target or the 24h limit.
- **Entry rules:**

  | Rule | Enters when |
  |---|---|
  | B1, trend | 1h and 4h trends agree |
  | B2, best plan | either side has a valid plan; takes the higher reward:risk |
  | B3, pullback | an RSI dip within the 4h trend |
  | B0, coin-flip control | a seeded coin flip picks the side |

- **Pass rule:** walk-forward selection over 6-month blocks and the section 7 rule, counting all 113 intraday trials.

## Results by configuration (whole development span)

| Configuration | Trades | Hit rate | 95% lower bound | Break-even | Net per trade | Profit factor | Account | Max drawdown (closed) | Exits (stop / target / 24h) |
|---|---|---|---|---|---|---|---|---|---|
| B1 trend, R:R 1.5 | 66 | 42.4% | 31.2% | 44.6% | −0.07% | 0.92 | +0.38% | −1.4% | 31 / 17 / 18 |
| B1 trend, R:R 2.0 | 15 | 26.7% | 10.9% | 30.2% | −0.19% | 0.84 | −0.70% | −1.7% | 11 / 3 / 1 |
| B2 best plan, R:R 1.5 | 380 | 33.9% | 29.4% | 38.9% | −0.18% | 0.81 | **−12.1%** | −15.8% | 237 / 89 / 53 (+1 drawdown halt) |
| B2 best plan, R:R 2.0 | 80 | 22.5% | 14.7% | 30.9% | −0.38% | 0.65 | −5.7% | −6.3% | 60 / 14 / 6 |
| B3 pullback, R:R 1.5 | 11 | 27.3% | 9.7% | 40.7% | −0.45% | 0.55 | −0.90% | −1.5% | 8 / 2 / 1 |
| B3 pullback, R:R 2.0 | 1 | 0% | 0% | — | −1.31% | 0 | −0.25% | −0.3% | 1 / 0 / 0 |
| B0 coin flip, R:R 1.5 | 266 | 35.7% | 30.2% | 38.3% | −0.10% | 0.89 | −5.6% | −9.2% | 160 / 68 / 38 |
| B0 coin flip, R:R 2.0 | 51 | 23.5% | 14.0% | 26.8% | −0.17% | 0.84 | −2.9% | −4.0% | 38 / 10 / 3 |

## Walk-forward (out of sample) and the pass rule

| Family | Trades | Hit rate (95% CI) | Break-even | Net per trade | PBO | Years (net per trade) | Verdict |
|---|---|---|---|---|---|---|---|
| TP (B1–B3) | 192 | 30.2% (24.2–37.0%) | 36.9% | −0.23% | 1.00 | 2023 −0.20%, 2024 −0.18%, 2025 −0.41% | **fail** on every criterion except enough trades |
| Control (B0) | 59 | 30.5% (20.3–43.1%) | 35.6% | −0.15% | 0.40 | 2023 −0.83%, 2024 +0.01%, 2025 −0.19% | fail |

The configuration picked on earlier data (mostly B2 at 1.5) was the worst out of sample in every block (PBO 1.0).

## Why, and what it means for step C (trade management)

- **Stops did most of the work.** About 60% of trades hit their stop, typically 1.3% away.
  - 37–39% of stopped trades were first ahead by half the risked amount.
  - Only 13–14% reached a full 1R ahead before reversing.
  - The typical trade's best point was 0.7–0.8R.
- **The structure doesn't predict direction.** Nearest support and resistance did not help choose a side: the coin flip did as well.
- **Consequence for step C.** When price changes have no predictable direction, which is what every test in this project finds, no exit rule can turn zero-edge entries into positive expected profit (the optional-stopping argument). Better management could reduce losses and drawdowns, but it cannot create the edge the entries lack.

## Recommendation

- Do **not** expect step C (RL for trade management) to make this profitable. Run it only if the goal is lower drawdowns or a learning and recording system.
- The experimental forward paper account (step D) can still be built as an auditable record that learns from outcomes. It must be presented as experimental, with no profitability claim.
- The pre-registered liquidation and order-book tests (I6/I7, from 2027-04-20) remain the best remaining chance of finding a real short-term edge.

## Reproduce

`uv run quantpilot research-agent --output artifacts/agent-research/<new dir>` (about 16 minutes per configuration on this laptop). Trades per configuration, the trial registry and `report.json` are in `artifacts/agent-research/run-20261020/`.
