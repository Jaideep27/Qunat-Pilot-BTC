# Trading agent, step A: market context, trade plans and futures simulator

Date: 2026-10-20. Owner request: bridge indicators, structure (support/resistance), forecasts, positioning and news into well-formed trade plans with dynamic management, toward an experimental reinforcement-learning paper account. This design covers step A only:
- **A1:** defect fixes (done);
- **A2:** the market context;
- **A3:** deterministic trade plans;
- **A4:** a versioned futures simulator.

Step B (pre-registered evaluation of the trade plans), step C (RL for trade management) and step D (experimental forward account, worker, dashboard) come later, after this step is merged.

Nothing here places real orders. Every rule below is a registered **starting rule**, not a claim of edge. Its evaluation is step B, under a new pre-registration amendment.

## A2. MarketContext (`src/quantpilot/context.py`)

**Built once, causally:** `context_frame(frames)` computes, from the PostgreSQL families (`event_study.load_market` frames), one row per completed 5-minute bar. Rows are indexed by the bar's close (the instant it becomes known), and every column uses only data available by then.

**Contents:**

| Group | Columns |
|---|---|
| Price and flow | close, mark, index, premium, 5m volume, relative volume (vs. trailing 24h mean), 1h taker buy share |
| Positioning | open interest and its 1h/24h change, global/top-account/top-position long/short ratios, latest funding rate |
| Indicators per timeframe (5m, 15m, 1h, 4h; higher timeframes from completed bars only) | RSI(14), ADX, ATR(14), EMA21 and EMA50 distance in ATR, MACD histogram in ATR, trend sign (close vs EMA50) |
| Structure per timeframe (15m, 1h, 4h) | nearest confirmed swing support below and resistance above; their distance in ATR and %. A swing is a centered 5-bar extreme, confirmed 2 bars after it forms; only the last 12 confirmed swings in each direction are considered |
| Prior periods | prior UTC day and prior ISO week high/low |
| Availability | age in seconds of price, mark, funding and positioning; `blocking` when price, mark or funding is missing or stale |

`snapshot(frame, cutoff, extras)` returns an immutable `MarketContext` (frozen pydantic model, `schema_version=1`) for the last row at or before the cutoff. It adds:
- **Forecasts:** the latest shadow forecast per horizon, with validation status and model version, when one was recorded at or before the cutoff.
- **Macro:** the next CPI, jobs or FOMC release, and `blackout = true` from 30 minutes before to 15 minutes after a known release.
- **News:** the number of recent items, the latest titles and collection health. These are context only; they never produce a direction.
- **Account and position state:** supplied by the simulator.

Liquidation and depth data are not used before their registered eligibility date (2027-04-20).

## A3. TradePlan (`src/quantpilot/tradeplan.py`)

`plan(context, side, account, rules=PLAN_RULES)` returns a `TradePlan` with:
- direction, entry reference (the last close), stop, target, quantity, notional, margin, leverage and estimated liquidation price;
- risk amount and net reward:risk;
- expiry (the next bar);
- supporting and opposing evidence;
- `rejections` (empty means valid).

Structure and ATR come from the **1-hour** timeframe, matching the 24-hour maximum hold. The 4-hour levels and prior day/week levels also count as structure.

**Registered starting rules (`PLAN_RULES`, version 1):**
- **Stop:**
  - Long: the nearest structural support below the entry, minus 0.25 ATR.
  - Short: the nearest resistance above, plus 0.25 ATR.
  - It must be at least 1.5 ATR from entry; a closer structural stop is pushed out to 1.5 ATR.
  - It must be at most 4 ATR away; otherwise the plan is rejected (`STOP_TOO_WIDE`).
  - With no structure on the protective side, the stop sits at 1.5 ATR.
- **Target:**
  - The nearest opposing structural level (resistance for longs, support for shorts) beyond the entry. With none, the 24-hour 80% range boundary.
  - A target beyond the 24-hour 80% range boundary is rejected (`TARGET_OUTSIDE_RANGE`). The range comes from the shadow forecast when available, otherwise from the causal volatility-scaled range.
  - The range is never presented as a target-hit probability.
- **Costs:** 5 bps fee plus 3 bps slippage per side, plus funding for 3 settlements at the current rate when the position would pay it.
- **Reward:risk:** `(target distance − costs) / (stop distance + costs)` must be ≥ 1.5 (`REWARD_RISK_BELOW_MIN`).
- **Ordering:** long requires stop < entry < target; short requires target < entry < stop (`INVALID_LEVELS`).
- **Size:**
  - quantity = (0.25% of equity) / (stop distance + entry × costs), floored to Binance's 0.001 BTC step;
  - notional ≤ 100% of equity; margin = notional / 2 (2× isolated) ≤ available cash;
  - minimum 0.001 BTC and 100 USDT notional (`BELOW_EXCHANGE_MINIMUM`);
  - the stop must be inside the estimated liquidation price (`STOP_BEYOND_LIQUIDATION`).
- **Blocks:** no entries during a macro blackout (`MACRO_BLACKOUT`), when inputs are blocking (`MISSING_CRITICAL_INPUT`), or while the account is halted or paused.
- **Revalidation at fill:** `revalidate(plan, fill_price)` re-applies ordering and reward:risk at the actual fill (`INVALIDATED_AT_FILL`).

**Evidence** lists what supports and opposes the side:
- trend on 1h/4h, RSI extremes, crowding (funding and long/short ratios), taker flow;
- the forecast against its base rate, distance to the levels, the macro calendar and news activity.

It is descriptive. Nothing in A3 decides whether to trade; that is the policy's job (step B baseline, step C RL).

## A4. Futures simulator (`src/quantpilot/futures.py`, reducer version `futures-v1`)

This is a pure reducer, `step(state, event)`, separate from the frozen `engine.py`, which is unchanged. The same reducer will run historical simulation and the forward experimental account.

**Events:**
- `bar`: 5m OHLC, mark close, time = bar close.
- `funding`: rate, mark, settlement time.
- `action`: decided at a bar close. One of `enter(plan)`, `exit`, `tighten_stop(price)`, `reduce_target(price)`, `hold`, `wait`.
- `control`: pause or resume.

**Order of processing on each bar:**
1. Pending orders from the previous decision fill at this bar's **open**, with slippage against the trader. An entry is revalidated first; a rejected entry is recorded, not filled. No decision ever fills at a price already observed.
2. **Existing protection is evaluated before newly requested changes take effect:**
   - liquidation at the mark (isolated margin, 0.5% maintenance; the loss is capped at the margin);
   - then stop or target. A gap through the stop fills at the open; if one bar touches both stop and target, the **stop** is assumed hit.
3. Time exit at the close once the position has been open 24 hours.
4. The day's loss check: once equity falls 2% below the UTC day's start, the position is closed and entries are halted until the next UTC day. A 10% peak-to-trough drawdown halts the account until the owner reviews it.

**Accounting rules:**
- Stops may only tighten (move toward the price, never past it); targets may only move closer (never past the price).
- No averaging down, no adding to a position, and no reversal in the same step.
- Funding is charged or received at settlement while open.
- Fees are charged on each fill's notional.
- Every fill, rejection, funding payment and protection change is appended to the state's event log with the reducer version.

**State** is JSON-serialisable, so a forward account can persist and resume it. Missed periods are reconciled by replaying bars, never by inventing entries.

## Tests (each written first)

- **Context:**
  - causality: a row is identical whether built from the full history or from data truncated at its cutoff;
  - higher timeframes use completed bars only;
  - swing levels are confirmed only after 2 bars;
  - blackout window edges;
  - blocking on missing or stale inputs.
- **Trade plan:**
  - mirrored long/short;
  - stop buffer, 1.5 ATR minimum and 4 ATR maximum;
  - fallback to the range boundary, and the range limit;
  - net reward:risk with costs and funding;
  - sizing at $10,000 and $1,000, exchange minimums and rounding;
  - liquidation check, blackout, and revalidation at fill.
- **Simulator:**
  - fill at the next open;
  - gap and same-bar ambiguity;
  - liquidation at the mark;
  - funding sign;
  - tighten-only stops and closer-only targets;
  - time exit, daily loss halt, drawdown halt;
  - replay of the same events gives the same state (forward/backtest parity);
  - JSON round trip;
  - the frozen engine unchanged.
