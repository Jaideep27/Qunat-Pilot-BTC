# Modular refactor before publishing — design

Date: 2026-10-24. The owner approved this in chat: "yes it will be a public repo and yeah continue with the refactoring".

## Why
The owner wants to put QuantPilot BTC on their resume and push it to a **public** GitHub repo. Today the code reads as one flat folder:
- 70 Python modules in `src/quantpilot/`;
- 70 test files in `tests/`;
- 30 mixed scripts in `scripts/`;
- 19 files in `frontend/src/`.

A reviewer should be able to see the architecture from the folder tree alone.

## Goals
- **Python.** Group the 70 modules into subpackages by responsibility, using absolute imports. `config.py` and `cli.py` stay at the top.
- **Tests, scripts, frontend.** Tests mirror the package layout. Scripts are grouped by purpose. The frontend gets `components/`, `lib/`, `styles/` and `app/`.
- **Docs.** Grouped by topic. A new public README covers what it is, the architecture diagram, honest results, how to run it, and the safety rules.
- **Safe to publish.** Nothing secret or private can be pushed, and no scheduled GitHub workflow runs against a database that isn't there.
- **Merge.** The work lands on `main`, so the owner pushes one branch.

## Non-goals
- No change in behaviour:
  - same decisions, numbers and API responses;
  - same database schema, and no migrations;
  - same model files.
- No rewriting of module internals beyond what the move needs: imports, and paths that used `parents[2]`.
- No new features, and no dependency changes.

## Anti-goals (a failure even if the tree looks nice)
- The live paper accounts, data collection, watchdog or backups break, or silently stop.
- A test that passed before fails after, or is deleted to make the suite pass.
- A secret, the `.env` file, the data or the backups reach GitHub.

## Constraints
- The live services run from `C:\Projects\BTC Quant` on branch `rule-engine`, and the watchdog runs `scripts/watchdog.py` every 5 minutes. All work therefore happens in a separate git worktree. The live tree changes only at the switch-over.
- The switch-over needs one service restart, which needs the owner's OK. The order is:
  1. remove the scheduled tasks;
  2. check out `main`;
  3. reinstall the package;
  4. restart the services;
  5. reinstall the tasks.
- RAM is tight. Run the tests in batches.

## Package map (my call)

| Package | Modules (old → new) |
|---|---|
| `storage` | store→database, market_store→market, recovery, snapshots |
| `data` | data→archives, live, history, collector, forward_data→forward, repair, research_data→panels, macro |
| `features` | features→core, context, patterns, charting |
| `simulation` | engine, futures, benchmarks, tradeplan, outcomes, worker |
| `research` | research→walkforward, replay, event_research→events, event_study, selective, signals, strategy_research→strategies, agent_research→agent, playbook_research→playbooks, setup_history, forecaster |
| `rules` | rules→rulebook, triggers, arbiter, diagnosis, journal, rule_live→live, layers, meta, learner, rl_manage, rulebook_export→export |
| `accounts` | accounts→config, account_live→live, account_score→score, account_journal→journal, account_review→review, account_report→report |
| `monitoring` | model_health, ops, operations, diagnostics, process |
| `ai` | news, news_events, chat, ask_ai→ask, mcp_proxy |
| `outlook` | outlook→engine, answers, analogs, questions |
| `api` | api→app, services, mcp_server |

**Import rule:** every import site that used an old module name keeps that name as an alias, for example `from quantpilot.rules import live as rule_live`. This keeps function bodies byte-for-byte the same, apart from the import lines.

**Known side effect:** the shadow forecaster hashes its own source. Its import lines change, so it retrains once after the switch, as it does after any edit to that file.

## Publishing safety (my calls)
- **History scan, 2026-10-24.** Every blob in all 99 commits was checked against the `.env` secrets (the DB passwords and the Gmail app password), API-key patterns and the phone number. No secrets were found. The only hit for the owner's Windows user path is in `progress.md`.
- The `.gitignore` keeps `.env`, `data/`, `artifacts/` and `.verification/` out.
- `paper.yml` loses its 15-minute schedule (manual run only). Otherwise a public fork or the owner's repo would fail every 15 minutes.
- No license is added. That is the owner's choice, and it is flagged to them.

## Done when
- `pytest` passes in full, with the same count as before (503).
- The browser tests pass (43, with the same 2 skips).
- `check-structure.cjs` passes, and the frontend build passes.
- `quantpilot --help` and the API start from the new layout.
- After the switch-over:
  - `/api/v1/ready` is ready, and the engine heartbeat is fresh;
  - the accounts keep deciding;
  - the watchdog task runs and returns 0.
