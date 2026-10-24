# Rule engine: event-triggered playbooks with ML, news and RL layers

Date: 2026-10-21. Branch `rule-engine` (from `trading-agent`). Owner request: a professional rule engine built from the owner's rulebook (`Bitcoin Futures Quant Strategies.docx`, `QuantPilot BTC Futures Rule Engine & Strategy Matrix.xlsx`). It acts when the market does something unusual, and it brings in technical indicators, forecasting, ML, news sentiment and reinforcement learning. All five milestones are built locally. Paper only: nothing places or sizes real trades.

## 1. Principles

1. **Event-triggered.** Nothing is evaluated for entry unless a trigger fires: an anomaly (spike, dump, OI shock, liquidation burst) or a technical "new story" (level break or sweep, squeeze release, trend flip, range edge, macro release, news shock).
2. **Rules decide the setup; other layers decide whether to act.** A playbook gives the side, entry, stop, target, abort and time stop. The ML filter, news check and learner can only *veto* or choose among pre-defined actions.
3. **Every layer earns its influence.** Each layer has a mode, `off`, `shadow` or `live`. Shadow layers record what they would have done. A layer becomes `live` for a playbook only after a pre-registered test shows that it improves on the layer below.
4. **Causal everywhere.** Every input is computed from data known at the decision instant. A missing input makes a condition false; it is never replaced by a default.
5. **Rules are data.** The rulebook is a versioned YAML file (`rulebook/v1.yaml`) whose hash identifies the policy. The evaluator never runs code from it (a fixed operator table only). An Excel copy is generated from it.
6. **Frozen account untouched.** The released XGBoost paper account (`engine.py`, SQLite) is unchanged. The rule engine runs a separate experimental `futures-v1` account.
7. **Honesty.** Historical results are reported for every playbook, including failures, with the section 7 pass rule. The holdout year (2025-10-20 → 2026-10-19) stays untouched.

## 2. Pipeline

```
data → patterns (causal features) → triggers → regime → playbooks (rulebook) → plan (tradeplan sizing)
     → ML filter → news check → learner → gates/arbiter → futures-v1 account → journal → diagnosis/learning
```

## 3. Components

### 3.1 Patterns (`patterns.py`)
`pattern_frame(frames, at=None)` extends `context.context_frame` with the inputs the rules need. Everything is computed from completed 5m bars and the families' availability times.
- **Moves:** `ret_15m`, `ret_1h`, `ret_4h`, `ret_24h` (log). `move_z_15m` = 15-minute return / std of 15-minute returns over the previous 24h. `move_z_1h` is the same with 1h returns over the previous 7 days.
- **Flow:** `volume_z_15m` (log volume vs the previous 24h); `taker_share_15m`; `oi_change_15m`; `oi_z_1h`.
- **Positioning:** `funding_z` (latest settlement vs the previous 90 settlements); `oi_price_state` ∈ {longs_opening, shorts_covering, shorts_opening, longs_closing}; `fear_greed`.
- **Candle anatomy** of the last completed 15m bar: body ratio, upper/lower wick ratios, close location, up/down.
- **VWAP:** anchored 00:00 UTC with a volume-weighted SD; `vwap`, `vwap_z`, `vwap_upper2`, `vwap_lower2`.
- **EMA stack** (9/21/50/200) on 15m and 1h: `ema_stack_15m`, `ema_stack_1h` ∈ {+1, −1, 0}; `ema21_15m`; `ema200_slope_1h`.
- **Squeeze** on 15m: Bollinger(20, 2) inside Keltner(20, 1.5·ATR). `squeeze_on`, `squeeze_release`.
- **Volatility and forecasting:**
  - `atr_pct_1h`: percentile of 1h ATR over 30 days.
  - `vol_forecast_1h`: EWMA (λ = 0.94) of hourly log returns.
  - 80% bands for the current 1h and 4h UTC blocks: anchored at the block's open price, half-width 1.2816·σ·√h. These give `band4_high`, `band4_low`, `range_pos_1h`, `range_pos_4h`, `room_up_atr` and `room_down_atr`. This is the volatility-scaled form of the deployed range model, so it can be computed for any past date.
- **RSI divergence** on 15m (bullish and bearish).
- **Levels:** candidate supports and resistances are the confirmed 1h/4h swing levels plus the prior day and week high/low, all known at the *previous* 15m close. They give `support`, `resistance`, their distances in ATR, and `level_confluence` (how many level types lie within 0.25·ATR of each other).
- **Level events** on the last completed 15m bar, against levels known before it (ATR = 1h ATR):
  - `sweep_low` / `sweep_high`: wick beyond the level by ≥ 0.1·ATR, close back inside;
  - `break_down` / `break_up`: close beyond the level by ≥ 0.3·ATR, body ratio ≥ 0.6;
  - `hold_support` / `reject_resistance`: touch within 0.15·ATR, close inside, wick ≥ 0.5;
  - `retest_from_below` / `retest_from_above`: a break within the last 8 bars, then a touch of the broken level from the other side with the close rejected.
- **Forward-only inputs**, missing before their collection started:
  - liquidations: `liq_long_usd_15m`, `liq_short_usd_15m`, `liq_z_15m`;
  - depth: `spread_bps`, `imbalance_15bps`, `depth_15bps_pct`.

### 3.2 Triggers (`triggers.py`)
Triggers are evaluated at every 5m close (every 1m close live). Each trigger records its time, type, direction, strength and episode facts: the extreme price during the episode, the OI change and taker share over it, and the level involved.

| Trigger | Definition |
|---|---|
| `DUMP` / `SPIKE` | `move_z_15m` ≤ −4 / ≥ 4 and `volume_z_15m` ≥ 1.5 (or `move_z_1h` beyond ±4) |
| `OI_SHOCK_DOWN` / `OI_SHOCK_UP` | `oi_change_15m` ≤ −1% / ≥ +1% |
| `LIQ_BURST_LONG` / `LIQ_BURST_SHORT` | forward only: liquidations in 15m ≥ $2M and `liq_z_15m` ≥ 4 |
| `FUNDING_EXTREME_HIGH` / `_LOW` | `funding_z` ≥ 2.2 / ≤ −2.0 (active while it holds) |
| `LEVEL_BREAK_UP` / `_DOWN`, `LEVEL_SWEEP_HIGH` / `_LOW` | from the level events |
| `SQUEEZE_RELEASE` | `squeeze_release` with `volume_z_15m` ≥ 1.5 |
| `TREND_PULLBACK_UP` / `_DOWN` | 1h EMA stack aligned, ADX(1h) ≥ 25, price within 0.3·ATR of the 15m EMA21 |
| `RANGE_EDGE_UPPER` / `_LOWER` | ADX(1h) < 20 and `range_pos_4h` ≥ 0.9 / ≤ 0.1 |
| `MACRO_RELEASE` | a high-impact macro release 15–120 minutes ago |
| `NEWS_SHOCK_UP` / `_DOWN` | M4: a high-impact, high-confidence classified news item within 2h |

A trigger opens a **watch window** (the playbook's `within_bars`, in 15m bars). That is how playbooks wait for confirmation instead of entering on the spike candle.

### 3.3 Regime
The regimes are checked in this order:
1. `PANIC`: `atr_pct_1h` ≥ 0.97 and |`ret_4h`| ≥ 4%.
2. `HIGH_VOL`: `atr_pct_1h` ≥ 0.85.
3. `TREND_UP` / `TREND_DOWN`: ADX(1h) ≥ 25 and the 1h EMA stack is aligned.
4. `RANGE`: otherwise.

### 3.4 Rulebook (`rulebook/v1.yaml`) and evaluator (`rules.py`)
Each playbook defines:
- `id`, `name`, `family`, `side`, `source` (which owner rules it implements);
- `trigger: {any: [...], within_bars, min_bars}`;
- `regimes`;
- `conditions`: `[feature, op, value-or-feature]`, plus `any:` / `all:` groups;
- `stop: {anchor, buffer_atr}`;
- `target: {candidates, cap}`;
- `min_reward_risk`, `abort`, `time_stop_hours`, `breakeven_at_r`, `may_flip`, `priority`;
- `modes` for the ML, news and learner layers.

Operators: `< <= > >= == !=`. Feature names come from the pattern row, the trigger episode (`trigger.*`) and the position (`position.*`, for aborts). The evaluator returns a `Hit` holding each condition with its value and whether it held, plus the missing inputs.

**Playbooks v1:**

| ID | Name | Side | Owner rules |
|---|---|---|---|
| PB01 | Flush rebound | long | R11, R05/SR-03, R01 |
| PB02 | Dump continuation (breakdown retest) | short | R03/SR-02 |
| PB03 | Spike exhaustion fade | short | R06/SR-06, R02 |
| PB04 | Spike continuation (breakout retest) | long | R04/SR-05 |
| PB05 | Short squeeze ignition | long | R12 |
| PB06 | Crowded long flush | short | R13 |
| PB07 | Squeeze-release breakout | both | R09/R10 |
| PB08 | Trend pullback | both | R07/R08 |
| PB09 | Sweep and reclaim (incl. prior day/week high/low) | both | R05/R06, SR-03/SR-06 |
| PB10 | Range-edge / VWAP ±2 SD fade | both | R17 + 80% range |
| PB11 | Support hold / resistance rejection (absorption) | both | R01/R02, SR-01/SR-04 |
| PB12 | Post-macro-release breakout | both | R14/R15 (replaced: no consensus data) |
| PB13 | News-shock continuation | both | new (M4; shadow only, no history) |

**Gates:**

| ID | Gate | Owner rule |
|---|---|---|
| G01 | Cascade halt: `move_z_15m` beyond ±8 or `liq_z_15m` ≥ 6, then no entries for 30 minutes | R18 |
| G02 | Macro blackout (existing plan rule) | |
| G03 | Stale or missing critical data | |
| G04 | One position at a time | |
| G05 | Thin book: spread ≥ 3 bps or depth below the 5th percentile (live only) | |
| G06 | Cooldown: 2 consecutive losses on a playbook pause it for 24h | |
| G07 | No entries within 5 minutes of a funding settlement | |
| G08 | Account halts (2% daily loss, 10% drawdown) | |

R16 (Coinbase premium) is not included: it failed as S5/I5, and there is no live Coinbase feed.

**Trade management** (every playbook):
- move the stop to breakeven plus costs at +`breakeven_at_r` R;
- exit when an abort condition holds at a 15m close;
- exit at the time stop;
- a `may_flip` playbook that fires against an open position closes it at the next bar and enters on the following bar.

**Confluence score** (owner's Tab 3): +1/−1/0 per factor for EMA stack, ADX, RSI, volume z, taker share, VWAP distance, OI-price state, funding z and range position, signed toward the playbook's side. It is recorded, used for arbitration order and given to the ML filter. It is never a gate.

### 3.5 Plans and arbiter (`arbiter.py`)
- A hit's stop and target come from its spec. Sizing, costs, liquidation and reward:risk come from `tradeplan.build` (refactored out of `tradeplan.plan`, so `plan` keeps its behaviour).
- The arbiter applies the gates, then the layers (ML, news, learner, each in its mode), and takes at most one hit. Order: priority, then confluence, then reward:risk.
- Every other hit is journalled with the reason it was skipped.

### 3.6 Historical evaluation (`playbook_research.py`, amendment A6)
- **Development span:** 2021-10-20 → 2025-10-19, built in yearly chunks with 60 days of warm-up.
- **Decisions:** at 15m closes.
- **Per playbook:** an outcome simulation of every hit (`outcomes.simulate`): next 5m open fill, stop first when a bar is ambiguous, breakeven move, abort at 15m closes, time stop, fees, slippage and funding. Inside anomaly windows (a DUMP or SPIKE in the last hour) slippage is **3×**. Each trade records MAE, MFE, R and the high/low of the 4 hours after exit.
- **Scoring:** one position per playbook at a time, then section 7. Each playbook is one frozen configuration, so no thresholds are tuned. N is the existing intraday trials plus these.
- **Combined account:** the arbiter over all playbooks, replayed in the `futures-v1` simulator.
- The run also writes every hit's counterfactual outcome under both stop widths. These are the training data for M3 and M5.

### 3.7 Live engine (`rule_engine_live.py`, supervisor thread `rule_engine`)
- **Data:** the collector adds 1m candles (`interval='1m'` in `market_candles`).
- **Every minute:**
  - step the experimental account through new 1m bars;
  - detect triggers on 1m data and log them;
  - check aborts and breakeven at 15m closes;
  - at each 15m close, evaluate the playbooks on a context built from about 35 days of 5m data.
- **Persistence:** account state in `rule_accounts` (JSON) in PostgreSQL; journal in `rule_triggers`, `rule_hits` and `rule_trades`.
- **Diagnosis:** 4 hours after a trade closes it is coded; a skipped hit gets its counterfactual once its window ends. The 8 owner codes are kept, plus `STANDARD_VARIANCE`, `SKIP_NEUTRAL` and `TIME_EXIT_FLAT`. MAE/MFE are measured in R.

### 3.8 ML filter (`meta.py`, M3)
- **Dataset:** the A6 hits with their context and confluence features and their counterfactual outcome (win = net R > 0).
- **Model:** L2 logistic regression on standardized features, one per playbook (pooled by family when a playbook has fewer than 200 training hits). It is refit at each 6-month walk-forward block.
- **Decision:** take only if p·RR − (1 − p) > 0, i.e. p > 1/(1 + RR). No threshold is tuned.
- **Ablation and gate:** on the pooled walk-forward test trades, the filter becomes `live` for a playbook only if its filtered mean net R beats the unfiltered one with a bootstrap 95% CI above 0, and ≥ 30 filtered trades remain. Otherwise it stays `shadow`.
- The frozen XGBoost direction probability is shadow-only: it was trained on the development span, so using it historically would leak.

### 3.9 News layer (`news_events.py`, M4)
- **Crawl4AI** runs in its own virtual environment (`.venv-crawl`) as an isolated subprocess (`scripts/crawl_articles.py`, Python `-I`). It fetches the full text of new RSS items using its no-browser HTTP strategy by default (browser mode is configurable).
- **Storage:** article text in `news_articles` (SQLite, next to the news tables).
- **Classification:** subscription Claude via the existing locked-down runner (no tools, stdin only, no API keys). Each item gets `impact`, `category` (hack, exchange, regulation, etf, macro, adoption, other), `direction` and `confidence`. The daily call budget is capped.
- **Use:** the NEWS_SHOCK triggers and PB13. A **veto**: high-impact bearish hack/exchange/regulation news blocks longs for 6h (and the mirror for shorts). Both are shadow until forward evidence exists, because there is no point-in-time news history. Fear & Greed is a pattern feature and an ML input.

### 3.10 RL (`learner.py`, `rl_manage.py`, M5)
- **Learner:** a full-information contextual bandit over (playbook, regime). The actions are `skip` (R = 0), `take` and `take_wide` (stop distance × 1.67). Rewards are the simulated net R of each action, so no ε-exploration is needed. It uses a shrinkage posterior (prior 0 R, 20 pseudo-observations). The policy takes the best action if its posterior 80% lower bound is > 0, otherwise it skips.
  - **Walk-forward gate:** the learner goes `live` only if its pooled test mean R beats "always take" with a bootstrap 95% CI above 0. It updates online from the forward journal.
- **Trade-management RL:** a Gymnasium environment over A6 trade episodes.
  - State: unrealized R, bars held, MFE/MAE in R, distance to target in R, ATR ratio, taker share, trend.
  - Actions: hold, stop to breakeven, trail 1R behind, exit now.
  - Reward: final net R.
  - **Agents:** tabular Q-learning (light) and PPO (stable-baselines3, CPU, small network, checkpointed). Training uses 2021-10 → 2023-10 and testing 2023-10 → 2025-10, against the fixed rule (breakeven at +1R).
  - **Gate:** a policy becomes `live` only if its test mean R beats the fixed rule with a bootstrap CI above 0. Otherwise it stays `shadow`.

### 3.11 API, MCP and dashboard
- **Routes:**
  - `/api/v1/rules`: rulebook, modes, live checklist;
  - `/api/v1/rules/account`;
  - `/api/v1/rules/journal`;
  - `/api/v1/rules/research`: A6, M3 and M5 reports;
  - `/api/v1/rules/levels`: levels, VWAP bands and hit markers for the chart.
- **Dashboard:** a new **Rules** page with the live account, triggers, playbook checklist, journal and diagnosis codes, per-playbook statistics and layer modes. The Chart page gets level, VWAP-band and marker overlays.
- **Exports:** `quantpilot rulebook-xlsx` writes the Excel copy; `quantpilot research-playbooks` runs A6.

## 4. Errors and safety
- Missing inputs make a condition false and are recorded.
- A failed cycle is logged to `market_feeds` (`rules:engine`); the next minute resumes from the persisted state.
- Missed minutes are reconciled by stepping the stored bars; no decisions are made in the past.
- Crawled text is untrusted data. It reaches Claude only through stdin with tools disabled, and its fetch is bounded (size, time, count).
- Long research jobs are resumable per playbook and chunk.

## 5. Testing
Every module is built test-first. The tests cover:
- leakage (a feature at t must not change when future bars are appended);
- each level event on synthetic bars;
- each trigger;
- the evaluator's operators and missing-input handling;
- the outcome simulation paths (stop, target, ambiguous bar, breakeven, abort, time stop, costs);
- the arbiter's gates and ordering;
- live-cycle reconciliation on a temporary database;
- the ML gate on synthetic data;
- the learner and environment on synthetic episodes;
- the API envelopes;
- a Playwright spec for the Rules page.
