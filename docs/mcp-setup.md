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

Verify discovery and calls through the real official SDK client with `uv run pytest tests/test_services_mcp.py -q`. This passing test starts the stdio process, discovers all nine tools, checks their output schemas and structured service parity, and rejects invalid inputs. For a trained local release, run `uv run python scripts/smoke_release.py` to compare all nine real-data tool results with FastAPI responses and record actual latency.

Example assistant workflow: call `check_data_coverage`, then `get_market_snapshot`, `get_prediction` and `explain_decision`. Treat stale data and blocked gates as evidence to withhold a trade claim.

The actual trained release **24c8238525d4** passed discovery and all nine structured tool/API comparisons on 19 October 2026. See [smoke evidence](release-smoke.json). Cold Windows startup preloads trusted model/native libraries before starting the stdio event loop. Calls dispatch shared services to threads, and indicator results reuse a cache keyed by the current Parquet file generation. Initial feature/model loading is slower than cached reads.

