# Model health (MLOps) for the four paper accounts — design

Date: 2026-10-23. Branch `rule-engine`. Owner approved the five parts in chat ("yes go ahead").

## Why
The only machine-learning model that acts live is the A8 **score model** (`accounts-score.joblib`, hash `7996f4bd6a09`). It gives each confirmed setup its win chance `p`, and the four accounts trade on `score = p·RR − (1−p)`. Today nobody can see whether that model is still right, whether the market has moved away from what it learned, or whether its inputs are clean. The first look already found a real fault: **the premium input (mark ÷ index − 1) has been missing on every live setup**. The mark and index prices arrive in a batch about two minutes after each 5-minute close, the minute cycle decides as soon as the candle is in, and the 10-minute join window then finds only the previous batch, which is 15 minutes old. Training had the premium on 99.5% of setups.

## Goals
1. **Is it still right?** Live calibration: predicted win chance against the real outcome of every scored setup.
2. **Has the market changed?** Drift: how unusual the latest live setups' inputs look compared with the training data, judged against how unusual ordinary stretches of the past looked.
3. **Is the data clean?** A data check before scoring; a setup failing it is held back (`DATA_CHECK_FAILED`). Plus a fix so the premium is present at decision time.
4. **Model record card:** what is live, what it learned from, how it tested.
5. **Challenger with the owner's OK:** a retrained model can run beside the live one in shadow. It is never swapped in automatically.

## Non-goals and anti-goals
- **No automatic retraining or swapping.** Accounts never change their own rules (owner's rule). Promoting a challenger means `accounts-v2` and a new amendment, decided by the owner.
- **The holdout (2025-10-20 → 2026-10-19) stays untouched.** A challenger learns only from development data plus live setups after 2026-10-20.
- No new heavy services: no MLflow server, no extra database. Everything lives in the existing PostgreSQL journal, `artifacts/`, the API and the dashboard. RAM is tight.
- Monitoring must never stop trading because the monitor itself failed. Only the data check can hold a setup back, and only for bad inputs.

## Decisions
**Recorded inputs.** The scorer also returns `inputs`: the setup's raw model row (the `meta._row` columns, before imputation). It is stored in each decision's payload. Drift and the challenger read it. Setups before this change have no inputs; monitoring starts from deployment.

**Reference file.** `quantpilot model-reference` writes `artifacts/rule-engine/accounts-score-reference.json` from the A8 training set (the A8 run `artifacts/rule-engine/research-v2-20261021`) and the frozen model. It holds:
- the model hash;
- per input: present share, min, max, quintile edges and training shares;
- the "critical" inputs (present in ≥ 99% of training rows: 56 of 67);
- the walk-forward calibration bins and Brier score;
- the drift baseline: the same drift statistic computed on rolling 50-setup windows of the development setups, in time order (step 10);
- the share of training setups the data check would hold back (measured: 2.1%).

A reference whose hash differs from the live model is ignored, and the panel says so.

**Data check (the only behaviour change).** Before scoring, a setup is held back as `DATA_CHECK_FAILED` when any of these holds:
- an input is ±∞;
- an input lies more than one full training range beyond the training min or max;
- a critical input is missing.

The check runs after the scorer and paused checks and before the cutoff. The decision payload records the failed inputs. Without a valid reference the check is skipped and the payload notes `data_check: "no reference"`.

**Premium fix.** At a fresh 15m close, if the stored mark or index prices end before the decision time, the cycle fetches the last hour of 5m mark and index klines from Binance and upserts them (the same pattern as `fetch_fast`). The market cache is keyed on the latest candle, mark and index times, so the frames reload. The definitions (10-minute join window) are unchanged.

**Calibration.** Distinct scored setups, each with `p` from its decision row, are joined to the setup's simulated outcome in the reference journal (`outcome.valid`, win = `outcome.net > 0`). This is the same label the model was trained on. Reported:
- n;
- mean predicted vs actual win rate with a Wilson 95% interval;
- Brier score, live vs development;
- three bins: p < 0.35, 0.35–0.50, ≥ 0.50.

Verdicts:

| Verdict | When |
|---|---|
| `TOO_FEW` | n < 30 |
| `OVERCONFIDENT` | the mean prediction is above the interval |
| `UNDERCONFIDENT` | the mean prediction is below the interval |
| `IN_LINE` | otherwise |

**Drift.**
- **Statistic:** over the latest 50 live setups with inputs, PSI for each input against the training quintiles. The statistic is the number of inputs whose PSI exceeds that input's own 95th percentile across the historical windows.
- **Verdict:**

| Verdict | When |
|---|---|
| `TOO_FEW` | fewer than 50 setups |
| `ALARM` | the count is above the 99th percentile of historical windows |
| `WATCH` | the count is above the 90th percentile |
| `STABLE` | otherwise |

- **Shown with it:** the five most-shifted inputs, with plain names and live vs training medians.

**Challenger.**
- **Building it:** `quantpilot train-challenger [--min-new 200]` refits the A8 recipe (same filter, same pipeline) on the development training set plus resolved live setups after 2026-10-20 that have inputs. It refuses with fewer than 200 new setups. It writes `challenger.joblib` and `challenger.json` (hash, counts, created time), and only one challenger exists at a time.
- **Shadow use:** while it exists, every decision payload also records `challenger: {hash, p, score}`. It is never used to decide.
- **Comparison:** champion vs challenger Brier on setups resolved since the challenger was created, with a paired bootstrap 95% interval on the difference.

| Verdict | When |
|---|---|
| `TOO_FEW` | n < 50 |
| `CHALLENGER_BETTER` | the whole interval is below 0 |
| `NO_DIFFERENCE` | otherwise |

**Overall status.**
- `ALARM` if drift is `ALARM`, calibration is `OVERCONFIDENT`, or more than 20% of the last 7 days' setups failed the data check.
- `WATCH` if drift is `WATCH`, the reference is missing, or any setup failed the data check in the last 24h.
- `OK` otherwise.

**Where it shows.**
- `GET /api/v1/rules/model-health` returns the card, calibration, drift, data checks (7-day counts and the latest failure), challenger and overall status.
- A **Model health** section on the Rules page, and a chip on the Overview accounts strip.
- The `get_rule_engine` MCP summary.
- The `account_reviews` supervisor thread (every 5 minutes) writes the feed status `rules:model-health`.

**Pre-registration.** Amendment **A9** (dated 2026-10-23) records the data check, the drift and calibration rules, the challenger rule and the no-auto-promotion rule before deployment.

## Left to the builder
File layout inside the new module, plain-English labels for inputs, and the UI layout within the existing Rules page style.
