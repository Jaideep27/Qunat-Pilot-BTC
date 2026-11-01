# Combined Bot — design (A11)

**Date:** 2026-11-01. **Rules:** amendment A11 in `docs/research/preregistration.md`. **Results:** `docs/research/style-research.md`.

## Why

The owner wants one final bot:
- every strategy is a member and has a say;
- it holds a single trade at a time;
- it sits beside the four A10 style accounts, which keep trading on their own.

**The owner chose** "boss + second opinion" and parking idle money in the funding hedge.

## Decisions

- **Members:** Range Trader and Breakout Hunter signals, the Trend Rider's daily entry, and confirmed rulebook setups with valid plans. Live, these are statuses TAKEN, SKIPPED_LOWER_PRIORITY and POSITION_OPEN; in history, `tradeable` hits.
- **Who leads:** the regime (`patterns.regime`) decides; other candidates can only confirm.
- **Veto:** the daily trend vote blocks counter-trend trades.
- **Second opinion:** a different source within the hour, or price within 0.5 × ATR(1h) of a confirmed 1h/4h support or resistance. The levels come from `features.context`, added to the style frame and tested equal to it.
- **Choice:** most agreements, then the leader order, then reward:risk.
- **One book** (`strategies/bot.py`): a futures-v1 ledger plus an optional carry-v1 hedge.
  - The hedge is sized from futures equity, and its result is handed back when it closes, so the brake and the floor watch one number.
  - The live account and the history test run this same code.
- **Recorded for every decision:** regime, vote, leaders, each candidate's status and agreements, and the Time Machine for entries.
- **Hedge cycles** are journaled as trades (number 0). They are not counted toward the 30-trade review.
- **Playbook management is not applied:** a playbook-led trade keeps only its stop, target and time stop, without break-even moves or aborts.
