# MCP setup

The custom server uses the official MCP Python SDK v1 and local stdio. It is read-only. FastAPI and MCP invoke the same functions in `services.py`.

Install the project and initialize its ledger:

```powershell
uv sync --extra dev
uv run quantpilot init-db
uv run quantpilot-mcp
```

For an external assistant supporting stdio MCP, adapt its server configuration:

```json
{
  "mcpServers": {
    "quantpilot-btc": {
      "command": "C:/Projects/BTC Quant/.venv/Scripts/python.exe",
      "args": ["-m", "quantpilot.mcp_server"],
      "env": {
        "QUANTPILOT_DATA_DIR": "C:/Projects/BTC Quant/data",
        "QUANTPILOT_ARTIFACT_DIR": "C:/Projects/BTC Quant/artifacts",
        "QUANTPILOT_DATABASE_URL": "sqlite:///C:/Projects/BTC Quant/data/quantpilot.db"
      }
    }
  }
}
```

On Linux/macOS, use the absolute `.venv/bin/python` executable and matching paths. To inspect the hosted paper account, point the local process at its protected PostgreSQL connection string using an environment secret. Keep credentials out of committed assistant configuration.

| Tool | Inputs | Purpose |
|---|---|---|
| get_market_snapshot | limit 1–2000 | Completed candles |
| get_indicators | none | Latest causal feature snapshot |
| get_market_positioning | none | OI and three long/short ratios |
| get_sentiment | none | Available Fear & Greed |
| check_data_coverage | none | Gap report and release status |
| get_prediction | none | Probability, gates, model associations |
| explain_decision | index 0–1999 | Persisted decision, newest first |
| compare_backtests | 1–4 named groups | Identical-timestamp feature ablations |
| get_paper_portfolio | none | Balances, position, costs, checkpoint |

Every response includes source, observation/served timestamps, freshness, and configuration/model version where applicable. Missing observations or unverified models return explicit blocked/no-trade results. Inputs use Pydantic bounds and enum values; there is no arbitrary URL, SQL or filesystem argument.

Verify discovery and calls through the real official SDK client with `uv run pytest tests/test_services_mcp.py -q`. This passing test starts the stdio process, discovers all sixteen tools, checks their output schemas and structured service parity, and rejects invalid inputs. For a trained local release, run `uv run python scripts/smoke_release.py` to compare the nine release-era tool results with FastAPI responses and record actual latency; it requires those nine tools and accepts the tools added later.

Example assistant workflow: call `check_data_coverage`, then `get_market_snapshot`, `get_prediction` and `explain_decision`. Treat stale data and blocked gates as evidence to withhold a trade claim.

The actual trained release **24c8238525d4** passed discovery and all nine structured tool/API comparisons on 19 October 2026. See [smoke evidence](evidence/release-smoke.json). Cold Windows startup preloads trusted model/native libraries before starting the stdio event loop. Calls dispatch shared services to threads, and indicator results reuse a cache keyed by the current Parquet file generation. Initial feature/model loading is slower than cached reads.


## Asking outlook questions from Claude Code

Later additions bring the full server to sixteen tools: `get_news_events`, `get_macro_calendar`, `get_shadow_forecasts`, `get_strategy_research` (the pre-registered strategy research verdict and forward data collection), and three outlook tools: `ask_outlook` (validated probability or range for direction, touch, range, big-move and explain questions), `record_ai_view` (logs the assistant's own probability so it is scored separately) and `get_answer_scoreboard`.

Connect QuantPilot to Claude Code with the fast proxy, which forwards to the running local API (start services first with `node scripts/start.cjs`):

```powershell
claude mcp add quantpilot -- "C:\Projects\BTC Quant\.venv\Scripts\python.exe" -m quantpilot.mcp_proxy
```

Then ask in a Claude Code chat, for example "Will BTC go up in the next 4 hours?". Claude calls `ask_outlook`, quotes the probability with its confidence label (`no_edge`, `weak` or `validated`) and evidence, and records its own view with `record_ai_view`. Every answer is scored once its horizon passes. When the API is not running, use the full server instead: `-m quantpilot.mcp_server`. The in-app **Chat** tab uses the same proxy and tools.
