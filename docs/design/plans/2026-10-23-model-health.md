# Model Health (MLOps) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watch the live A8 score model. Live calibration, drift, a data check that holds back setups with bad inputs, a model card and a shadow challenger, all visible on the dashboard. Also fix the missing premium input.

**Architecture:**
- New module `src/quantpilot/model_health.py`: pure functions plus the reference builder and the challenger.
- `account_live` records each setup's model inputs, applies the data check and scores the challenger in shadow.
- `rule_live` refreshes mark and index prices before a decision.
- `services`, `api`, `mcp_server` and the supervisor serve the health status; `Accounts.tsx` shows it.

**Tech Stack:** Python 3 (pandas, numpy, scikit-learn, joblib, SQLAlchemy), FastAPI, React + Playwright.

**Spec:** `docs/design/specs/2026-10-23-model-health-design.md`

## Global Constraints
- Paper only. Nothing places real orders.
- Accounts never change their own config. No automatic retraining or swapping of the model.
- The holdout 2025-10-20 → 2026-10-19 stays untouched. The challenger's live rows are setups after 2026-10-20 only.
- A monitor failure never stops trading. Only `DATA_CHECK_FAILED` holds a setup back.
- RAM is tight: run targeted tests per task and the full suites once at the end.
- Python: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. Browser: `cd frontend && npx playwright test --reporter=line --workers=2`.
- Commits end with the two attribution lines (Co-Authored-By / Claude-Session).

## Review Focus
1. Decision rows from scorers without `inputs` (old stub scorers, older rows) must not crash the check or monitoring. The check is skipped and noted.
2. A reference whose hash differs from the live model must be ignored, never applied.
3. NaN inputs in PSI need a missing bin of their own; otherwise an input that is always missing live (premium) is invisible to drift.
4. The mark/index fetch failing (network) must not block the cycle. The decision goes ahead and the data check records it.
5. Calibration must count each setup once, although four accounts each record a decision row for it.

---

### Task 1: model_health core and reference

**Files:** Create `src/quantpilot/model_health.py`, `tests/test_model_health.py`. Modify `src/quantpilot/cli.py` (command `model-reference`).

**Interfaces (Produces):**
- `reference_path() -> Path` → `artifacts/rule-engine/accounts-score-reference.json`
- `build_reference(train: DataFrame, model: dict, wf: DataFrame, window=50, step=10) -> dict`. Keys:
  - `model_hash`;
  - `inputs` `{col: {present, min, max, edges[4], shares[6]}}`, where the shares are 5 quintile bins plus a missing bin;
  - `critical` (list of cols with present ≥ 0.99);
  - `blocked_share`;
  - `calibration`, `brier`;
  - `drift_baseline` `{thresholds: {col: p95 psi}, counts_p90, counts_p99, windows}`.
- `load_reference(model_hash) -> dict|None`: None when the file is missing or the hash differs.
- `psi(values: Series, spec: dict) -> float`: 6 bins, with 1e-4 added to every share.
- `data_check(x: dict, ref) -> list[str]`: failure reasons, e.g. `'inf:f_x'`, `'range:f_x'`, `'missing:f_premium'`; an empty list means pass.
- `wilson(k, n, z=1.96) -> (lo, hi)`
- `calibration(pairs: DataFrame[p, won], ref) -> dict`: n, predicted, actual, lo, hi, brier, dev_brier, bins[3], verdict.
- `drift(rows: DataFrame (model columns), ref) -> dict`: n, count, verdict, top[5] {input, label, psi, live_median, train_median}.
- `run_reference(output) -> dict`: loads the A8 run, `account_score.training_set` / `walk_forward` / `load_model`, then writes the file.

- [ ] **Step 1: Write failing tests** in `tests/test_model_health.py`, on synthetic frames (300 rows, columns `a`, `b`; `b` 50% missing):
  - `test_psi_zero_for_same_distribution_and_large_for_shift`: psi of the training sample < 0.02; psi of a sample shifted by 3 std > 1.
  - `test_psi_counts_an_input_that_is_always_missing`: an all-NaN live column for an input 100% present in training → psi > 1.
  - `test_reference_marks_critical_inputs_and_blocked_share`: `a` is critical, `b` is not; blocked_share == the share of rows with `a` missing.
  - `test_data_check_flags_inf_out_of_range_and_missing_critical`: three cases give reasons starting with `inf:`, `range:` and `missing:`; a training row passes (`[]`).
  - `test_load_reference_ignores_another_models_file`: written with hash `x`; `load_reference('y')` is None.
  - `test_calibration_verdicts`: n = 20 → `TOO_FEW`; 100 setups with p = 0.7 and 30 wins → `OVERCONFIDENT`; p = 0.3 and 30 wins → `IN_LINE`.
  - `test_drift_stable_on_training_like_rows_and_alarm_on_shifted`: 50 training rows → `STABLE` or `WATCH`; 50 rows with both inputs shifted by 5 std → `ALARM`; 10 rows → `TOO_FEW`.
- [ ] **Step 2: Run** `pytest tests/test_model_health.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement.**
  - The drift baseline uses development setups in time order: windows `[i, i+window)` for i in `range(0, n-window+1, step)`.
  - Per-input threshold = p95 of that input's psi across windows.
  - Count = the number of inputs above their own threshold.
  - `counts_p90` / `counts_p99` = percentiles of the window counts.
  - Verdicts as in the spec. Input labels: `f_x` → `x` with underscores replaced by spaces, and `tf_` → "trigger: …".
  - `brier` is from `wf` (`p`, `o_net > 0`).
  - CLI: `model-reference --output DIR` prints the summary.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** with the message "Model health core: reference, data check, PSI drift, calibration".

### Task 2: live accounts record inputs, apply the data check, score the challenger

**Files:** Modify `src/quantpilot/account_live.py`, `tests/test_account_live.py`.

**Interfaces:**
- Consumes `model_health.load_reference`, `data_check`, `load_challenger`, which Task 4 defines. In this task, add `load_challenger() -> dict|None` (returns None until Task 4 writes files).
- `default_scorer()` result dict gains `inputs` (the `live_row` record as a plain dict with floats/None and side/regime strings) and `challenger` ({hash, p, score} or None).
- Decision status `DATA_CHECK_FAILED`, applied after `PAUSED` and before `BELOW_CUTOFF`. The payload gains `inputs`, `data_check` (a list, or `'no reference'` / `'no inputs'`) and `challenger`.

- [ ] **Step 1: Failing tests** in `tests/test_account_live.py`:
  - `test_bad_inputs_hold_the_setup_back`: a scorer stub that returns `inputs` with `f_x = inf`, plus a monkeypatched `model_health.load_reference` returning a reference with `f_x` → every decision is `DATA_CHECK_FAILED`, `data_check` contains `'inf:f_x'`, and no position opens.
  - `test_no_reference_skips_the_check`: the stub with inputs and a None reference → decisions as before (`TAKEN` present) and `data_check == 'no reference'`.
  - `test_default_scorer_returns_inputs`: with the real model file present (skip if missing), the `inputs` keys ⊇ `{'f_premium', 'side', 'regime'}`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** `step_all` loads the reference once per cycle with `load_reference(scorer.hash)`, and only when the scorer has a hash. A stub scorer (no hash) gives `ref=None`, which means `'no reference'` (`'no inputs'` when the scorer gives none). Wrap reference loading in try/except → None.
- [ ] **Step 4: Run** `tests/test_account_live.py tests/test_lookahead.py -k "not slow"` (the look-ahead test takes ~3 min; run it once here). Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 3: fresh mark and index prices at decision time

**Files:** Modify `src/quantpilot/rule_live.py`, `tests/test_rule_live.py`.

**Interfaces:**
- `fetch_marks(engine, client, now) -> int`: upserts the 5m mark and index klines of the last hour as `MarketObservation` rows: family `mark`/`index`, values `{mark_price}`/`{index_price}`, `available_at = open + 5 min`, complete bars only, `source` = the endpoint URL. Index uses the `pair` param.
- The `_market` cache key becomes (max candle `available_at`, max mark `available_at`, max index `available_at`).
- In `cycle`: when `client` is not None, a fresh 15m close is pending, and the latest stored mark or index `available_at` is earlier than the latest candle `available_at`, call `fetch_marks` inside try/except (errors go into the summary as `marks_error`).

- [ ] **Step 1: Failing tests:**
  - `test_fetch_marks_upserts_mark_and_index` with a fake client returning two klines each: rows stored and `available_at` = open + 5 min; an incomplete bar is skipped.
  - `test_market_cache_reloads_when_marks_arrive`: after inserting a newer mark row, `_market` returns frames containing it.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**, reusing `market_store._upsert` as `fetch_fast` does.
- [ ] **Step 4: Run** `tests/test_rule_live.py tests/test_account_live.py`. Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 4: challenger

**Files:** Modify `model_health.py`, `cli.py`, `tests/test_model_health.py`.

**Interfaces:**
- `challenger_paths() -> (joblib, json)` under `artifacts/rule-engine/`.
- `live_training_rows(engine) -> DataFrame`: one row per hit with stored `inputs`, hit time ≥ 2026-10-20, a resolved valid outcome, and hit reasons ⊆ `account_score.ALLOWED`. Columns: the inputs plus `o_net` and `o_exit_time`.
- `train_challenger(dev_train, live_rows, min_new=200, now) -> dict`: raises `ValueError('not enough new setups: N of 200')` below the minimum. Fits `account_score.fit(concat)`, writes both files, and returns the meta {hash, n_dev, n_live, created}.
- `load_challenger() -> dict|None`
- `compare(rows: DataFrame[p, cp, won], reps=2000, seed=0) -> dict`: n, brier, challenger_brier, diff_lo, diff_hi, verdict.
- CLI `train-challenger --output DIR [--min-new 200]`.

- [ ] **Step 1: Failing tests:**
  - `test_train_challenger_refuses_without_enough_new_setups`
  - `test_train_challenger_writes_files_and_scores` (min_new = 5 on synthetic data)
  - `test_compare_verdicts`: n = 10 → `TOO_FEW`; a challenger that is perfect on 100 rows → `CHALLENGER_BETTER`; an identical challenger → `NO_DIFFERENCE`.
- [ ] **Steps 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit.**

### Task 5: health service, API, MCP, supervisor feed

**Files:** Modify `services.py`, `api.py`, `mcp_server.py`, `scripts/data_supervisor.py`, `model_health.py`, `tests/test_model_health.py`, `tests/test_api*.py` (the existing rules API test file).

**Interfaces:**
- `model_health.status(engine, now) -> dict`:
  - `card`: model hash, trained_until, training_setups, features, walk-forward calibration, live_since, config hash and match;
  - `calibration`;
  - `drift`: the last 50 setups with inputs;
  - `data_checks`: {checked_7d, failed_7d, failed_24h, latest};
  - `challenger`;
  - `reference`: 'ok' / 'missing';
  - `overall` and `reasons`: plain-English strings.
- `services.rules_model_health()` → envelope.
- `GET /api/v1/rules/model-health`.
- The MCP `get_rule_engine` summary gains `model_health: {overall, reasons}`.
- The supervisor's `account_reviews` loop writes the feed status `rules:model-health`: `{status: healthy|degraded, overall, reasons}`.

- [ ] **Step 1: Failing tests:**
  - `test_status_overall_rules` (pure `overall(cal, drift, checks, ref)`, covering each ALARM/WATCH/OK case);
  - `test_status_on_journal` (sqlite engine: decisions with inputs plus hits with outcomes → n counted once per hit);
  - an API test for `/api/v1/rules/model-health`, returning 200 with `overall`.
- [ ] **Steps 2–4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit.**

### Task 6: dashboard

**Files:** Modify `frontend/src/pages/Accounts.tsx`, `frontend/tests/accounts.spec.ts`.

- **Model health section:** a `region` labelled "Model health" under the account cards. It has a status chip (OK / Watch / Alarm) and the reasons, then five Collapsibles:
  - "Is it still right?": the calibration numbers;
  - "Has the market changed?": the drift verdict and top inputs;
  - "Is the data clean?": the 7-day counts and the latest failure;
  - "Model record card";
  - "Challenger (needs your OK to swap)".
- **AccountsStrip:** a chip "Model: OK/Watch/Alarm".

- [ ] **Step 1: Failing test** `model health shows status, calibration and data checks`: the fixture returns `model-health` data with overall `WATCH`; assert the region contains "Watch", "Is it still right?" and the reasons text, and the strip shows "Model: Watch".
- [ ] **Steps 2–4:** run (FAIL), implement, run `npx playwright test accounts.spec.ts --workers=2` (PASS).
- [ ] **Step 5: Commit.**

### Task 7: amendment, docs, reference artifact

**Files:** `docs/research/preregistration.md` (A9), `docs/rule-engine.md`, `progress.md`, the reference artifact (git-ignored artifacts as per repo practice).

- [ ] Append A9 (dated 2026-10-23) with the data-check, calibration, drift, challenger and no-auto-promotion rules, before deployment.
- [ ] Run `quantpilot model-reference --output artifacts/rule-engine/research-v2-20261021`. Expected: `blocked_share ≈ 0.021`, and the hash `7996f4bd6a09` matches.
- [ ] Update the docs. Run the full Python and browser suites, plus `node scripts/check-structure.cjs`.
- [ ] Commit. Ask the owner before restarting the services.
