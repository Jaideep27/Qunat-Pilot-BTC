# Four paper accounts with confidence tiers: design

Date: 2026-10-22. Branch: `rule-engine`. Status: draft for the owner's approval.

## 1. Why

The live rule engine (rulebook v2, account `experimental-v1`) trades about once a week. In its first 22 hours,
3 setups passed their checklists and all 3 were skipped for reward:risk below 1.5. The owner wants:

- more live trades to learn from;
- strategies that run side by side and differ in how much risk they take;
- a full, permanent record of why each trade was taken and why it won or lost;
- a review after every 30 trades: where it went wrong and what could change.

Everything runs on the live market with paper money. Nothing here places real orders.

## 2. What the owner decided

1. **Four paper accounts, $10,000 each, BTCUSDT perpetual futures**, each with its own strategy, running at the same
   time and never affecting each other. Ordered from lower to higher risk.
2. **Tiers are "the best X% of setups"**, not "X% sure to win". Measured on 2021–2025 development data, no setup's
   estimated win chance ever reached 70% (the maximum was about 66%), so "90% sure" would mean no trades.

   | Account | Takes | Leverage | Risk per trade |
   |---|---|---|---|
   | A, careful | best 10% of setups | 2× | 1% of equity |
   | B | best 30% | 3× | 1.5% |
   | C | best 40% | 4× | 2% |
   | D, aggressive | best 50% | 5× | 2.5% |

3. **No account changes its own rules.** A losing strategy keeps running and is allowed to fail.
4. **Each account pauses after 30 closed trades** and waits for a review with the owner. It resumes only when the
   owner says so.
5. **Every decision is stored in PostgreSQL**:
   - for setups that trade and setups that are skipped;
   - with which checklist items were ticked, why the trade was taken or skipped, how it ended, and why.
6. **A restart continues where it stopped.** If account C had 5 trades, its next trade is number 6.
7. **The UI shows each account's rules and what it watches**, in plain English.
8. **A learning and reasoning block runs at each 30-trade review.** It explains what went wrong and suggests changes.
   It never applies them.
9. **Calculated risk always:**
   - every trade has a stop and a target before it opens;
   - size follows from the stop distance and the risk percentage;
   - leverage is a hard ceiling;
   - the stop must sit inside the liquidation price.

## 3. Goals, non-goals, anti-goals

**Goals**
- Four accounts trading live on paper.
- Account A trades about twice as often as the current strict account (roughly 3 a week). D trades about 10 a week.
  These are estimates; the research step in §9 measures them before go-live.
- A complete, queryable audit trail per account.
- A 30-trade review with numbers and a plain-English explanation.

**Non-goals for this version**
- Real orders.
- New data feeds (Coinbase premium, options DVOL, ETF flows).
- Limit-order entries.
- More than one open position per account.
- Changing rulebook v2's playbooks, triggers or checklists.

**Anti-goals**: this is a failure even if it runs.
- An account silently changes its rules or thresholds.
- A claim that any trade is "90% sure".
- Accounts sharing state, or one account's trade overwriting another's journal row.
- State lost or trade numbering reset after a restart.
- A trade whose reasons can't be reconstructed from the database.
- Touching the holdout year 2025-10-20 → 2026-10-19.

## 4. How a setup becomes a trade (per account)

The existing pipeline runs **once per decision** and is shared by all accounts:

> 1m and 5m candles → patterns → triggers → playbook checklists (rulebook v2) → stop and target levels

For each **confirmed** setup (checklist passed, stop and target built), every account then runs its own steps.

### 4.1 Score
A single **score model**, shared and frozen, gives each confirmed setup:
- **confidence** `p`: the estimated chance the trade ends with a net profit;
- **score**: expected net result in R (multiples of the amount risked), `score = p·RR − (1−p)`, where RR is the plan's
  net reward:risk after costs.

**Model:**
- L2 logistic regression on standardized features, the same family and feature list as `meta.py` (`meta.FEATURES`):
  - market features: long/short ratios (global and top traders), funding, open interest changes, taker share,
    Fear & Greed, volatility, trend and range position;
  - setup facts: trigger facts, confluence, reward:risk, regime.
- XGBoost is deliberately not used in rule-engine decisions (owner's earlier ruling).
- Training data: every confirmed setup in the v2 research hits from 2021-10-20 to 2025-10-19 that has an outcome
  (about 7,500). Label: net result > 0.
- Fit once and freeze. The model file, its hash and its training window are stored with every decision.

### 4.2 Tier cutoffs
- Each account's cutoff is a **fixed score number**: the 90th, 70th, 60th and 50th percentile of the walk-forward
  scores on development data (refit at 6-month blocks, like `meta.py`).
- The cutoffs are written into the account config and pre-registration before go-live, and never move live.
- "Best 10%" therefore means "would have been in the top 10% historically".

### 4.3 Remaining per-account steps
1. **Take or skip.** The setup is eligible if `score ≥ cutoff`.
   - The rulebook's `min_reward_risk` of 1.5 does **not** apply to these accounts; the score replaces it.
   - Structural rejections still apply: invalid levels, stop wider than `max_stop_atr`, stop beyond liquidation,
     exchange minimum, insufficient margin.
2. **Gates.** The existing global gates apply to every account: cascade halt, macro blackout, thin book, funding
   window, and the per-playbook cooldown counted on that account's own trades. The account must also not be paused,
   halted or holding a position.
3. **Choose.** If several setups qualify at once, the one with the **highest score** wins. This replaces
   priority-first ordering for these accounts.
4. **Size.**
   - Risk amount = equity × the account's risk %. A setup whose score is below the account's **full-risk line** uses
     half of that. The full-risk line is the percentile halfway between the account's cutoff and 100%:
     A 95th, B 85th, C 80th, D 75th, resolved to fixed scores alongside the cutoffs. *(My call: gives "bet more when
     more confident" a fixed rule.)*
   - Quantity = risk amount ÷ (stop distance + costs).
   - Notional is capped at equity × the account's leverage.
   - The stop must be inside the liquidation price at that leverage, or the setup is rejected.
5. **Enter and manage:**
   - entry at the next 1m bar's open, as today;
   - stop, target, break-even move, aborts and time stop exactly as the playbook says;
   - market orders: 0.05% fee plus 0.03% slippage per side, 3× slippage in stressed minutes.
   - *(My call: market orders keep costs honest; limit entries can come in a later version.)*
   - These accounts never flip directly from long to short or back. A playbook's `may_flip` is ignored, and a new
     setup waits until the open trade has closed. *(My call: keeps one clear trade at a time and simpler accounting.)*
   - The live features fed to the score model must use the same names and definitions as the research hits
     (`f_*`, `tf_*` columns). A test pins that the live feature row for a setup equals the research row for the same
     timestamp.
6. **Count and pause.** After the 30th closed trade of a round, the account switches to `PAUSED_FOR_REVIEW`:
   - no new entries;
   - the review job (§7) runs;
   - the next round starts only when the owner resumes it.

**Live-only "watch items"** are recorded with every decision as ticked or not, but are **not** in the score, because
they have no history to fit on:
- order-book imbalance within 5 and 15 bps;
- spread;
- liquidations in the last 15 minutes against the trade's side;
- the latest classified news item and its stance;
- time to the next macro release.

The 30-trade review checks whether they separated winners from losers.

## 5. Risk limits (safety brakes)

`futures.py` today halts for the day at a 2% daily loss and **permanently at a 10% drawdown**. With 1–2.5% risk per
trade, that would halt account D on its first loss of the day and could end it before trade 30.

The brakes become per-account settings. **The defaults below need the owner's confirmation.**

| Account | Daily brake | Floor (permanent stop before review) |
|---|---|---|
| A | 3% | equity < $5,000 |
| B | 4.5% | equity < $5,000 |
| C | 6% | equity < $5,000 |
| D | 7.5% | equity < $5,000 |

- The daily brake is 3× risk per trade: closes the position and blocks entries until the next UTC day.
- The floor is a 50% loss: pauses the account for review early, with the reason recorded.

The existing `experimental-v1` account keeps its current limits.

## 6. Storage (PostgreSQL, market database)

**Accounts**
- Each account is a row in `rule_accounts` (`acct-a` … `acct-d`) holding its full state:
  futures state, round number, trades in round, status, config hash.
- `experimental-v1` keeps running unchanged and is shown as "Reference: original strict rules (v2)".
  *(My call; it costs nothing and keeps the old baseline visible.)*

**`rule_trades`** gets an `account` column, and trade ids become `<account>:<hit id>`, so four accounts taking the
same setup never collide. Existing rows get `account = 'experimental-v1'`.

**New `rule_decisions`**: one row per (account, confirmed setup) at each decision time.
| Column | Contents |
|---|---|
| `account`, `hit_id`, `time` | which account, which setup, when |
| `decision` | `TAKEN` / `BELOW_CUTOFF` / `GATED` / `POSITION_OPEN` / `PAUSED` / `PLAN_REJECTED` / `LOWER_SCORE` |
| `score`, `confidence`, `cutoff` | the numbers behind the decision |
| `ticks` | every checklist item and watch item with value and pass/fail |
| `sizing` | risk %, risk amount, quantity, notional, leverage, liquidation price |
| `snapshot` | the market at decision time (see below) |
| `model` | score model hash, config hash, rulebook hash |
| `trade_id` | when taken |

The `snapshot` holds:
- price change over 1h, 4h and 24h;
- 1h volume vs normal;
- long/short ratios, funding, open interest change, taker share;
- order-book imbalance and spread;
- liquidations in the last 15 minutes;
- Fear & Greed;
- the latest news item;
- the next macro event.

**Trade outcome.** After exit plus 4h, the existing diagnosis adds the diagnosis code, MFE/MAE and "what happened
after" to the trade payload. A short **plain-English explanation** is added too (§7.1).

**New `rule_reviews`**: one row per (account, round), holding the review report (§7.2), its status
(`pending` / `ready` / `resumed`) and the owner's resume time.

**Restart behaviour.** The engine reloads each account from `rule_accounts` and steps every missed 1m bar.
`fetch_fast` already backfills from the last stored minute, 1,000 bars per call, up to 2 days back.
- Open positions are settled on those real bars; no entries are made at past closes.
- If the gap is longer than the backfill reaches, the open position is settled on stored 5m candles. If those are
  missing too, the trade is closed at the first available price and flagged `SETTLED_AFTER_GAP`.

## 7. Reasoning and learning

### 7.1 Per trade
- **At entry:** the `rule_decisions` row above records why the trade was taken.
- **After exit plus 4h:**
  - the existing `diagnosis.code`, e.g. stop too tight, target too far, wrong regime, news shock;
  - a 2–4 sentence explanation from Claude, given only that trade's decision row, outcome and post-exit path.

**Claude call constraints (unchanged security rules):**
- native `claude.exe -p` on the subscription login, never an API key;
- run with `--tools ""`, `--strict-mcp-config`, hooks disabled, slash commands disabled and no session persistence,
  in an empty working directory;
- data passed via stdin, with `ANTHROPIC_*`, `CLAUDE_CODE_*`, `QUANTPILOT_*`, `OPENAI_*`, `AZURE_OPENAI_*` and
  `CODEX_API_KEY` stripped from the environment;
- if the call fails, the trade keeps its diagnosis code and the explanation is retried later.

### 7.2 Per 30 trades (the review)
Runs when an account pauses. All of it is read-only analysis; nothing feeds back into live decisions.

1. **Numbers:**
   - win rate, total and average net, max drawdown;
   - results by playbook, exit reason, diagnosis code and regime;
   - actual vs expected results for the account's score band.
2. **What separated winners from losers:** for each checklist item, watch item and snapshot field, winners vs losers
   with effect size. Thirty trades is a small sample, so findings are labelled "hint, not proven" unless they hold
   at a stated confidence.
3. **Learning counterfactuals** (shadow only), replaying the 30 trades:
   - the existing bandit learner (`learner.py`): which trades would it have skipped or taken wider?
   - the trade-management RL agents (`rl_manage.py`, tabular Q and PPO): would an earlier break-even or exit have
     changed the result?
4. **Plain-English summary** from Claude (same sandbox): where it went wrong, why, and up to three suggested changes,
   each tied to the evidence above.
5. **Suggestions are only proposals.** Applying one means a new account-config version and a dated pre-registration
   amendment, decided with the owner at the review.

## 8. UI (Rules page, plus a compact Overview strip)

**A new "Accounts" section** at the top of the Rules page: four cards side by side on wide screens, stacked on a
phone. Each card shows:
- name and tier;
- **status**: running / in a trade / paused for review / daily brake / floor hit;
- equity and P&L since start;
- **trades this round, e.g. "12 / 30"**, and the round number;
- **a "Rules" box**, generated from the account config so it can't drift. Example: "Takes a setup only when its
  score is at least 0.12 R, the top 10% historically. Risks 1% of equity (0.5% for lower-scored setups). Leverage
  2×. Pauses after 30 trades for review.";
- **a "Watches" box**: the checklist families and watch items it looks at, with live values (long/short ratio,
  book imbalance, funding, latest news);
- the open position: entry, stop, target, liquidation price, time in trade;
- the last decision: which setup, score vs cutoff, and ticked or unticked items.

**Trades table and reviews**
- Filterable by account.
- Clicking a trade shows: why it was taken (ticks, score, sizing), how it ended, and why it won or lost (code plus
  explanation).
- When an account is paused, its card links to the review report and shows a **Resume** button. The button posts to
  a loopback-only endpoint and starts the next round.

**Overview strip:** the four equities and their round progress.

**Endpoints**
- `GET /api/v1/rules/accounts`
- `GET /api/v1/rules/decisions?account=`
- `GET /api/v1/rules/reviews?account=`
- `POST /api/v1/rules/accounts/{id}/resume` (loopback only)

The MCP tool `get_rule_engine` gains the accounts summary.

## 9. Research and pre-registration before go-live

1. Write amendment **A8** in `docs/research/preregistration.md` *before* fitting. It records:
   - the four account configs and the score model's features and formula;
   - the tier percentiles, sizing and brakes;
   - that holdout data stays untouched.
2. Fit the score model on development data. Compute walk-forward scores, the four cutoffs, and per-account
   expectations: trades per week, win rate, average net, worst drawdown.
3. Publish those expectations in `docs/rule-engine.md` and on the Rules page, so the live results can be compared
   with them at each review.
4. **Go live regardless of whether the expectations are positive.** The owner wants the accounts to run and be
   judged at 30 trades.

## 10. Configuration

A new file `rulebook/accounts-v1.yaml` holds the four accounts (id, name, cutoff percentile, the resolved cutoff
score, leverage, risk %, half-risk rule, daily brake, floor, round length 30) and the score model path and hash. Its
hash is stored with every decision. Changing anything makes `accounts-v2`.

## 11. Testing

**Unit tests**
- tier selection at, above and below the cutoff;
- highest-score selection;
- sizing per account: risk amount, half-risk band, leverage cap, liquidation check;
- per-account brakes and floor;
- pause at exactly 30 and resume starting round 2;
- trade-id uniqueness across accounts.

**Persistence**
- Run cycles, drop the in-memory state, reload, and continue: the next trade is number n+1 and the round count is
  preserved.
- Simulated multi-hour downtime with an open position settles on the missed bars.

**Review job:** a fixture of 30 trades and a fake Claude runner produce a stored review. A Claude failure still
stores the numeric review.

**API:** account summary, decisions filtered by account, loopback-only resume.

**Playwright:** the four cards render at phone, tablet and desktop widths (extend `responsive.spec.ts`); the Rules
box text comes from config; the Resume button shows only when paused.

**Full suites green:** pytest, Playwright, `scripts/check-structure.cjs`.

## 12. Left to the builder
- Whether the shared pipeline is computed once and fanned out to accounts inside `rule_live.cycle`, or in a new
  module called from it.
- How the new tables are created (the existing `rule_*` tables are created from `store.Base`).
- Exact card styling, within the existing design system.
- Prompt wording for the Claude explanations, within the sandbox rules above.
