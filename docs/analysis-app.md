# Analysis app (amendment A15, 2026-11-02)

The owner switched QuantPilot from paper trading to analysis. The app now explains what Bitcoin is doing: every indicator and every strategy, what each one says now, and a written analysis by Claude. **Nothing trades.** The trading code, accounts and journals are kept and archived.

## What runs

| Part | What it does | How often |
|---|---|---|
| Analysis snapshot (`analysis/snapshot.py`) | One JSON with price, market type, about 30 indicator readings on 15m/1h/4h/1d, group summaries, support/resistance, the likely 4h range, every strategy's live view, the calendar and headlines | Every 5 minutes, stored for 24 hours (`analysis_snapshots`) |
| Market brief (`analysis/ai.py`) | Claude **Haiku** writes a brief from the compact snapshot | Every 4 hours, plus after a CPI, jobs or FOMC release (`analysis_briefs`) |
| Explain this (`analysis/ai.py`) | Claude **Sonnet** explains one indicator, strategy, news item or chart view | On request; cached per snapshot; at most 30 an hour (`analysis_explanations`) |
| Data, news, macro, forecasts, live recorder | Unchanged | As before |
| Paper trading (old paper worker, rule engine, trade reviews) | **Off** unless `QUANTPILOT_TRADING_ENABLED=true` | — |

Claude runs on the owner's subscription through `claude -p` (no tools, hooks or MCP; API keys stripped). It may only use the snapshot's numbers, never tells the reader to trade, and every text is labelled "AI analysis, not financial advice".

## Code

- **`analysis/catalogue.py`**: the indicator library. Each indicator has its group, settings, a plain "what it is" and "how to read it", reading zones and the chart studies it maps to.
- **`analysis/readings.py`**: current values, zones and sparklines on completed bars only. These use the same maths as the chart (`features.core.technical`) and the rule engine (`features.patterns`).
- **`analysis/strategies.py`**: live checklists and verdicts for:
  - Trend Rider, Range Trader, Breakout Hunter, Funding Collector and the Master Bot logic;
  - the 13 rulebook playbooks (20 versions).

  Each also carries its history-test record.
- **`analysis/snapshot.py`**: builds the snapshot, saves it, and produces the compact form that Claude reads.
- **`analysis/ai.py`**: briefs, explanations, the cache and the rate limit.
- **API (read):** `GET /api/v1/analysis/snapshot`, `/analysis/catalogue`, `/analysis/briefs`, `/analysis/explanations`. These are also on the public link.
- **API (this computer only):** `POST /api/v1/analysis/explain` and `POST /api/v1/analysis/brief`. The public gateway never forwards them.
- **Dashboard:**
  - tabs: Home, Indicators, Strategies, Chart, More;
  - under More: News, Ask, Research, Trading history (archived) and System;
  - the Rule engine and Old paper account pages are reached from Trading history.
- **Trading switched off:**
  - `settings.trading_enabled` (default off) decides which supervisor threads run;
  - `rulebook/accounts-v4.yaml` has no accounts;
  - `quantpilot retire-trading` retires the Master Bot and pauses the reference and old paper accounts.
- **Health checks:**
  - With trading off, readiness no longer waits for the paper worker.
  - The watchdog's heartbeat is the analysis snapshot (15-minute window).
  - The readiness candle query uses the index; it took 5–50 s when the laptop was short of memory.

## Honest limits

- The indicator and strategy read-outs describe the market. They are not predictions, and none of the strategies is proven (see `docs/research/style-research.md` and `docs/rule-engine.md`).
- The only forecasting skill that tested well is the likely-range forecast, which is shown as the 4h range bar.
- Liquidation and order-book readings exist only since 20 October 2026 (recorded live).
