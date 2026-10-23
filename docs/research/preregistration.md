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

**A1 — 2026-10-20, before any strategy was evaluated (data facts found while building the panels).**
- Binance's USD-M funding archives start in **January 2020**, not September 2019. The flat default funding (0.01% per settlement) is therefore charged before 2020-01-01, and S3 (funding crowding) can only call from 2020 onward.
- Open interest is available from **2021-10** (the PostgreSQL store), so S3 with the OI filter only calls from 2021-10 onward. Archive rows with open interest ≤ 0 (28 hours, 2024-07 to 2025-04) are treated as missing.
- I4 uses the taker buy share from the 5-minute candles (already defined that way above). The Binance metrics taker long/short volume ratio is not downloaded: it carries the same taker flow at coarser resolution.
- The daily Coinbase premium is computed against Binance spot BTCUSDT, so it includes USDT/USD moves (up to 13% in 2017–2018). The z-score over the trailing window is used exactly as registered.

**A2 — 2026-10-20, after the development run (no family passed) and before any liquidation or order-book data existed.**
The holdout year 2025-10-20 → 2026-10-19 was **not** evaluated, because section 7 requires a development pass first. It stays untouched for future pre-registered tests.

Two forward-only intraday families are registered now, before their data exists (collection started 2026-10-20):

| ID | Name | Rule | Parameters |
|---|---|---|---|
| I6 | Liquidation exhaustion | L = long-liquidation USD in the last hour; p = its percentile within the trailing 30 days. +1 if p ≥ c and the 1h return ≤ −m. Mirror with short liquidations: −1 if their percentile ≥ c and the 1h return ≥ +m | c ∈ {0.99, 0.995}; m ∈ {0.01, 0.02} |
| I7 | Depth imbalance persistence | b = hourly mean of `imbalance_15bps`; z = (b − 168h mean) / 168h std. +1 if z ≥ z0 for each of the last k hours; −1 if z ≤ −z0 for each | z0 ∈ {1.5, 2.0, 2.5}; k ∈ {1, 3} |

- **Holding periods and scoring:** H ∈ {4, 12, 24} hours, scored exactly as in section 3.
- **When:** evaluated once at least 6 months of data exist (no earlier than 2027-04-20).
- **Walk-forward:** 1-month test blocks after a 2-month burn-in (otherwise as in section 5). Pass rule as in section 7. The trial count includes every earlier intraday trial (105 at registration).
- **Holdout:** the last 2 months of the collected data are held back and evaluated once, only for a family that passes.

**A3 — 2026-10-20, after an independent review of the development run (verdict unchanged).** This records choices the original text left open or did not describe exactly.
- **Deflated Sharpe variance.** Section 7 did not say how V[SR], the cross-trial variance of Sharpe ratios, is estimated.
  - **Used in the 2026-10-20 run:** the variance of the per-call Sharpe of every registered configuration in the track, mixing all-call rule Sharpes and walk-forward meta Sharpes, at any call count.
  - **How much it matters:** this choice decides the deflated-Sharpe criterion. M1's value is 0.01 with it, 0.10 using only trials with ≥ 30 calls, and about 0.97 with no deflation at all.
  - **Effect on the verdict:** none. Every family also fails at least one other criterion; M1 fails PBO at 0.42.
  - **For every later test (including I6/I7):** V[SR] is the variance of per-call Sharpe across that track's trials with at least 30 calls.
- **Intraday meta-labeling features.** These were `event_study.STATE` **plus** the 168-hour taker-share z-score, the Coinbase premium and the DVOL-minus-realised-volatility gap. Section 6 listed only STATE and DVOL. M2 lost money in every year regardless.
- **Development cutoff.** The development span was implemented as bars closing before 2025-10-20 00:00 UTC, so the daily bar of 2025-10-19 (which closes at 2025-10-20 00:00) was excluded. That bar is not used in the holdout either.
- **Forward depth.** Each snapshot also records how far it reaches on each side (`bid_reach_bps`, `ask_reach_bps`), so an `imbalance_15bps` computed from a book thinner than 15 bps can be detected and excluded in I7.

**A4 — 2026-10-20, trading agent step B: deterministic trade plans with simple entry rules. Registered before any of them was evaluated.**

- **Question.** Do the step A trade plans make money after costs when entered by simple, pre-defined rules? The plans set the stop, target and size; nothing else adjusts them.
- **Data and spans.**
  - Binance USD-M BTCUSDT 5m candles, mark price and funding settlements, plus the macro calendar, from the PostgreSQL store.
  - Development: 2021-10 → bars closing before 2025-10-20. The holdout (2025-10-20 → 2026-10-19) stays untouched unless something passes.
  - The context is built in yearly chunks with 60 days of warm-up, using only data known at each bar close.
  - No historical news or shadow forecasts are used: they didn't exist causally. The 24h range therefore comes from 24h volatility.
- **Decisions.** At every completed 1h close while flat, the entry rule picks a side (or none). The trade-plan rules v1 (step A design) must accept the plan, including the macro blackout.
- **Entries and exits.**
  - Fill at the next 5m open in the `futures-v1` simulator, $10,000 account, 2× isolated, 0.25% risk per trade.
  - Exits are only the plan's stop, target and the 24h time limit; stops are never moved.
  - The 2% daily-loss halt applies. A 10% drawdown halt is recorded and, for historical evaluation only, reviewed automatically at the next UTC day.
- **Entry rules.** Each rule is run with the plan's minimum reward:risk at 1.5 and at 2.0.

  | ID | Rule |
  |---|---|
  | B1 | trend: long if the 1h and 4h trend (close vs EMA50) are both up; short if both down |
  | B2 | best plan: of the long and short plans that pass every rule, take the one with the higher reward:risk |
  | B3 | pullback: long if the 4h trend is up and 1h RSI < 40; short if the 4h trend is down and 1h RSI > 60 |
  | B0 | control: a seeded coin flip (seed 0) picks the side; enter if that plan passes |

  B1–B3 form the family "TP" (6 configurations). B0 forms the family "TP-control" (2 configurations); it is reported, not selected.
- **Scoring.**
  - Each trade's net return = (price P&L − fees + funding) / entry notional.
  - Hit = net return > 0.
  - Account return, maximum drawdown, trades per month and the exit-reason mix are also reported.
- **Selection.** Walk-forward over 6-month blocks (section 5): the configuration with the best mean net return over trades that exited before the block, among those with ≥ 10 trades.
- **Pass rule.** Section 7 with the intraday thresholds (≥ 100 trades), applied to the pooled out-of-sample trades.
  - N = every intraday trial: the 105 earlier ones plus these 8.
  - V[SR] = the variance of per-call Sharpe across intraday trials with ≥ 30 calls (amendment A3).
- **Holdout.** Evaluated once, only for a family that passes: ≥ 10 trades, mean net > 0, hit rate ≥ the development break-even.

**A5 — 2026-10-21, data correction (no evaluation affected).** Liquidation collection recorded **nothing** from 2026-10-20 14:00 UTC until about 19:30 UTC.
- **Cause:** Binance's legacy futures websocket path (`fstream.binance.com/ws/…`) accepted the connection but delivered no messages, as a test on the trade stream confirmed. The collector now uses the all-symbol stream on the market path, `wss://fstream.binance.com/market/ws/!forceOrder@arr`, keeps BTCUSDT, and reconnects after 15 silent minutes.
- **Effect on I6:** its data start is about 2026-10-20 19:30 UTC; the earliest evaluation date (2027-04-20) still gives about 6 months.
- **Unaffected:** order-book depth (REST) was recorded throughout.

**A6 — 2026-10-21, rule engine: event-triggered playbooks (rulebook v1). Registered before any of them was evaluated.**

- **Question.** Do the playbooks in `rulebook/v1.yaml` (hash `3ea0f47e52c1`) make money after costs? They are built from the owner's rulebook: R01–R18, SR-01..SR-06, the confluence tab and the diagnostics tab. Design: `docs/superpowers/specs/2026-10-21-rule-engine-design.md`. Every threshold is in that file; changing any of them makes a new rulebook version that needs its own amendment.
- **Data and spans.**
  - Binance USD-M BTCUSDT 5m candles, mark, open interest and positioning, funding settlements, Fear & Greed, and the macro calendar (first-release times), from the PostgreSQL store.
  - Development: decisions from 2021-10-20 to the last 15m close before 2025-10-20. Patterns are built in yearly chunks with 60 days of warm-up. No row at or after 2025-10-20 is read: a trade still open at the end of the data exits at the last close, marked `END_OF_DATA`.
  - The holdout (2025-10-20 → 2026-10-19) stays untouched.
  - Liquidations, order-book depth and classified news exist only from 2026-10. Historically, the LIQ_BURST and NEWS_SHOCK triggers and the thin-book gate never fire, and PB13L/PB13S have no hits; they are shadow-only.
- **Decisions.**
  - At every completed 15m close, each playbook whose trigger window is open is evaluated (conditions, regime, levels, `tradeplan.size` with rules v1 sizing and minimum reward:risk 1.5).
  - The gates apply: cascade halt, macro blackout, funding window, 2-loss cooldown, account halts.
- **Entries, exits and costs.**
  - Fill at the next 5m open. Fees 5 bps per side.
  - Slippage 3 bps per side, or **9 bps** when a DUMP or SPIKE trigger fired in the hour before the decision.
  - Funding settlements crossed are paid or received.
  - Stop first when a bar touches both stop and target; a gap through the stop fills at the open.
  - The stop moves to break-even plus costs once the trade has gone `breakeven_at_r` R in its favour. A playbook's abort conditions exit at the next open after a 15m close where they hold. Each playbook has its own time stop (≤ 24h).
- **Per-playbook scoring.**
  - Each executable playbook (PB01–PB12, long and short variants separate: 20 configurations) is one frozen configuration and one trial.
  - Its trades are its confirmed hits that pass the plan rules and the gates, taken one position at a time within that playbook.
  - Net return = (price P&L − fees + funding) / entry notional; hit = net > 0.
  - Pass rule: section 7, intraday thresholds, on all development trades. N = every intraday trial in the registry, including these. With no parameter selection, PBO is 0 by definition. "Most years positive" counts calendar years with ≥ 5 trades.
- **Combined account (one more trial, `RE-combined`).**
  - The arbiter over all playbooks in one `futures-v1` account: $10,000, 0.25% risk per trade, 2× isolated, one position.
  - Flips allowed for `may_flip` playbooks. The 2% daily-loss halt applies. The 10% drawdown halt is reviewed automatically at the next UTC day, as in A4.
  - It is scored with the same pass rule on its trades. Account return and maximum drawdown are also reported.
- **Counterfactual record (for M3 and M5).** Every confirmed hit, taken or not, is simulated under both its normal stop and the wide stop (stop and target distances × 1.67), with MAE, MFE, R and the 4 hours after exit. These are the inputs to the ML filter and the learner. They are not separate trials.
- **Layer gates (registered now, evaluated later on these development hits only).** The walk-forward uses 6-month blocks, as in section 5.
  - **ML filter (M3).** Per playbook, L2 logistic regression on standardized features. It is refit at each block on hits that exited before the block, and takes a hit only if p > 1/(1 + RR). It goes live for a playbook only if the filtered test trades number ≥ 30 and their mean net return beats the unfiltered mean, with a bootstrap 95% CI of the difference above 0.
  - **Learner (M5).** Shrinkage means (prior 0 R, 20 pseudo-observations) per playbook × regime × action (skip, take, take_wide). The policy takes the best action whose 80% lower bound is > 0, otherwise it skips. It goes live only if its pooled test mean R beats "always take", with a bootstrap 95% CI above 0.
  - **Trade-management RL (M5).** Trained on 2021-10 → 2023-10 hits and tested on 2023-10 → 2025-10. It goes live only if its test mean R beats the fixed break-even rule, with a bootstrap 95% CI above 0.
- **What runs live regardless.** At the owner's request, rulebook v1 drives the separate experimental paper account from the day it is deployed. That is data collection; it is not a pass. The frozen release and its paper account are unchanged.

**A7 — 2026-10-21, rulebook v2: target selection only. Registered before v2 was evaluated.**

- **Erratum to A6.** A6 says 20 executable configurations. The rulebook has **18**: PB01–PB06 plus the long and short variants of PB07–PB12. PB13L/S are shadow-only. The A6 run registered 18 playbooks plus `RE-combined`, giving N = 132 intraday trials.
- **A6 result (rulebook v1, hash `3ea0f47e52c1`).**
  - 7,572 confirmed hits, of which 7,365 were rejected by the plan rules for net reward:risk below 1.5.
  - The cause: v1's target is the *nearest* candidate level, which is usually well inside the 1–2 ATR stop (median reward:risk about 0.3).
  - Only 106 trades were taken. No playbook passed. The combined account made 50 trades, returned −1.77% and had a 2.8% maximum drawdown.
  - Seen before writing this amendment: v1's per-playbook trade counts, hit rates and mean net returns (development span), and first-year outcome summaries. No v2 outcome has been computed.
- **The one change (rulebook v2, `rulebook/v2.yaml`, hash `db3d684ff71c`).** A target is the nearest candidate level whose net reward:risk (fees, slippage and expected funding included) is at least `min_reward_risk`. If none clears it, the playbook's fallback R-multiple target is used. The band cap, stops, triggers, conditions, gates, management and costs are exactly as in v1.
- **Evaluation.** Identical to A6 in a new run directory. The trial registry carries every earlier trial, so the 18 v2 playbooks and `RE-combined` add 19 trials (N = 151). Same section 7 pass rule, same counterfactual record. The M3/M5 layer gates registered in A6 apply to the v2 hits.
- **Live.** The experimental paper account runs v2 from deployment. That is data collection, not a pass.

**A8 — 2026-10-22, four paper accounts with confidence tiers (`rulebook/accounts-v1.yaml`). Registered before the score model was fitted.**

- **Purpose.** Live data collection, at the owner's request, not a pass. Four separate $10,000 paper futures accounts trade the rulebook v2 setups (hash `db3d684ff71c`; playbooks, triggers, checklists, levels and management unchanged). They differ only in which setups they take and how much they risk. No account changes its own configuration. Any change is `accounts-v2` plus a new amendment.
- **Seen before writing this amendment.** Per-playbook win rates and mean net returns of all v2 confirmed setups (development span), and a walk-forward table of playbook × regime × confluence win-rate estimates against outcomes. Estimated win chance never exceeded about 66%, and setups estimated at 50% or more won 41%. No logistic score below has been fitted or evaluated.
- **Training set.** v2 research hits (`artifacts/rule-engine/research-v2-20261021`) that:
  - have a plan;
  - have a valid outcome that exited before 2025-10-20;
  - have no rejection reason, or only `REWARD_RISK_BELOW_MIN`.
  `INVALID_LEVELS` and `STOP_TOO_WIDE` are excluded because they are rejected live too.
- **Score model.** One L2 logistic regression (C = 1.0, median imputation, standardization), with the feature list of the M3 filter (`meta.FEATURES`). Label: net > 0.
  - `p` = predicted probability.
  - `score = p·RR − (1 − p)`, where RR is the plan's net reward:risk after costs.
  - For the cutoffs, walk-forward scores come from refits at the 6-month blocks of section 5 on hits that exited before each block. The live model is one final fit on the whole training set, then frozen. XGBoost is not used.
- **Accounts.**

  | Account | Takes setups at or above the percentile | Full risk at or above | Leverage cap | Risk per trade | Daily brake | Floor |
  |---|---|---|---|---|---|---|
  | `acct-a` Careful | 90th | 95th | 2× | 1% | 3% | $5,000 |
  | `acct-b` Steady | 70th | 85th | 3× | 1.5% | 4.5% | $5,000 |
  | `acct-c` Bold | 60th | 80th | 4× | 2% | 6% | $5,000 |
  | `acct-d` Aggressive | 50th | 75th | 5× | 2.5% | 7.5% | $5,000 |

  - Percentiles are of the walk-forward development scores and are resolved once to fixed score numbers.
  - Below the full-risk line, half the risk is used.
  - Quantity = risk amount ÷ (stop distance + costs), with notional capped at equity × leverage. The stop must be inside the liquidation price.
- **Account rules.**
  - The rulebook's reward:risk minimum does not apply; the score replaces it. Structural plan rejections still apply.
  - Gates as in rulebook v2. The cooldown counts the account's own trades.
  - One position per account, no flips. With several eligible setups, the highest score is taken.
  - Market orders and v2 costs.
  - The daily brake closes the position and blocks entries until the next UTC day. Below the floor, the account stops for review. The 10% drawdown halt does not apply to these accounts.
  - Each account **pauses after 30 closed trades** and resumes only when the owner says so.
- **Watch items.** Order-book imbalance, spread, liquidations against the side, the latest news stance and time to the next macro release. They have no history, so they are recorded with every decision but are not in the score.
- **Reported, not gated.** Per-account development expectations from the walk-forward scores, one position at a time: trades per week, win rate, mean R, worst drawdown, final equity. Each 30-trade review compares the live results with them.
- **The holdout (2025-10-20 → 2026-10-19) stays untouched.** Live trading begins on deployment.


**A9 — 2026-10-23, model health for the A8 score model (`src/quantpilot/model_health.py`). Registered before deployment.**
- **Scope.** Monitoring of the frozen score model `7996f4bd6a09`. Nothing here changes the model, the cutoffs or the account rules, with one exception: the data check below.
- **Reference.** Built once from the A8 development training set (7,071 setups, `artifacts/rule-engine/research-v2-20261021`):
  - per input: present share, range, quintile edges and training shares, with missing as a bin of its own;
  - the critical inputs, present in at least 99% of training setups (56 of 67);
  - the walk-forward Brier score: 0.2496;
  - the drift baseline: rolling 50-setup windows of the development setups in time order, step 10, 703 windows.

  A reference for any other model hash is ignored.
- **Data check (behaviour change).** A setup is held back as `DATA_CHECK_FAILED` when any of these holds:
  - an input is infinite;
  - an input lies more than one full training range beyond the training minimum or maximum;
  - a critical input is missing.

  The check runs after the paused check and before the cutoff. On the training set it would have held back 2.1% of setups. Without a valid reference it is skipped and noted. It cannot make an account take a setup it would otherwise skip.
- **Premium fix.** Before a 15m decision, stale mark and index prices are refreshed from the exchange. Every live setup since deployment had the premium missing, because those prices arrive in a later batch than the candle. The feature definitions are unchanged.
- **Calibration.** Each scored setup is counted once and matched with its simulated plan outcome (the training label).

  | Verdict | When |
  |---|---|
  | `TOO_FEW` | n < 30 |
  | `OVERCONFIDENT` / `UNDERCONFIDENT` | the mean predicted win chance lies above / below the Wilson 95% interval of the actual win rate |
  | `IN_LINE` | otherwise |
- **Drift.** PSI of each input over the latest 50 setups, against the training bins. An input counts as shifted when its PSI exceeds its own 95th percentile across the baseline windows.

  | Verdict | When |
  |---|---|
  | `ALARM` | the number of shifted inputs is above the baseline's 99th percentile |
  | `WATCH` | it is above the 90th percentile |
  | `STABLE` | otherwise |
- **Challenger.** It needs at least 200 resolved live setups after 2026-10-20 that have recorded inputs. It is refit with the A8 recipe on development plus those live setups; the holdout is never read. It is scored in shadow only.

  | Verdict | When |
  |---|---|
  | `TOO_FEW` | n < 50 |
  | `CHALLENGER_BETTER` | the paired-bootstrap 95% interval of the Brier difference lies wholly below 0 |
  | `NO_DIFFERENCE` | otherwise |
- **Overall status.**
  - **Alarm:** drift `ALARM`, calibration `OVERCONFIDENT`, or more than 20% of the last 7 days' setups failing the data check.
  - **Watch:** drift `WATCH`, a missing reference, or any data-check failure in the last 24 hours.
  - **OK:** otherwise.
- **No automatic promotion.** A challenger, or any model change, goes live only by the owner's decision, as `accounts-v2` with a new amendment.
