# Market outlook Q&A — design

Date: 2026-10-20 · Status: draft for owner review · Branch: `news-pipeline`

## 1. Intent

The owner wants to ask natural questions such as "Will BTC go up or down in the next 1h / 4h / 24h?" and receive a real, immediate answer that uses everything QuantPilot has: indicators, volume, positioning, funding, the macro calendar, news and the validated forecasters. Answers must be reachable from a chat with Claude (via MCP tools) and from a dashboard Ask box.

Constraints carried from the research (docs/forecast-research.md):

- No model has validated skill for BTC direction at 1h, 4h or 24h. An honest answer is a **probability with reasons and a confidence label**, never a yes/no promise.
- Every number shown must come from a method that has been checked historically and is scored forward.
- Nothing in this feature trades, sizes positions or changes the paper-trading policy.
- Claude runs only on the saved claude.ai subscription (`claude -p`), never a billed API key.

Success criteria:

1. A supported question receives an answer within ~30 seconds (≤ 3 seconds without the AI explanation).
2. The answer states: headline probability, method, confidence label, likely range, key evidence with percentiles, the AI's separate view, and the method's track record.
3. Every answer is stored and scored after its horizon; a scoreboard compares quant, AI and base-rate accuracy.
4. When Claude is unavailable, the quant answer still works.

## 2. Supported question types

Free-form wording is converted to one canonical question:

| Type | Fields | Example |
|---|---|---|
| `direction` | `horizon_hours` (1–168) | "Will BTC be higher in 4 hours?" |
| `touch` | `horizon_hours`, `level` (USD), `side` (`above`/`below`, inferred from current price when omitted) | "Will BTC hit $120k by Friday?" |
| `range` | `horizon_hours`, `coverage` (default 0.8) | "Where will BTC be tomorrow?" |
| `big_move` | `horizon_hours`, `threshold_pct` (default 5), `side` (`down`/`up`/`either`) | "Chance of a 5% drop today?" |
| `explain` | `horizon_hours` (look-back, default 24) | "Why is BTC dumping?" |

Horizons above 168 hours, non-positive levels and unknown types are rejected with an explanation of what is supported. Time expressions ("by Friday", "today", "this week") are resolved to hours in UTC at ask time.

## 3. Architecture

```
question text ──► questions.parse (rules) ──┐
                  ai.parse (claude -p) ─────┤──► canonical Question
                                            ▼
                         outlook.answer(Question, now)
                 ┌──────────────┼──────────────┬───────────────┐
           base rates     analogs.estimate   touch/range     evidence pack
           (5y hourly)    (+ validation      (vol champion,  (state %iles, macro,
                           gate)              calibration)    news, recent change)
                 └──────────────┴──────┬───────┴───────────────┘
                                       ▼
                      ai.explain (claude -p) → text + own probability
                                       ▼
                     answers.record → scored later by answers.evaluate
```

### Units

| Module | Responsibility | Depends on |
|---|---|---|
| `src/quantpilot/questions.py` | `Question` pydantic model, rule-based parser for common phrasings, time-expression resolution, validation | — |
| `src/quantpilot/analogs.py` | State vector at a cutoff (past-only percentile ranks), nearest-neighbour outcome estimate with shrinkage to the base rate, walk-forward validation and promotion decision | `event_study.hourly_panel`, `macro.macro_features` |
| `src/quantpilot/outlook.py` | Answer a canonical `Question`: base rate, analog estimate (if promoted), touch/range probabilities, evidence pack, confidence label, track record | `analogs`, `forecaster` champions/bundle, `event_study`, `macro`, `news` |
| `src/quantpilot/ask_ai.py` | `claude -p` question parsing and answer writing with JSON schemas; validation of cited evidence keys; reuses the news module's isolation (`claude_executable`, `claude_env`, usage-limit handling) | `news` helpers |
| `src/quantpilot/answers.py` | `OutlookAnswer` table, recording, forward scoring, scoreboard | store, market candles |

Existing modules change only to register tables (`store.initialize`), add the service/API/MCP entry points and add supervisor threads.

## 4. How each type is answered

All computations use only data available at the ask time (the latest completed 5-minute bar).

### 4.1 Base rates

From the five-year hourly panel: the frequency of `close(t+h) > close(t)` for direction, of touching a threshold for big moves, computed over all hours with known outcomes. Reported with a Wilson 95% interval on the number of *non-overlapping* windows.

### 4.2 Analog ("similar situations") estimator

- **State vector** at a cutoff: realized volatility (24h), 24h return, EMA-50 distance (ATR units), RSI-14, funding rate, 24h open-interest change, global long/short ratio, hours to the next CPI/jobs/FOMC release (capped at 168). Each feature is converted to its **percentile rank among past values only** (expanding window) so no future distribution leaks in.
- **Neighbours:** the `k = 200` past hours closest in Euclidean distance on the percentile vector, restricted to hours whose outcome window ended before the cutoff, and thinned so selected neighbours are at least 24 hours apart.
- **Estimate:** `p = (hits + m·base) / (k_eff + m)` with prior strength `m = 100` (shrinkage toward the base rate), plus a Wilson interval on `k_eff`.
- **Walk-forward validation** (`quantpilot validate-outlook`): for cutoffs sampled every 6 hours over the five-year panel (after a one-year burn-in), compute the estimate using only earlier data and score Brier vs the base rate at standard horizons 1h, 4h, 12h, 24h, 72h, 168h. Results per horizon: skill, block-bootstrap interval (weekly blocks), calibration table.
- **Promotion gate** (same rule as the forecaster): promoted for a horizon only when mean skill > 0 on the validation span **and** on a later confirmation span (last 12 months), with at least half of the yearly sub-periods better. Non-standard horizons use the gate decision of the nearest standard horizon.
- **Headline:** promoted → analog probability; otherwise → base rate, with the analog result shown as "historical context, not validated".

### 4.3 Touch and big-move probabilities

- **Raw probability:** reflection-principle barrier probability for a driftless log-price, `P = 2·(1 − Φ(|ln(L/S)| / (σ_h)))`, capped at 1, where `σ_h` is the horizon volatility from the deployed volatility champion (EWMA or GARCH) and S the current price.
- **Calibration:** `validate-outlook` replays sampled cutoffs with thresholds of ±1, 2, 3, 5, 7, 10% at standard horizons, compares raw probability with realized touches (hourly highs/lows), and fits an isotonic calibration on the earlier span. The calibration is used only if it lowers Brier on the confirmation span (same gate); otherwise raw probabilities are shown, labeled "uncalibrated".

### 4.4 Ranges

- 1h and 4h: the deployed range champion's quantiles from the shadow forecaster.
- Other horizons: `σ_h` × the empirical quantiles of standardized returns (`return / σ_h`) from history. Coverage of the 80% range is reported from `validate-outlook`.

### 4.5 Explain

No probability. Evidence for the look-back window: return, volume ratio, taker-buy share, funding and OI change, long/short ratio change, macro releases inside the window with first-release values, and analysed news events (Claude/Codex) with source links. Not scored.

### 4.6 Evidence pack and confidence label

Every answer carries `evidence`: keyed values with their 5-year percentile (e.g. `funding_rate: 0.00012, pct 92`), upcoming macro events inside the horizon, and the latest relevant analysed news. `confidence` is one of:

- `no_edge` — headline is the base rate (method not promoted),
- `weak` — promoted method whose Brier improvement over the base rate is below 1% (relative), or with fewer than 300 independent validation windows,
- `validated` — promoted with larger, confirmed skill.

`track_record` holds the validation summary and the live forward score for the same type/horizon bucket.

## 5. AI layer (`ask_ai.py`)

Both steps call `claude -p` with `--model claude-sonnet-5-5 --effort low`, no tools, no MCP servers, hooks disabled, no session persistence, an empty working directory, the native executable, API keys stripped, and the question/data passed only via stdin — identical isolation to the news reasoner.

- **Parse:** JSON schema = `Question` plus `supported: bool`, `reason`. Used only when the rule-based parser cannot classify the text. Output is re-validated by `Question`.
- **Explain:** input = the quant answer and evidence pack. JSON schema: `answer` (≤ 150 words), `ai_probability` (0–1, null for explain), `reasons` (2–5 items, each with `evidence_key` that must exist in the pack — otherwise the response is rejected), `agrees_with_quant: bool`. The prompt forbids changing quant numbers, requires stating the confidence label and forbids trading advice.
- Usage limits use the same `UsageLimitReached` handling; an hourly/daily ask budget (`QUANTPILOT_ASK_DAILY_LIMIT`, default 100) is enforced in `answers`.
- Failure → quant-only answer with a template explanation and `ai_status = unavailable`.

## 6. Interfaces

| Interface | Behavior |
|---|---|
| `POST /api/v1/ask` `{"question": "..."}` | Full pipeline; returns the answer object. Accepted only from loopback clients (checked in the handler, in addition to the 127.0.0.1 bind), one AI call at a time, daily limit. CORS gains `POST` for the configured dashboard origins. |
| `GET /api/v1/answers?limit=50` | Stored answers with outcomes and the scoreboard. |
| MCP `ask_outlook(type, horizon_hours, level?, side?, threshold_pct?, coverage?)` | Quant answer + evidence for a structured question (the chat Claude does the parsing). Records the answer with `source = mcp`. |
| MCP `record_ai_view(answer_id, probability, reasons)` | Lets the chat Claude log its own view so it is scored like the Ask-box AI view. |
| MCP `get_answer_scoreboard()` | Scoreboard. |
| CLI `quantpilot ask "<question>"`, `quantpilot validate-outlook` | Same pipeline from the terminal; validation run that writes `artifacts/outlook-validation/report.json` read by the gates. |
| Dashboard **Ask** tab | Question box with example chips, answer card (headline probability + confidence label, range, top evidence, AI view marked "scored separately", track record), history with ✓/✗ outcomes, scoreboard. |
| Claude Code connection | Documented one-liner: `claude mcp add quantpilot -- "C:\Projects\BTC Quant\.venv\Scripts\python.exe" -m quantpilot.mcp_server`. |

## 7. Recording and scoring (`answers.py`)

- Table `outlook_answers` (migration `0008`): id, asked_at, source (`ask`, `mcp`, `scheduled`, `cli`), question, quant answer, evidence, AI view, outcome. Row-level security enabled on PostgreSQL like other tables.
- **Scoring** (supervisor, every 15 minutes, after data is available): direction from closes; touch/big move from 5-minute highs/lows over the window; range from inclusion. Brier is computed for the quant probability, the AI probability and the base rate.
- **Scoreboard** per type × horizon bucket: scored count, independent count (answers at least one horizon apart), mean Brier for quant/AI/base, calibration buckets. Verdicts ("quant beats base", "AI beats quant") appear only with ≥ 30 independent answers; otherwise "too early to judge".
- **Scheduled questions:** every hour, quant-only answers for direction 1h/4h/24h and ±3% touch within 24h (`source = scheduled`); the AI view is added to at most `QUANTPILOT_ASK_SCHEDULED_AI_PER_DAY` (default 6) of them per day.

## 8. Failure handling

| Condition | Behavior |
|---|---|
| Latest candle older than 10 minutes | Answer returned with `stale_data` warning; still recorded and scored |
| Missing state features | Analog step skipped; headline = base rate with reason |
| Unsupported / invalid question | 422 with the supported types and examples |
| Claude unavailable, usage-limited or invalid output | Quant answer, `ai_status` explains why |
| Daily ask limit reached | 429 with reset time |
| No validation report yet | Every method treated as not promoted (`no_edge`), touch probabilities labeled uncalibrated |

## 9. Testing

Written first (TDD), mirroring existing test style:

- `questions`: table of phrasings → canonical questions; time expressions; rejections.
- `analogs`: percentile ranks use only the past (future mutation leaves results unchanged); shrinkage literal; neighbours exclude unfinished outcomes and respect spacing; gate decisions on literal validation rows.
- `outlook`: barrier probability literal values; base-rate fallback when not promoted; evidence percentiles; confidence labels.
- `ask_ai`: fake CLI outputs — schema validation, unknown evidence keys rejected, usage-limit path, isolation arguments (no tools, stdin only, no API key).
- `answers`: recording idempotence, scoring on hand-built price paths for each type, independent-count rule, budget limits.
- API/MCP: parity with services, loopback restriction, `POST` accepted only on `/ask`, no trading side effects.
- Playwright: Ask tab with fixtures — answer card, no-edge label, AI-unavailable state, history outcomes.
- Real run: `quantpilot validate-outlook` on the five-year panel; results summarized in `docs/forecast-research.md`.

## 10. Out of scope

- Using any answer, quant or AI, to place or size paper trades.
- Questions about assets other than BTCUSDT perpetual.
- Horizons beyond 7 days.
- Historical news backfill.
