# Acceptance map

Current local verification passes 88 Python cases, 14 browser cases and seven real PostgreSQL checks. See [operations](operations.md) for reload/readiness details and [current evidence](evidence/local-readiness-verification.json) for exact execution results. The frozen research experiment remains model 24c8238525d4.

| Requirement | Code / intended evidence |
|---|---|
| Full historical period, no family removal | data.manifest, audit_frames, acquire; expected-archives.json |
| Checksums, schema, source/time metadata | data.fetch, parse, audit_frames |
| Future mutation invariance / swing confirmation | test_features.py |
| Completed 15m / 1h / 4h context | features.build_features; test_features.py |
| Availability joins and missing-input quarantine | data.join_external; audit/feature tests |
| Train-only selection, scaling, calibration | research.fit_model, splits; test_research.py |
| Common timestamps and untouched holdout | research.train, exclusive holdout lock |
| Costs, stale/weak forecasts, notional/risk budgets | engine.gate/reduce_event; test_engine.py |
| Long/short P&L, funding, fees, stops, margin | test_engine.py fixtures |
| Retry/restart consistency and rollback | store.apply_events; ledger tests |
| Worker serialization and no replay entries | worker locks; engine replay test |
| Read-only API, bounded MCP/service equality | test_services_mcp.py (official stdio client) |
| Browser market → prediction → research → portfolio | frontend/tests/dashboard.spec.ts, release.spec.ts and operational/workspace suites; 14 cases pass |
| Database migrations and free-host configuration | migrations, Dockerfile, render.yaml, workflows |
| Architecture, evaluation/model/coverage/MCP documents | docs/ |
| Measured research release | Complete; frozen holdout metrics and exact replay equality |
| Public URL and remote PostgreSQL | Deferred by owner; local PostgreSQL verified |
| Forward paper evidence | 36 actual forward decisions over about 8.88 hours; zero fills; sustained uptime remains unmeasured |
| Run monitoring and readiness | Persistent outcomes, HTTP 200/503 gates and CLI; code verified, service reload pending |
| Two-minute walkthrough | Refreshed six-scene recording: chart/storage, four views and current operational state; decode verification passed |
| Startup readiness | Seven Node cases pass; launcher distinguishes HTTP availability, outdated API and operational gates |
| Actual periodic persistence | Four observed saved snapshots; median receipt interval 5.638 seconds; WebSocket latency remains unverified |

