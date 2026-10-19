# Local operations and readiness

The [latest local completion manifest](local-completion-verification.json) adds seven startup-readiness tests, an actual PostgreSQL persistence sample and the refreshed, decoded [two-minute walkthrough](walkthrough.md). The frozen research/application verification remains separate. Reproduce the new checks with `node --test tests/service-readiness.test.cjs`, `node scripts/start.cjs --check` and `node scripts/verify_persistence.cjs`.

The latest extension adds `uv run quantpilot doctor`, `backup-db` and `restore-check`. [Doctor evidence](local-doctor.json) confirms complete five-year candle coverage and reachable dashboard/API, while identifying the pending monitor reload. It records 36 forward decisions over about 8.88 hours with zero fills; this supersedes the earlier 22-decision snapshot below. [Recovery checks](recovery.md) restored all seven database tables and 3,639,242 stored rows in an isolated drill. The current Python suite passes 88 cases.

The Paper portfolio view now includes persistent worker monitoring. [Runtime evidence](operations-runtime.json) reads actual PostgreSQL state through the updated application code without executing a worker or inserting test prices. [Verification evidence](local-readiness-verification.json) records tests, migrations and build sizes. Public deployment is deferred at the owner's request.

## Activate and inspect

`node scripts/start.cjs --check` performs a read-only check of the running API and its monitoring response. It starts no processes and exits 2 when a reload or readiness gate needs attention. The normal launcher now checks operational readiness after startup; HTTP health alone is not reported as full readiness. Seven Node regression cases cover old APIs, malformed/unavailable monitors, failed gates and request failures. These checks also run in the full verifier and CI configuration.

From the regular network-enabled PowerShell terminal in the project directory:

```powershell
node scripts/start.cjs --restart-services
uv run quantpilot status
```

Open [Paper portfolio](http://localhost:5173), select **Paper portfolio**, and inspect **Worker monitoring**. The launcher reuses the project PostgreSQL instance and initializes missing tables without resetting the account. It checks recorded process ownership and creation time before replacing the API and supervisor. The first worker attempt performs current reconciliation immediately; later attempts target UTC minutes **2, 17, 32 and 47**. This is a target schedule; delays and failures remain visible.

The existing collector is already connected with a declared five-second polling interval. At the recorded check, the immutable ledger contained 22 forward decisions spanning about 5.32 hours, with zero forward or replayed fills. The latest decision passed input availability checks. A no-trade decision is valid operation; monitoring never forces an entry or relaxes the frozen model policy.

The running API returned 404 for the new operations route at that check. The updated code returned a healthy paper checkpoint but readiness 503 because no instrumented runs existed yet. After a reload and successful worker attempt, new outcomes and durations will appear. Existing historical decisions are counted, but their run durations are not reconstructed. Automatic approval review rejected stopping the existing API because sandbox approval is disabled; the existing services were preserved, and activation is prepared for the regular terminal.

## Interfaces

| Interface | Behavior |
|---|---|
| `GET /api/v1/operations?limit=20` | Readiness checks, recent runs, lifetime run totals, collector status and full-ledger evidence; limit 1–100 |
| `GET /api/v1/ready` | 200 when every readiness gate passes, otherwise 503 with the individual gates |
| `GET /api/v1/health` | Existing basic service response; use `/ready` for operational gating |
| `uv run quantpilot status --limit 10` | Same underlying report; exits 2 when readiness fails |
| `uv run python scripts/verify_operations.py` | Read-only actual-database inspection through current code plus a check of the running API version |
| `uv run python scripts/verify_postgres.py` | Seven PostgreSQL integration checks in a uniquely named disposable database; fixtures stay out of the real account |

The detailed report requires a reachable database, recent completed BTCUSDT candles, a fresh paper checkpoint, started run instrumentation, released model metadata, eligible coverage and a last decision without critical data/model gates. Default worker staleness is 1,800 seconds, configurable with `QUANTPILOT_WORKER_MAX_AGE_SECONDS` (900–7,200). Database errors return a generic 503 without publishing connection strings. Weak forecasts and insufficient expected edge remain valid no-trade results.

## Evidence conventions

`worker_runs` persists start/end times, outcome, final or failing stage, duration, model/config identity and associated decision reasons separately from the immutable accounting ledger. Failed runs publish the exception class rather than the exception message. If the process disappears, the next lock-owning worker marks its unfinished run interrupted. That timestamp records detection, and its unmeasured duration remains null.

The UI refreshes monitoring every 15 seconds. A new failure overrides an older fresh checkpoint. Successful duration p50/p95 values use successful runs among the latest 100 recorded runs, with sample count and scope reported. These are worker durations, not exchange-to-browser latency. Current running rows initially show the ingestion stage; completion/failure records the final stage.

Forward decisions and fills are counted from the entire immutable ledger rather than the dashboard's capped 2,000-row account arrays. Replayed fills are separate. Observed span means first forward decision to latest checkpoint; it is not an uptime percentage. Sustained reliability evidence must accumulate during operation.

## Storage, migrations and loading

The owner launcher uses idempotent schema initialization for existing installations. Migration revision `0003_market_history` now correctly follows revision `0002`, and `0004_worker_runs` adds monitoring with PostgreSQL row-level security enabled. The integration check applies the full Alembic chain to an empty isolated database before exercising COPY/upsert, quality flags, completed/forming separation and worker readiness. CI now includes a PostgreSQL service for the same check; remote CI execution remains pending repository setup.

Research plots, chart workspace and worker monitor load on demand. The current production entry JavaScript is about 439 kB and the research plot chunk about 368 kB, both below Vite's 500 kB advisory threshold. Market no longer downloads the full backtest evaluation or portfolio on each refresh; a browser test checks that these requests start when their views are opened.

Five-year storage coverage refers to BTCUSDT price candles, not five years of every derivatives or sentiment feature. Frozen research model `24c8238525d4`, its 24-month experiment and its authorized positioning quarantine remain unchanged. See [live data](live-data.md), [market storage](market-platform.md) and [evaluation](evaluation-report.md).

## Remaining external verification

`node scripts/verify_persistence.cjs` observes actual saved candle receipt timestamps through the local API. The [recorded sample](persistence-runtime.json) contains four distinct saved observations, with a median interval of 5.638 seconds and median local API request time of 159.5 ms. Prices did not change in that short sample; timestamp advancement verifies ongoing snapshot writes. These are observed save intervals, not exchange-to-database latency or an uptime measurement.

Public GitHub/hosting setup is deferred. The agent-controlled browser is denied outbound Binance WebSocket connections; actual WebSocket latency remains unverified here. The independent connected collector and PostgreSQL persistence have actual runtime evidence. `node scripts/verify_live.cjs` can collect provider-stream evidence from a terminal/browser environment that permits it. Neither a passing simulated stream test nor a healthy database checkpoint proves live WebSocket latency or long-term uptime.
