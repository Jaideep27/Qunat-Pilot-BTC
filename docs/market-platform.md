# PostgreSQL market history and chart workspace

The native market workspace is available at [localhost:5173/#chart](http://localhost:5173/#chart). The isolated project database runs on PostgreSQL 18 at localhost:55432. Credentials are generated into ignored local files and `.env`; they are never placed in frontend code. Existing PostgreSQL services and databases are preserved.

## Current data status

The initial migration imported **910,730 rows**: 227,808 candles, 227,808 mark observations, 227,808 index observations, 221,755 positioning observations, 2,373 funding observations and 3,178 sentiment observations. That initial candle dataset began on 1 August 2024. Subsequent archive ingestion has extended it; the current five-year audit below supersedes those initial coverage boundaries.

Actual live price changes have been verified through PostgreSQL, the API and the browser. The five-year perpetual candle audit has **525,888 stored out of 525,888 expected observations, with zero missing timestamps**. The audit spans 19 October 2021 through 19 October 2026, at its recorded five-minute cutoff. See [five-year and live evidence](evidence/five-year-live-status.json). Earlier reports describing spot backfill are historical snapshots: the current configuration has one active symbol, **BTCUSDT**, using the perpetual feed. Already-running services need the restart below to load the new configuration.

From the normal, network-enabled PowerShell terminal used for the earlier successful data downloads, run:

```powershell
cd 'C:\Projects\BTC Quant'
node scripts/start.cjs
# Once after this single-market update, reload the running API and collector:
node scripts/start.cjs --restart-services
```

This starts or reuses the isolated PostgreSQL server, FastAPI and dashboard, then starts a single locked data supervisor in the background. Logs are under `.verification/services/`. The supervisor retries failed collection with backoff, resumes historical acquisition every 30 minutes and invokes paper reconciliation every 15 minutes. Successful provider responses determine freshness; starting a process does not label a feed connected.

The restart command validates recorded Python process ownership and creation times before stopping only the project's API and data supervisor. PostgreSQL and the dashboard remain running. The current agent session cannot terminate these existing processes, and its outbound exchange connections are blocked, so activation must run from the same regular terminal that established the working feed. After restarting, `uv run python scripts/verify_single_market.py` checks the actual API market schema, connected collector and five-year timestamps; [the status report](evidence/single-market-status.json) records whether activation is complete.

## One active BTCUSDT dataset

BTCUSDT uses Binance USD-M perpetual candles, matching derivatives features and the simulated long/short portfolio. Spot and perpetual prices can differ; spot candles are never mixed into this series. PostgreSQL retains the original internal key `BTCUSDT_PERP` so existing history, archive checkpoints and research provenance remain intact. The API and dashboard use the public symbol `BTCUSDT`.

The synchronization target is the **latest five years**. `QUANTPILOT_MARKET_HISTORY_YEARS` defaults to 5; `backfill-history --years 5` sets it explicitly. `--all-history` requests earlier perpetual history only. Older imported rows remain intact, and the frozen ML dataset is unchanged.

| Market | Source and handling |
|---|---|
| BTCUSDT | Active: Binance five-minute perpetual candles, mark/index, funding and available positioning archives. Provider 404s remain unavailable records. |
| BTCUSDT_SPOT | Inactive legacy archive; excluded from current synchronization, charts, API market choices and coverage targets. |
| BTCUSD_REFERENCE | Inactive Coin Metrics daily reference archive; excluded from the active workspace and API. |

Each feature family begins when the source actually provides it. The preserved Coin Metrics archive remains attributed under **CC BY-NC 4.0**. Existing browser drawings under the earlier perpetual symbol remain available after this update.

Archives are checksum-verified, cached and checkpointed individually in `market_feeds`. Retry-safe bulk COPY/upsert uses `(market, interval, event_time)` or `(market, family, event_time)` keys. Missing positioning observations are not interpolated; invalid numeric external values are retained as null with `valid=false`. These market tables are separate from the immutable Parquet dataset and trained holdout release.

Owner commands:

```powershell
uv run quantpilot import-market
uv run quantpilot backfill-history
uv run quantpilot collect-live
```

Live REST polling persists both forming and completed five-minute candles. After downtime it paginates from the latest completed candle. A forming observation cannot overwrite a completed candle. The paper worker also persists live mark/index/funding/positioning/sentiment frames before rotating its bounded Parquet cache. Full history remains in PostgreSQL.

The chart now receives BTCUSDT candles directly from Binance's WebSocket stream and updates price/volume incrementally. The provider documents a 250 ms update cadence when updates exist; actual latency depends on the network. Each selected timeframe has its matching stream. PostgreSQL synchronization runs independently, targeting every five seconds (`QUANTPILOT_MARKET_POLL_SECONDS`, 1–60 seconds), with a reused HTTP connection pool and catch-up pagination. It saves current candle snapshots and every completed candle. Full raw trade history is outside this candle store.

Live and stored prices have separate labels. A stream that closes or sends no valid observation for ten seconds reconnects with bounded backoff; during disconnection the chart polls PostgreSQL every five seconds and retains the last observation. History and completed-candle studies refresh every fifteen seconds. Forming stream candles never become model inputs. See [live-data behavior and verification](live-data.md).

Run `uv run quantpilot market-coverage` to audit exact native timestamps in the five-year BTCUSDT window and list actual gaps. Candle completeness does not establish five-year completeness for derivatives/sentiment families, whose source coverage remains separate.

## Trading workspace

The live chart loads PostgreSQL history through bounded API queries, then applies direct exchange stream updates. It supports five-minute, 15-minute, 30-minute, hourly, four-hour, daily and weekly views; candlestick, OHLC-bar, line and area styles; crosshair, pan, zoom, fit/latest controls; and CSV/PNG export.

The searchable **86-study catalogue** includes EMA/SMA, Bollinger/Keltner/Donchian levels, UTC session VWAP, RSI, stochastic variants, MACD, ADX, ATR, CCI, Williams %R, OBV, MFI, volatility, volume, candle structure, confirmed swings, prior levels, pivots and normalized research features. Up to 12 studies can be active. Oscillators use separate panes. Only requested series are transferred; calculations use completed candles and explicit warm-up. Changing timeframes never treats an unfinished higher-timeframe candle as completed.

Drawing tools include trend line, ray, horizontal and vertical lines, rectangle, Fibonacci retracement, measurement and text. Undo/clear controls are available. Drawings are anchored to time/price and saved per market/timeframe in browser storage; selected studies are also saved. They are personal chart settings, separate from PostgreSQL market data.

The optional **Official TradingView chart** uses TradingView's hosted widget and its available indicators/drawing controls. It has a separate provider feed and requires access to TradingView's servers. It is not represented as data from our database, and it does not include every proprietary feature or private Pine script on TradingView. The native workspace remains available without that widget.

## API and verification

- `GET /api/v1/market/storage`: active BTCUSDT database counts, first/last observations and collector status.
- `GET /api/v1/market/candles`: bounded BTCUSDT/timeframe history, optional `before` cursor and explicit forming-candle flag. The default and only public market value is `BTCUSDT`.
- `GET /api/v1/market/studies`: catalogue plus up to 12 selected study series.

All routes are read-only. Ingestion runs through owner services. PostgreSQL data survives API/collector restarts. The project cluster is under ignored `data/postgres-local/cluster`; back it up with PostgreSQL tools before moving it. The native instance is not installed as a Windows startup service: run the launcher after reboot. Docker Compose supplies a separate PostgreSQL database on localhost:55433 with the generated private environment settings.

The actual PostgreSQL integration script creates and removes a uniquely named test database, checking COPY/upsert deduplication, forming/complete separation, JSON quality flags, completed-bar queries and persisted mock-provider ingestion. Mock responses validate mechanics; they are not evidence of an actual live provider connection. See the latest [market verification](evidence/market-platform-verification.json).

The earlier platform verification passed **64 Python cases**, **six browser cases**, five real-PostgreSQL integration checks and all nine real-data MCP/API comparisons. The five-year/live extension additionally passed 11 focused Python cases and a production build. Its actual-provider browser probe confirmed a new PostgreSQL observation and an on-screen price change. [Live workspace screenshot](screenshots/live-postgres.png) and [five-year/live evidence](evidence/five-year-live-status.json) record the current connected state. The earlier restricted collector test had been stopped before the successful network-enabled supervisor was launched.

Sources: [Binance public data](https://github.com/binance/binance-public-data), [Coin Metrics archive and license](https://github.com/coinmetrics/data), [TradingView widget documentation](https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/).
