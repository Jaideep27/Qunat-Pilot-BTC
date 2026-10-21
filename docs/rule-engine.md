# Rule engine: event-triggered playbooks (results and how to use it)

Branch `rule-engine` (from `trading-agent`). Design: `docs/superpowers/specs/2026-10-21-rule-engine-design.md`. Pre-registration: amendments A6 and A7 in `docs/research/preregistration.md`. Paper only: nothing places or sizes real orders.

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
  - a record of the research forecaster's calls (always shadow).
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
- **Learner (M5):** walk-forward, it learned to **skip almost everything** (233 of 234 test setups). Skipping beats always-take by +0.37R per setup (95% CI 0.23–0.49). It made only 1 trade, under the registered 30, so it stays in shadow. In plain terms, the data say these setups should not be traded.
- **Trade-management RL (M5):** not finished. Windows stopped the run for low memory after the tabular agent and a 50,000-step PPO checkpoint, before the comparison was written. Re-run with `quantpilot research-manage --output artifacts/rule-engine/research-v2-20261021` when memory allows.
- **News layer (M4):** forward-only, since no point-in-time news archive exists. It stays in shadow until the forward record can be scored.

## Commands
```
uv sync --locked --extra dev --extra rl          # rl = gymnasium, stable-baselines3, torch (CPU)
quantpilot research-playbooks --output DIR [--prior trials.jsonl]   # A6/A7 harness (resumable per year)
quantpilot research-ml --output DIR              # M3 gate, writes meta-report.json and modes.json
quantpilot research-learner --output DIR         # M5 bandit gate
quantpilot research-manage --output DIR          # M5 tabular Q + PPO gate (needs about 1 GB free RAM)
quantpilot rulebook-xlsx                         # Excel copy of the current rulebook
```
Crawl4AI lives in its own environment: `uv venv .venv-crawl` then `uv pip install --python .venv-crawl/Scripts/python.exe crawl4ai==0.7.4`. It runs in HTTP mode, without a browser.

## What this means
The playbooks, as written, lose money after costs on four years of data. The learner's verdict ("skip") and the ML filter's lack of data agree with that. The engine is still useful as a tool:
- it acts on your rules live in a separate paper account;
- it records why each setup was taken or skipped;
- it diagnoses every outcome.

Any change to a playbook needs a new dated amendment and counts as new trials. The forward record (journal plus experimental account) is the honest test from here.
