# Four account styles: history test (A10, 2026-11-01)

**Result: no style passed the pre-registered rule.**

| Style | Result | Verdict |
|---|---|---|
| Trend Rider | +28.6% over 8 years, worst drop −4.1% | Positive but not proven |
| Funding Collector | +13.9% over 5.8 years | Positive but not proven |
| Range Trader | −50% | Lost half the account |
| Breakout Hunter | −50% | Lost half the account |

The holdout year (2025-10-20 → 2026-10-19) was not used.

The four styles replaced the A8 accounts on the owner's request (amendment A10). By the owner's decision, they run live on paper from the start, and this test ran in parallel. Every rule was fixed before the test ran (`docs/research/preregistration.md`, A10). Each style is one configuration, so the test adds 4 trials to the registry.

## Results (development span)

| Style | Span | Trades | Win rate | Account | Per year | Worst drop | Holding BTC | Pass rule |
|---|---|---|---|---|---|---|---|---|
| T1 Trend Rider | 2017-08 → 2025-10 | 171 | 40% | **+28.6%** | about +3.1% | −4.1% | +2,398% | fail: deflated Sharpe 0.28; coin-flip test |
| C1 Funding Collector | 2020-01 → 2025-10 | 133 cycles | n/a | **+13.9%** | +2.3% | −10.8% | +1,401% | fail: deflated Sharpe 0.03 |
| R1 Range Trader | 2021-10 → 2025-10 | 453 | 50% (break-even 64%) | **−50%** (floor hit in 2023) | — | −50.5% | +69% | fail on every item |
| B1 Breakout Hunter | 2021-10 → 2025-10 | 209 | 29% (break-even 43%) | **−50%** (floor hit in 2022) | — | −50.0% | +69% | fail on every item |

Every figure is net of fees, slippage and funding, at 1% of equity at risk per trade with a 2× leverage cap; the collector holds a 60%/40% hedge.

## What it means

- **Trend Rider** made money in 6 of the 8 years with at least 5 trades.
  - Profit factor 2.3: it won 2.3× what it lost.
  - Its drawdowns were tiny.
  - The amount is small because the rule risks 1% per trade on a wide stop (3 × the 20-day range, often 10–30%), so it holds only about 3–15% of the account in BTC.
  - With 9 swing trials already in the registry, a Sharpe ratio this size could still be luck (deflated Sharpe 0.28 < 0.95).
  - Berdondini's coin-flip test fails by construction for a 40%-win, large-payoff style: it counts wins, not their size. This caveat does not change the pre-registered verdict.
- **Funding Collector** earned $5,216 in funding but paid most of it back in fees.
  - Cause: the 3-day exit rule switched the hedge on and off 133 times, at about 0.25% per round trip.
  - It made money in 2020–21 and 2023–24, and lost a little in 2022 and 2025, when funding was often negative.
- **Range Trader and Breakout Hunter** lose as surely as the old playbooks did.
  - Range Trader's target is about as far away as its stop, so it needs to win 64% of trades; it won 50%.
  - Breakout Hunter's stops are tight (often the 0.3% minimum), so costs of about 0.3 R per trade swamp any edge.
- **Overlap.** Replayed over the 30 days to 2026-11-01, the four styles never signalled in the same hour on the same side. The A8 accounts had entered the same trade in the same minute.

Changing any rule after seeing these numbers would be fitting to the past (Berdondini's composite-hypothesis warning). Any change goes to the owner, as accounts-v3 with a new amendment.

## Reproduce

```powershell
uv run quantpilot research-styles --output artifacts/style-research/<new dir>
```

Per-style trades and verdicts are in `artifacts/style-research/run-20261101/runs/`, and the summary in `report.json`. The run takes about 5 minutes and reads the funding archive, the daily and hourly research panels, and the PostgreSQL 5m candles.

## The Combined Bot (A11)

The bot listens to every member and holds one trade at a time. Same span and costs as the styles above. Holdout untouched.

| Version | Price trades | Win rate (break-even) | Account | Worst drop | Idle-hedge cycles | Pass rule |
|---|---|---|---|---|---|---|
| With idle money parked (live version) | 314 | 46% (56%) | **−50%** (floor hit in 2023) | −51% | 101, +$109 funding | fail on every item |
| Without parking | 405 | 45% (57%) | **−50%** (floor hit in 2023) | −52% | — | fail on every item |

**Who led the 395 trades of the live version:**

| Leader | Trades |
|---|---|
| Range Trader | 220 |
| Breakout Hunter | 141 |
| Trend Rider | 19 |
| Rulebook setups | 15 |

**Filtered out:**
- 470 leaders had no second opinion;
- 255 trades were vetoed by the daily trend;
- 153 signals came from members that were not allowed to lead.

**What it means:**
- The rules did what they were written to do: one trade at a time, no counter-trend trades, two sources behind every entry.
- But most entries were still led by the two losing members, and the extra filters did not turn their losses into gains.
- The idle hedge earned little: it parked only after a full flat day, and most of its funding went to switching costs.
- A bot led only by the members that made money in history (Trend Rider, plus the hedge) is an obvious next idea. It would be chosen after seeing these results, so it would need a new amendment and a forward test, and only if the owner wants it.
