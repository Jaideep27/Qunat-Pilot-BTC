# Four Paper Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four live paper futures accounts (A–D) run the rulebook v2 setups. Each takes setups by a frozen confidence
score cutoff, sizes by its own risk and leverage, journals every decision to PostgreSQL, pauses at 30 trades and
gets a reviewed report.

**Architecture:**
- `rule_live.cycle` keeps computing the shared pipeline: patterns → triggers → hits.
- A new `account_live` module fans each confirmed hit out to the four accounts.
- `account_score` holds the frozen logistic score model and its cutoffs. `accounts` holds config, sizing and
  plain-English rules. `account_review` holds the explanations and the 30-trade review.
- New `account_*` tables in the market database; `rule_trades` stays untouched for `experimental-v1`.

**Tech Stack:** Python 3.11, pandas, scikit-learn, SQLAlchemy (PostgreSQL), FastAPI, React/Vite, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-four-accounts-design.md`

## Global Constraints

- Paper only: nothing places real orders. Write endpoints use `local_only(request)`.
- Claude is called only through `news.claude_json` (subscription login, no tools, stdin input, stripped env).
  Never an API key.
- The holdout 2025-10-20 → 2026-10-19 stays untouched. Fitting uses development hits only (exit before 2025-10-20).
- Amendment A8 is written in `docs/research/preregistration.md` **before** the score model is fitted.
- No XGBoost in rule-engine decisions.
- Accounts never change their own config. Any change is `accounts-v2` plus a new amendment.

**Accounts:**

| id | name | percentile | full-risk percentile | leverage | risk | daily brake | floor | round |
|---|---|---|---|---|---|---|---|---|
| `acct-a` | Careful | 90 | 95 | 2 | 0.01 | 0.03 | 5000 | 30 |
| `acct-b` | Steady | 70 | 85 | 3 | 0.015 | 0.045 | 5000 | 30 |
| `acct-c` | Bold | 60 | 80 | 4 | 0.02 | 0.06 | 5000 | 30 |
| `acct-d` | Aggressive | 50 | 75 | 5 | 0.025 | 0.075 | 5000 | 30 |

**Score:** `score = p·RR − (1−p)`, where `p` is the logistic probability that net > 0 and RR is the plan's net
reward:risk.

**Training set:** v2 research hits with a plan, a valid outcome, and either no rejection reason or only
`REWARD_RISK_BELOW_MIN`.

**Costs** are unchanged: 5 bps fee and 3 bps slippage per side, 3× slippage within 1h of a DUMP or SPIKE.

**No flips:** `may_flip` is ignored for these accounts; one position per account.

**Full test commands:**
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
- `cd frontend && npx playwright test --reporter=line`
- `node scripts/check-structure.cjs`

## Review Focus

1. **Restart with a pending entry order** (decided at a 15m close, server stopped before the next minute): the order
   fills once at the first new bar and is never duplicated or dropped silently. (Task 5 test.)
2. **Score model file missing or unreadable at startup:** the four accounts take no trades and show status
   `NO_SCORE_MODEL`, while `experimental-v1` keeps running. (Task 5 test.)
3. **Live feature row with missing values** (positioning lags 5–15 min, depth gaps): the score is still computed
   (median imputation), and the decision row lists the missing features. (Task 1 test.)
4. **Claude CLI unavailable or timing out:** trades keep their diagnosis code, the explanation stays pending and is
   retried next cycle, and a review is stored with numbers even without a summary. (Tasks 6 and 7 tests.)
5. **Daily brake firing while a position is open:** the position closes with `DAILY_BRAKE`, no entries until the
   next UTC day, and the trade counts toward the round. (Task 3 test.)

---

### Task 1: Pre-registration A8, score model, cutoffs, expectations

**Files:**
- Modify: `docs/research/preregistration.md` (append A8, dated 2026-10-22, **first**)
- Create: `src/quantpilot/account_score.py`
- Create: `rulebook/accounts-v1.yaml`
- Modify: `src/quantpilot/cli.py` (command `research-accounts --output <research run dir>`)
- Test: `tests/test_account_score.py`

**Interfaces:**
- Produces:
  - `training_set(hits: DataFrame) -> DataFrame`
  - `fit(train: DataFrame) -> dict(model, columns, n, trained_until)` (reuses `meta.matrix`, same pipeline as
    `meta.fit`, C=1.0)
  - `score(model, frame: DataFrame) -> DataFrame[p, score, missing]`
  - `walk_forward(hits) -> DataFrame` (adds `p`, `score`; 6-month blocks from `strategy_research.blocks`)
  - `cutoffs(scores: Series, percentiles: dict[str,int]) -> dict[str,float]`
  - `expectations(wf: DataFrame, config) -> dict[account_id, dict(trades_per_week, win_rate, mean_net_r, worst_drawdown, final_equity)]`
  - `run(output) -> dict` (writes `artifacts/rule-engine/accounts-score.joblib`, `accounts-report.json`, and resolved
    cutoffs into `rulebook/accounts-v1.yaml`)
  - `load_model(path=MODEL) -> dict | None`
  - `live_row(hit: dict, features: dict) -> DataFrame` (same columns as research; reuse `meta._row`)

- [ ] **Step 1:** Append amendment A8 to `docs/research/preregistration.md`. It records the account table, the
  score formula, the training set, the percentiles, the half-risk rule, the brakes, the 30-trade pause, the watch
  items, and that the holdout stays untouched. Commit: "Pre-register A8: four paper accounts".
- [ ] **Step 2: Write the failing tests:**
  - `test_training_set_keeps_rr_rejections_and_drops_structural_ones`: a frame with statuses TAKEN,
    PLAN_REJECTED(`REWARD_RISK_BELOW_MIN`), PLAN_REJECTED(`INVALID_LEVELS`), and NOT_CONFIRMED without a plan
    returns only the first two.
  - `test_training_set_excludes_holdout`: a row exiting 2025-10-21 is dropped.
  - `test_score_formula`: p=0.5, RR=2 → score 0.5; p=0.25, RR=1 → −0.5.
  - `test_score_tolerates_missing_features`: NaN feature values yield a finite score, and `missing` lists them.
  - `test_cutoffs_are_percentiles`: scores 1..100 with {a:90, d:50} → {a:≈90.1, d:≈50.5} (numpy linear).
  - `test_expectations_one_position_and_half_risk`: overlapping rows take only the first. A row between cutoff and
    full-risk line moves equity by half the risk × o_r.
- [ ] **Step 3:** Run `pytest tests/test_account_score.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4:** Implement `account_score.py`. The expectations replay walks the walk-forward rows in time order:
  - one position per account (skip rows whose `o_entry_time` < the previous exit);
  - `equity *= 1 + risk_used × o_r`, with risk halved below the full-risk line;
  - track the peak and the worst drawdown.
- [ ] **Step 5:** Run the tests. Expected: PASS.
- [ ] **Step 6:** Run `.venv/Scripts/python.exe -m quantpilot.cli research-accounts --output artifacts/rule-engine/research-v2-20261021`.
  Expected: model saved; cutoffs written into `accounts-v1.yaml`; report with expectations for 4 accounts.
- [ ] **Step 7:** Commit the code, tests, `accounts-v1.yaml` and the report.

### Task 2: Account config, sizing, plain-English rules

**Files:**
- Create: `src/quantpilot/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: `rulebook/accounts-v1.yaml` from Task 1.
- Produces:
  - `load(path=CONFIG) -> dict(accounts=[...], hash, model=dict(path, hash))`
  - `eligible(cfg_account, score) -> bool` (`score >= cutoff`)
  - `full_risk(cfg_account, score) -> bool` (`score >= full_risk_score`)
  - `size(cfg_account, plan: dict, score, equity, cash, funding_rate) -> dict(quantity, notional, margin, leverage, risk_fraction_used, risk_amount, liquidation, rejections)`
  - `choose(candidates: list[dict]) -> dict | None` (highest score)
  - `describe(cfg_account) -> dict(rules: list[str], watches: list[str])`
  - `watch_items(side, features, news_latest, macro_next_minutes) -> list[dict(name, value, ok, note)]`

**Sizing:**
- `qty = equity × risk / (|entry − stop| + entry × cost)`;
- cap the notional at `equity × leverage`;
- margin = notional / leverage;
- reject `STOP_BEYOND_LIQUIDATION`, `BELOW_EXCHANGE_MINIMUM` and `INSUFFICIENT_MARGIN` with the same rules as
  `tradeplan.size`.

**Watch items**, each with `ok` for the trade's side:

| Item | ok when |
|---|---|
| `book_imbalance_5bps` | sign agrees with the side |
| `spread_bps` | < 1 |
| `liquidations_against_15m` | USD liquidated on the trade's side < 1,000,000 |
| `news_latest` | stance not opposed |
| `macro_next` | more than 120 min away |

- [ ] **Step 1: Write the failing tests:**
  - `test_size_risks_the_account_fraction` (A, equity 10,000, entry 80,000, stop 79,200, cost 0.0016 →
    risk_amount ≈ 100 at full risk, ≈ 50 below the full-risk line);
  - `test_leverage_caps_the_notional` (D, stop 0.1% away → notional ≤ 50,000 and risk_fraction_used < 0.025);
  - `test_stop_beyond_liquidation_rejects`;
  - `test_choose_takes_the_highest_score`;
  - `test_describe_mentions_cutoff_risk_leverage_and_30_trades`;
  - `test_watch_items_for_a_long`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement `accounts.py`. The config hash is sha256 of the canonical YAML text, first 12 characters.
- [ ] **Step 4:** Run them. Expected: PASS. Commit.

### Task 3: Per-account limits in the futures simulator

**Files:**
- Modify: `src/quantpilot/futures.py` (`initial`, `_bar`, `_open`)
- Test: `tests/test_futures.py`

**Interfaces:**
- Produces: `initial(balance, bar_minutes=5, daily_loss=DAILY_LOSS, max_drawdown=MAX_DRAWDOWN, floor=None)`. The
  state carries `daily_loss`, `max_drawdown` (None = off) and `floor` (None = off), plus `floor_hit: bool`.
- Halt reasons are `DAILY_LOSS_HALT` (unchanged default) and `FLOOR_HIT`.
- `_open` skips the reward:risk check when `plan['min_reward_risk'] is None`. Levels are still validated: the stop
  and target must sit on the correct sides of the fill, otherwise `INVALIDATED_AT_FILL`.
- States without the new keys behave exactly as today.

- [ ] **Step 1: Write the failing tests:**
  - `test_custom_daily_brake_closes_and_blocks_until_next_day`: daily_loss 0.075, a 3% loss doesn't halt; an 8% move
    closes with `DAILY_LOSS_HALT`, the next-day bar clears it, and the trade is in `trades`.
  - `test_floor_hit_closes_and_stops`: floor 5000.
  - `test_max_drawdown_none_never_halts`.
  - `test_old_state_without_keys_keeps_defaults`.
  - `test_open_without_min_reward_risk_still_validates_levels`.
- [ ] **Step 2:** Run `pytest tests/test_futures.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement, reading limits with `s.get(...)` and falling back to the module constants.
- [ ] **Step 4:** Run the full futures and rule_live tests. Expected: PASS. Commit.

### Task 4: Journal tables for accounts

**Files:**
- Create: `src/quantpilot/account_journal.py` (registered in `store.initialize` next to `journal`)
- Test: `tests/test_account_journal.py`

**Interfaces:**
- **Tables:**

  | Table | Primary key | Columns |
  |---|---|---|
  | `account_decisions` | `id` = `<account>:<hit id>` | account, hit_id, time, decision, score, confidence, cutoff, trade_id, payload JSON (ticks, watch, sizing, snapshot, model) |
  | `account_trades` | `id` = `<account>:<hit id>` | account, round, number, playbook, side, entry_time, exit_time, net, r, reason, payload JSON, diagnosis, diagnosed_at, explanation, explained_at |
  | `account_reviews` | `id` = `<account>:round-<n>` | account, round, created_at, status, payload JSON, resumed_at |

- **Functions:**
  - `add_decisions(engine, rows) -> int` (insert-new only)
  - `add_trade(engine, account, round, number, trade) -> bool`
  - `trades(engine, account=None, limit=500, undiagnosed=False, unexplained=False)`
  - `decisions(engine, account=None, limit=200)`
  - `diagnose(engine, trade_id, code, detail, now)`
  - `explain(engine, trade_id, text, now)`
  - `save_review(engine, account, round, payload, status)`
  - `reviews(engine, account=None)`
  - `mark_resumed(engine, account, round, now)`
- Account state lives in the existing `rule_accounts` table via `journal.save_account` / `load_account`.

- [ ] **Step 1: Write the failing tests:**
  - `test_same_hit_in_two_accounts_makes_two_rows`;
  - `test_add_trade_is_idempotent`;
  - `test_trades_filter_by_account_and_undiagnosed`;
  - `test_review_round_trip_and_resume`.

  Use SQLite via `store.database('sqlite:///' + tmp)` and `store.initialize`, as other journal tests do.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. Register the module in `store.initialize`.
- [ ] **Step 4:** Run them. Expected: PASS. Commit.

### Task 5: Live fan-out to the four accounts

**Files:**
- Create: `src/quantpilot/account_live.py`
- Modify: `src/quantpilot/rule_live.py` (`cycle` calls `account_live.step_all` after the reference account, passing
  `pf`, `table`, the new bars, the decision hits and their features)
- Modify: `src/quantpilot/arbiter.py` (`decide` returns `features` per hit id in its output, so features aren't
  recomputed)
- Test: `tests/test_account_live.py`

**Interfaces:**
- **Consumes:**
  - `account_score.load_model`, `account_score.live_row`, `account_score.score`;
  - `accounts.load`, `accounts.eligible`, `accounts.size`, `accounts.choose`, `accounts.watch_items`;
  - `futures.initial` with limits;
  - `account_journal.*`;
  - `arbiter.gates`, `arbiter.cooldown`, `arbiter.manage`.
- **Produces:**
  - `step_all(engine, book, pf, table, fast_new, decision, now, frames, macro_events, news_events, model=None, cfg=None) -> dict[account_id, summary]`
  - `new_state(cfg_account, now, last_bar) -> dict`
  - account state keys: `futures`, `round` (starts 1), `in_round` (closed trades this round), `number` (all-time
    trade count), `status` (`RUNNING` / `PAUSED_FOR_REVIEW` / `FLOOR_HIT` / `NO_SCORE_MODEL`), `last_bar`,
    `last_decision`, `config_hash`, `model_hash`, `extra`.

**Per account, each cycle:**
1. Step the new 1m bars like `rule_live._step_bars`: stops, targets, funding, the break-even move, and at 15m closes
   aborts and the time stop. No flips, and no RL manager acting (shadow is fine).
2. Each newly closed trade gets the next `number` and `round`, is added to `account_trades`, and increments
   `in_round`. At 30, set `PAUSED_FOR_REVIEW` and `futures.paused = True`.
3. At a fresh 15m decision, for every hit with a plan whose reasons are empty or only `REWARD_RISK_BELOW_MIN`:
   - compute the score once (shared across accounts);
   - per account, one decision row with status `TAKEN`, `BELOW_CUTOFF`, `GATED`, `COOLDOWN`, `POSITION_OPEN`,
     `PAUSED`, `PLAN_REJECTED` (sizing rejections) or `LOWER_SCORE`;
   - ticks = the hit's `checks` plus the watch items, and a market snapshot from the pf row;
   - enter the chosen plan with `min_reward_risk=None` and the account's leverage and quantity.
4. If the model is missing: status `NO_SCORE_MODEL`, decision rows `NO_SCORE_MODEL`, no entries.

**Snapshot fields**, from the pf row and context:
- `close`, `ret_1h`, `ret_4h`, `ret_24h`;
- `relative_volume`, `global_ratio`, `top_position_ratio`, `funding_rate`, `oi_change_1h`, `taker_share_1h`;
- `imbalance_5bps`, `spread_bps`, `liq_long_usd_15m`, `liq_short_usd_15m`, `fear_greed`;
- `news_latest` (title, stance, impact) and `macro_next` (label, minutes).

- [ ] **Step 1: Write the failing tests.** Reuse `test_rule_live.py` helpers (`book()`, `market`, `minute_bars`) and
  a stub model whose score is fixed per playbook (L1=0.9, L2=0.1). Cutoffs: A=0.5, D=0.0.
  - `test_each_account_decides_independently`: A takes L1; D takes L1 (highest score); four decision rows per hit
    with distinct ids.
  - `test_below_cutoff_is_recorded_not_taken`: L2 in A → `BELOW_CUTOFF`.
  - `test_trade_numbers_continue_after_restart`: run cycles to 2 closed trades, reload state from the DB with a
    fresh module cache, run on → the third trade is number 3.
  - `test_pending_entry_survives_restart_once`: stop after the decision cycle, restart → exactly one fill.
  - `test_pause_at_round_length`: with round length 2 in a test config → `PAUSED_FOR_REVIEW` after 2 trades and
    then only `PAUSED` decisions.
  - `test_missing_model_blocks_accounts_not_reference`.
  - `test_long_downtime_settles_open_position_on_missed_bars`.
  - `test_live_features_match_research_columns`: `live_row` columns equal `meta.FEATURES` inputs.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement `account_live.py` and wire it into `rule_live.cycle`. The reference account's behaviour
  is unchanged (`tests/test_rule_live.py` stays green).
- [ ] **Step 4:** Run `pytest tests/test_account_live.py tests/test_rule_live.py tests/test_arbiter.py -q`.
  Expected: PASS. Commit.

### Task 6: Per-trade diagnosis and explanation

**Files:**
- Create: `src/quantpilot/account_review.py` (explanations part)
- Modify: `src/quantpilot/account_live.py` (call `diagnose_and_explain` each cycle)
- Test: `tests/test_account_review.py`

**Interfaces:**
- Produces:
  - `diagnose_trades(engine, window, now) -> int`: after exit+4h, `diagnosis.from_trade` plus `diagnosis.code`
    stored via `account_journal.diagnose`, with the post-exit high and low.
  - `explain_trades(engine, now, runner=news.claude_json, limit=3) -> int`: diagnosed but unexplained trades; at most
    `limit` per cycle; failures leave them pending.
  - `EXPLAIN_SCHEMA = {why: str (≤ 600 chars), lesson: str (≤ 300)}`

**Prompt input:** only the trade's decision payload (ticks, watch, snapshot, score), its outcome and its diagnosis,
as JSON. The system prompt says to treat the content as data.

- [ ] **Step 1: Write the failing tests:**
  - `test_trades_are_diagnosed_after_the_window`;
  - `test_explanation_stored_from_runner` (fake runner returns {why, lesson});
  - `test_runner_failure_leaves_trade_pending_and_retries`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them. Expected: PASS. Commit.

### Task 7: The 30-trade review and resume

**Files:**
- Modify: `src/quantpilot/account_review.py`
- Modify: `src/quantpilot/account_live.py` (when an account pauses, `build_review` runs; `resume(engine, account_id, now)`
  starts the next round)
- Test: `tests/test_account_review.py`

**Interfaces:**
- **Produces** `build_review(engine, account_id, round, cfg, expectations, runner=news.claude_json) -> dict`, stored
  with status `ready`. The payload contains:
  - `numbers`: win_rate, total_net, mean_r, max_drawdown, by_playbook, by_reason, by_diagnosis, by_regime, and
    expected vs actual;
  - `separators`: per tick, watch item and snapshot field, winners vs losers, with an effect size (difference in
    means / pooled SD, or a difference in pass rates) and `label` = `hint` or `consistent`. `consistent` only when
    |effect| ≥ 0.8 and both groups have ≥ 8 trades;
  - `learner`: per trade, `learner.choose(stats, playbook, regime)` from `learner.load()`;
  - `manager`: for each trade with stored 1m bars, the `rl_manage.live_manager()` action sequence replayed through
    `rl_manage.play` on that trade's path vs the actual result (skipped when no policy exists);
  - `summary`: Claude {where_it_went_wrong, why, suggestions[≤3]}, or null with `summary_error` if the runner fails.
- **Produces** `resume(engine, account_id, now) -> dict`: only from `PAUSED_FOR_REVIEW`. Sets `round += 1`,
  `in_round = 0`, `status RUNNING`, `futures.paused = False`, and `mark_resumed`.

- [ ] **Step 1: Write the failing tests.** Use 30 synthetic trades with decision payloads.
  - `test_review_numbers_and_separators`;
  - `test_review_stored_without_summary_when_runner_fails`;
  - `test_resume_only_from_paused_and_starts_next_round`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them. Expected: PASS. Commit.

### Task 8: API, services, MCP

**Files:**
- Modify: `src/quantpilot/services.py`, `src/quantpilot/api.py`, `src/quantpilot/mcp_server.py` and/or
  `mcp_proxy.py` (whichever builds `get_rule_engine`)
- Test: `tests/test_rules_api.py`

**Interfaces:**
- `GET /api/v1/rules/accounts` returns `data.accounts[]`, each with:
  - `id, name, status, equity, balance, account_return, round, in_round, round_length, number`;
  - `position` (entry, stop, target, liquidation, entry_time, playbook, score);
  - `rules[], watches[]` from `accounts.describe`;
  - `watch_now[]` (current watch-item values from the overlay row);
  - `last_decision` (hit, decision, score, cutoff, ticks summary);
  - `expectations`, `config_hash`, `model_hash`, `review` (latest status and id).
- `GET /api/v1/rules/decisions?account=&limit=`
- `GET /api/v1/rules/account-trades?account=&limit=`
- `GET /api/v1/rules/reviews?account=`
- `POST /api/v1/rules/accounts/{id}/resume`: `local_only`; 409 unless `PAUSED_FOR_REVIEW`.
- `get_rule_engine` includes an `accounts` summary (id, status, equity, in_round).

- [ ] **Step 1: Write the failing tests:**
  - `test_accounts_endpoint_lists_four_with_rules_text`;
  - `test_decisions_filter_by_account`;
  - `test_resume_rejects_non_local_and_non_paused` (TestClient with client host other than 127.0.0.1 → 403).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them. Expected: PASS. Update the MCP tool count in `scripts/check-structure.cjs` only if a tool
  is added (none is planned). Commit.

### Task 9: UI: account cards, trade detail, reviews

**Files:**
- Create: `frontend/src/pages/Accounts.tsx` (section component used inside `Rules.tsx`)
- Modify: `frontend/src/pages/Rules.tsx`, `frontend/src/pages/Overview.tsx` (or the overview component that shows
  the strip), `frontend/src/styles.css`
- Test: `frontend/tests/accounts.spec.ts`, extend the mocks in `frontend/tests/responsive.spec.ts` with the
  `accounts` key

**Behaviour:**
- **Four cards:** grid on wide screens, one column under 720px. Each shows name, status pill, equity and return,
  "Trade n / 30" progress, the round, a Rules list and a Watches list (with live values), the open position, and
  the last decision with ticks (✓/✗).
- **Trades tab:** an account filter; a row expands to show why it was taken, the outcome, the diagnosis and the
  explanation.
- **Review:** when paused, a "Review ready" link opens the review (numbers, separators, summary), plus a **Resume**
  button that POSTs to the resume endpoint.
- **Overview strip:** the four equities and progress.

- [ ] **Step 1: Write the failing Playwright test** `accounts.spec.ts`:
  - four cards render with "Trade 12 / 30";
  - the Rules text includes "Leverage 2×";
  - the Resume button is visible only for the paused fixture account;
  - no horizontal overflow at 375px.
- [ ] **Step 2:** Run `npx playwright test tests/accounts.spec.ts`. Expected: FAIL.
- [ ] **Step 3:** Implement with the existing `ui.tsx` components (`Card`, `Pill`, `Stat`, `Collapsible`, `money`,
  `pct`).
- [ ] **Step 4:** Run the full Playwright suite. Expected: all pass. Commit.

### Task 10: Docs, full verification, go live

**Files:**
- Modify: `docs/rule-engine.md` (the four accounts section, the expectations table from Task 1, how to resume)
- Modify: `progress.md`
- Modify: `scripts/data_supervisor.py` (comment only; the cycle already calls `rule_live.cycle`)

- [ ] **Step 1:** Write the docs. Run all three suites. Expected: green. Commit.
- [ ] **Step 2:** Restart services detached:
  `Start-Process node 'scripts/start.cjs','--restart-services'`.
  Check `.verification/services/data-supervisor.log` shows rule cycles without `blocked`, and
  `GET /api/v1/rules/accounts` lists four `RUNNING` accounts with equity 10000.
