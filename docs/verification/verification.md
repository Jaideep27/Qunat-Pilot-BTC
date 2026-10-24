# Local release verification

Current local readiness is documented in [operations](../operations/operations.md) and [current verification](../evidence/local-readiness-verification.json): 88 Python cases, 14 browser cases and seven real PostgreSQL checks pass; the production build has no oversized chunks. Actual runtime evidence shows a connected five-second collector and 22 forward decisions. The new monitor requires a regular-terminal service reload; public hosting is deferred. The rest of this page preserves the original research-release verification, whose test counts and runtime restrictions describe that earlier checkpoint.

For the newer PostgreSQL market-storage and chart-workspace extension, see [market-platform.md](../operations/market-platform.md) and [its execution evidence](../evidence/market-platform-verification.json). The frozen research experiment below remains a separate reproducible result.

Verified on 19 October 2026 against frozen research model **24c8238525d4**. The [machine-readable manifest](../evidence/release-verification.json) contains source/artifact hashes, training identity, API latency and remaining deployment work. Earlier attempts are preserved in [verification history](verification-history.md).

| Gate | Actual result |
|---|---|
| Historical acquisition and recovery | 874 original Binance archives and two supplemental daily archives checked; 576 mark/index observations recovered |
| Coverage policy | Eligible under authorized bounded positioning quarantine; strict completeness remains false |
| Dataset and catalogue | 209,983 common eligible cutoffs, 106 features, every required family and all 24 months |
| Training/evaluation | Four groups, three model candidates, 14 walk-forward folds per group, frozen final two-month holdout |
| Frozen replay | Complete full-group trading object equals original evaluation, including fills and sampled curves |
| Python regression | **55 passed**, zero failures/errors/skips; 21.933 seconds in JUnit |
| Python compilation | Passed |
| Static repository checks | Seven passed |
| TypeScript and production build | Passed; approximately 801 kB main JavaScript chunk advisory |
| Browser | **Five passed** in 20.4 seconds with installed Chrome through Playwright |
| Real-data API/MCP | Ten read API routes including health; all nine official SDK stdio tool results match API data |
| Local research publishing/export | Coverage/evaluation projections published to SQLite; worker export hashes equal frozen artifacts |
| Live forward-worker attempt | Provider connection denied with WinError 10013 before reconciliation; no fills created |
| Public GitHub/Render/Vercel/Supabase | Configuration supplied; account setup and deployed verification pending |

## Tests and browser evidence

The Python suite checks future-row invariance, weekly/swing/timeframe boundaries, external availability and missing data, quarantine bounds, exact-source repair/provenance, risk/cost gates, long/short accounting, funding/stop/margin fixtures, retry/rollback behavior, worker reconciliation and API/MCP parity with invalid-input rejection. The latest worker fixture checks a funding settlement just after the last completed candle, before advancing the checkpoint.

The final suite used a project-local temporary directory because the default Windows temp location was denied. Two warnings remain: Starlette's httpx test-client deprecation and a denied pytest cache write. Neither failed a test. The original requirements document remains unchanged.

Four browser cases cover fixture navigation, mobile usability, explicit backend failures and missing-versus-invalid coverage. A fifth case uses the actual audited Parquet, trained model, holdout results and local API. It checks stale no-trade reasons, quarantine disclosure, calibration, equity/drawdown, empty holdout trades and the paper ledger. Screenshots were visually reviewed:

- [Market](../screenshots/release-market.png)
- [Prediction](../screenshots/release-prediction.png)
- [Research](../screenshots/release-research.png)
- [Paper portfolio](../screenshots/release-paper.png)

The real-data MCP smoke test starts a separate server with the official Python SDK, discovers all nine tools and compares each structured data result against FastAPI. Native ML libraries are initialized before the stdio event loop; tools run shared services in threads. This resolves a Windows cold-import stall observed when loading the trained model during a tool call. [Smoke evidence](../evidence/release-smoke.json) records the actual data/freshness gates and measured API latency: 14.69 seconds for the first full-history feature computation, 0.27 seconds for a cached indicator read and 9.63 seconds for the first prediction including model explanation. These are local single-run timings, not hosted service latency guarantees.

## Interpretation and remaining work

The full-group model has holdout ROC-AUC **0.5388**, Brier **0.2511** and balanced accuracy **50.65%**. Its frozen 0.60 confidence threshold, cost and risk gates produced **zero holdout trades**. No post-holdout threshold tuning was performed. There is no measured profitable strategy or forward paper track record. See [evaluation](../research/evaluation-report.md) and [model card](../research/model-card.md).

Source completeness is still false: five missing positioning timestamps and 57 invalid source rows are quarantined under the authorized policy. Raw observations and exact exclusions remain disclosed. All four experiments use identical eligible timestamps.

The training identity remains immutable. Later MCP startup and worker funding-tail fixes are identified separately by runtime source hashes in the manifest; they do not retrain the model or retune its holdout policy.

Public deployment, PostgreSQL integration and scheduled forward-worker operation require the remote repository, hosting accounts and protected database connection described in [deployment](../operations/deployment.md). They are not covered by the passing local SQLite/browser tests. The current environment denies outbound provider sockets; the actual worker attempt failed before account mutation. The local account remains 10,000 USDT with zero fills.

## Reproduce checks

```powershell
uv run pytest -q --junitxml=.verification/pytest-release-final.xml
npm --prefix frontend run build
# Run API :8000, then enable the real release browser case:
$env:QUANTPILOT_RELEASE_SMOKE='1'
npm --prefix frontend test
uv run python scripts/release/smoke_release.py
uv run quantpilot backtest --group full
```

The release verification scripts also retain raw command logs under `.verification/`. [Python evidence](../evidence/python-regression-verification.json) and [frontend evidence](../evidence/frontend-milestone-status.json) carry report/source hashes. Re-running tests does not refresh or retrain the frozen holdout experiment.
