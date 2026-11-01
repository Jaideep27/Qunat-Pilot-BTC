# Four paper accounts, four styles (accounts-v2) — design

**Date:** 2026-11-01. **Pre-registration:** amendment A10 in `docs/research/preregistration.md`, which holds the exact rules. **Results:** `docs/research/style-research.md`.

## Why

- The A8 accounts traded the same rulebook setups and differed only in score cutoff and size, so they took the same trades at the same minute.
- The owner asked for four different, "dynamic" strategies, researched from:
  - the day-trader-skill repo;
  - the owner's NotebookLM sources: Berdondini, Dr Tan's Bitcoin paper, Zerodha algos, TradingView scripts and price-action videos;
  - general knowledge.
- The owner chose:
  - replace the old accounts;
  - make the fourth a funding collector;
  - go live on paper at once, with the history test in parallel.

## Decisions

- **"Dynamic" means regime switching and volatility sizing under fixed rules.** Accounts never change their own rules (the owner's standing rule).
- **Each style trades a different market condition:**
  - Trend Rider: daily trend agreement;
  - Range Trader: the RANGE regime;
  - Breakout Hunter: after a squeeze;
  - Funding Collector: price-neutral.
- **Risk is identical everywhere** (1% per trade, 2× cap), so the styles are what differ. This follows Berdondini: risk management sets size and never the strategy.
- **Parameters are source defaults with no search.** Each style is one trial.
- **futures-v1 gains optional fields:** resting limit entries, `max_hold_hours` and stop-only trades. Old plans are unchanged.
- **The hedge has its own ledger** (`carry-v1`), whose closed cycles have the futures trade shape, so the journal, reviews and report work unchanged.
- **Live and history use the same code** (style frame, signals, sizing). The style frame is tested equal to the rule engine's pattern frame.
- **The Time Machine is recorded only.** It is the repo's idea, scaled to BTC: same time-of-day bootstrap over 60 days, plus the style's history-test trades.
- **Cascade halt omitted.** It depends on the playbook trigger table, so the styles apply the macro blackout but not the cascade halt, both live and in the history test.

## Rollback

Point `accounts.config.CONFIG` back at `accounts-v1.yaml`. The live cycle then runs the A8 fan-out again; the retired accounts would need their status reset by hand.
