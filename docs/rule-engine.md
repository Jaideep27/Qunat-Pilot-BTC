# Rule engine: event-triggered playbooks (results and how to use it)

Branch `rule-engine` (from `trading-agent`). Design: `docs/design/specs/2026-10-21-rule-engine-design.md`. Pre-registration: amendments A6 and A7 in `docs/research/preregistration.md`. Paper only: nothing places or sizes real orders.

## What it does
- **Triggers**, the only moments the engine looks at all:
  - anomalies: DUMP and SPIKE (a 15-minute move more than 4× its usual size, with a volume surge), open-interest shocks, liquidation bursts and funding extremes;
  - technical events: level sweeps, breaks, holds and rejections, squeeze releases, trend pullbacks and 80%-range edges;
  - macro releases and classified high-impact news.
- **Rulebook** `rulebook/v2.yaml`: 18 executable playbooks (PB01–PB12, long and short variants) plus 2 shadow-only news playbooks (PB13). They are built from the owner's R01–R18 and SR-01..SR-06. Each playbook defines its trigger window, regimes, conditions, stop, target, aborts, time stop, break-even and flip rule.
  - **Gates:** cascade halt (owner R18), macro blackout, funding window, 2-loss cooldown, thin order book, account halts.
  - **Excel copy:** `rulebook/QuantPilot-Rulebook-v2.xlsx`, generated with `quantpilot rulebook-xlsx`.
- **Layers** after a playbook confirms. Each layer is shadow unless its registered test makes it live:
  - ML filter (M3);
  - news veto (M4);
  - learner (M5 bandit);
  - trade-management RL (M5);
  - beside each setup, **what happened the last times it appeared** (direction after 1h and 4h, the simulated plan's win rate and mean R), from `quantpilot rule-history`. By the owner's decision (2026-10-21), the XGBoost direction model plays no part in the rule engine; it still runs only the frozen Paper account.
- **Experimental paper account** (`futures-v1`, $10,000, 0.25% risk per trade): a supervisor thread runs one cycle per minute on 1m candles and decides only at fresh 15m closes. Every trigger, hit, skip and trade goes into a journal in PostgreSQL. Trades and skipped setups get the owner's diagnosis codes (FAIL_STOP_HUNT, PASS_CLEAN, …) four hours after their window ends.
- **Dashboard:** a new **Rules** page with the account, live checklist, playbooks with verdicts, journal and layer modes. The Chart page gains a **Rule levels** toggle (support/resistance, VWAP ±2σ, 4h 80% band, trade markers). The API routes are `/api/v1/rules`, `/rules/journal`, `/rules/research` and `/rules/levels`; the MCP tool is `get_rule_engine`.

## Results on development data (2021-10-20 → 2025-10-19; holdout untouched)

All numbers are net of fees (5 bps per side), slippage (3 bps, or 9 bps in the hour after a spike or dump) and funding.

| Run | Rulebook | Confirmed setups | Trades taken | Combined account | Passing playbooks |
|---|---|---|---|---|---|
| A6 | v1 (`3ea0f47e52c1`), nearest-level target | 7,572 | 106 | −1.77% (50 trades, max DD 2.8%) | none |
| A7 | v2 (`db3d684ff71c`), nearest level that clears 1.5 R:R | 7,572 | 376 | **−10.1%** (180 trades, max DD 10.7%) | none |

- **v1:** 97% of confirmed setups were rejected, because the nearest level sat inside a 1–2 ATR stop (median reward:risk about 0.3).
- **v2:** this fixed the target choice. The rest of the rejections come from your 80% range gate: the target does not fit inside the 4h band.
- **The v2 trades lose:**
  - win rate 31.7% against a 45.4% break-even;
  - mean −0.35R per tradeable setup;
  - every regime is negative (RANGE −0.44R, HIGH_VOL −0.41R, TREND_DOWN −0.10R, TREND_UP −0.17R).
- **Largest samples:**
  - PB09L sweep-and-reclaim: 72 trades, −0.53% each;
  - PB09S: 66 trades, −0.26%;
  - PB02 dump continuation: 30 trades, −0.19%.
- **Never fired:** PB05/PB06 (funding squeezes), PB07 (squeeze breakout), PB11 (absorption) and PB12 (post-release breakout). Their conditions are too strict, or the plan never fits the band.
- **ML filter (M3):** no model could be trained. No playbook or family had the 200 earlier setups the rule requires, so the filter keeps every hit and stays in **shadow**.
- **Learner (M5):** it keeps learning from the live journal (each diagnosed setup updates its statistics). Walk-forward, it learned to **skip almost everything** (233 of 234 test setups). Skipping beats always-take by +0.37R per setup (95% CI 0.23–0.49). It made only 1 trade, under the registered 30, so it stays in shadow. In plain terms, the data say these setups should not be traded.
- **Trade-management RL (M5):** both agents were trained on 175 trades (2021-10 → 2023-10) and tested on 201 (2023-10 → 2025-10). Tabular Q-learning scored −0.23R and PPO −0.24R per trade, against −0.35R for the fixed break-even rule. That is about +0.11R better, but the 95% interval includes zero (−0.02 to +0.26 and −0.03 to +0.25), so both stay in **shadow**. The live engine records the tabular agent's suggestion for each open trade. Managing a trade better cannot turn a losing entry into a winning one.
- **News layer (M4):** forward-only, since no point-in-time news archive exists. It stays in shadow until the forward record can be scored.

## Commands
```
uv sync --locked --extra dev --extra rl          # rl = gymnasium, stable-baselines3, torch (CPU)
quantpilot research-playbooks --output DIR [--prior trials.jsonl]   # A6/A7 harness (resumable per year)
quantpilot research-ml --output DIR              # M3 gate, writes meta-report.json and modes.json
quantpilot research-learner --output DIR         # M5 bandit gate
quantpilot research-manage --output DIR          # M5 tabular Q + PPO gate (needs about 1 GB free RAM)
quantpilot rulebook-xlsx                         # Excel copy of the current rulebook
quantpilot research-accounts --output DIR        # A8: fit the accounts' score model, cutoffs and expectations
quantpilot model-reference --output DIR          # A9: reference statistics for model health
quantpilot train-challenger --output DIR         # A9: shadow challenger (needs 200 new live setups)
```
Crawl4AI lives in its own environment: `uv venv .venv-crawl` then `uv pip install --python .venv-crawl/Scripts/python.exe crawl4ai==0.7.4`. It runs in HTTP mode, without a browser.

## Four paper accounts (amendment A8, 2026-10-22)
At the owner's request, four extra paper futures accounts ($10,000 each) trade the same rulebook v2 setups live.
- **What they share:** playbooks, triggers, checklists, stops, targets and management.
- **What differs:** which setups they take and how much they risk.
- Config: `rulebook/accounts-v1.yaml`. Design: `docs/design/specs/2026-10-22-four-accounts-design.md`.

**How a setup is scored.**
- Every confirmed setup gets `p`, its chance of a net profit, from one frozen L2 logistic regression on the M3 feature list. Training: 7,071 development setups, model hash `7996f4bd6a09`.
- Its **score** is `p·RR − (1 − p)`: its expected result in R.
- Each account takes setups whose score is at or above a fixed percentile of the 2021–2025 walk-forward scores.

| Account | Takes | Leverage | Risk per trade | Daily brake | History (walk-forward, 2022–2025): trades/week · win rate · R per trade · worst drawdown |
|---|---|---|---|---|---|
| A Careful | best 10% (score ≥ +0.21) | 2× | 1% (0.5% below the 95th pct) | 3% | 1.4 · 49% · −0.11 · −17% |
| B Steady | best 30% (≥ −0.07) | 3× | 1.5% | 4.5% | 3.6 · 48% · −0.12 · −54% |
| C Bold | best 40% (≥ −0.15) | 4× | 2% | 6% | 4.5 · 46% · −0.15 · −76% |
| D Aggressive | best 50% (≥ −0.23) | 5× | 2.5% | 7.5% | 5.3 · 46% · −0.15 · −88% |

**What the history says.**
- The score does rank setups: in the walk-forward test, the top fifth won 51% and the bottom fifth 33%.
- But every tier still lost on average, so these accounts collect data; none is expected to make money.
- The drawdowns above cover three years of trading. One 30-trade round is expected to cost about 3% (A) to 11% (D).

**Rules every account follows.**
- One position at a time and no flips.
- The stop must sit inside the liquidation price.
- The rulebook's gates apply: macro blackout, cascade halt, funding window and the per-playbook cooldown.
- A daily brake, and a floor at $5,000.
- **Each account pauses after 30 trades.** It resumes only when the owner presses *Resume* on the Rules page (loopback-only `POST /api/v1/rules/accounts/{id}/resume`).

**What is recorded (PostgreSQL).**
- `account_decisions`: one row per account per confirmed setup. It holds the decision, score, win chance and cutoff, every checklist item ticked or not, the live-only watch items, the sizing, and a market snapshot. The snapshot covers moves over 1h, 4h and 24h, volume, long/short ratios, funding, OI, taker share, book imbalance, spread, liquidations, Fear & Greed, the latest news and the next macro release.
- `account_trades`: every closed trade with its round and number. Four hours after the exit it gets a diagnosis code, then a short Claude-written "why it won or lost" (subscription login, sandboxed).
- `account_reviews`: one per finished round. It holds:
  - the numbers;
  - the facts that separated winners from losers;
  - what the bandit learner would have skipped;
  - a replay of each trade under the trade-management RL policy, the break-even rule and plain holding;
  - a Claude summary with at most three suggestions.

  Suggestions are never applied automatically; a change means `accounts-v2` and a new amendment.

**Report card.** Each account card and each round review shows plain measures:
- return against simply holding BTC since the account started (or over the round);
- win rate and profit factor (total won ÷ total lost);
- average win and loss;
- worst drop and the longest time below a high;
- the longest losing streak.

There is no Sharpe ratio: over a few dozen trades it mostly measures noise.

**No look-ahead, end to end.** `tests/test_lookahead.py` runs the whole live chain twice: once on real data and once with every bar after a cut-off replaced by an unrelated path. The chain covers patterns, triggers, checklists, plans, the reference account, and the four accounts' scores, decisions and trades. Everything up to the cut-off must be identical. A planted leak (a feature read three bars ahead) is caught by the same check, which proves the check works. The idea comes from the look-ahead grader in github.com/Adityak1507/BacktestingEngine.

**Model health (amendment A9).** The Rules page has a **Model health** panel; the API route is `GET /api/v1/rules/model-health`. It checks four things about the score model:
- **Is it still right?** Predicted win chance against the real outcome of every scored setup, judged from 30 results.
- **Has the market changed?** How unusual the latest 50 setups' inputs look, compared with ordinary 50-setup stretches of 2021–2025.
- **Is the data clean?** Before scoring, a setup with an impossible value or a missing must-have input is held back (`DATA_CHECK_FAILED`). On past data this rule would have held back about 2% of setups. The first check found that the premium input was missing on every live setup, because the mark and index prices arrived late. The cycle now refreshes them before deciding.
- **Model record card:** what the model is and how it tested.

**Challenger.** `quantpilot train-challenger` can retrain the model once 200 new live setups have results. The challenger only runs in practice mode next to the live model and is never swapped in automatically.

The reference statistics come from `quantpilot model-reference --output artifacts/rule-engine/research-v2-20261021`. The overall status (OK, Watch, Alarm) is also in the `get_rule_engine` MCP summary, in the overview strip and in the feed status `rules:model-health`.

**Where it runs.**
- The minute cycle (`rule_live.cycle` → `account_live.step_all`) trades and diagnoses.
- A separate supervisor thread (`account_reviews`, every 5 minutes) writes explanations and reviews.
- A restart resumes every account from the database. Missed minutes are stepped on real 1m bars, and trade numbers continue.

## Status
The live engine has been running in the experimental paper account since 2026-10-21 13:23 UTC. The four A8 accounts were added on 2026-10-22. The Python suite passes (470 tests) and so does the browser suite (42 passed, 2 skipped; run with `--workers=2` when memory is tight).

## What this means
The playbooks, as written, lose money after costs on four years of data. The learner's verdict ("skip") and the ML filter's lack of data agree with that. The engine is still useful as a tool:
- it acts on your rules live in a separate paper account;
- it records why each setup was taken or skipped;
- it diagnoses every outcome.

Any change to a playbook needs a new dated amendment and counts as new trials. The forward record (journal plus experimental account) is the honest test from here.
