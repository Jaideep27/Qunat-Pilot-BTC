# Two-minute recruiter walkthrough

The refreshed recording includes the PostgreSQL chart workspace, five-year candle coverage, current database observations and the operational monitor state. [The runtime snapshot](walkthrough/runtime-snapshot.json) records the actual local inspection used for the captions. The running monitor still requires a service reload; the recording displays that state explicitly.

[Watch the captioned walkthrough](walkthrough/quantpilot-btc.webm). The two-minute video uses snapshots captured from the actual local application, with explanatory captions and no voiceover. It shows frozen research model **24c8238525d4**, zero holdout trades, measured local checks and actual forward no-trade decisions. The exchange socket is intentionally disabled during recording, so it does not demonstrate continuous live trading or actual WebSocket latency.

The recording metadata and scene captions are stored in [recording.json](walkthrough/recording.json). Reproduce with API on port 8000 and dashboard on 5173:

```powershell
node scripts/release/record_walkthrough.cjs
node scripts/verify/verify_walkthrough.cjs
```

The script uses installed Chrome and its built-in MediaRecorder. It reads actual local status, visits the chart workspace and four dashboard views, and records six captioned scenes. It waits for PostgreSQL storage counters and research plots to load. Captions use the current decision, collector configuration, ledger evidence and verification files. Existing successful MCP SDK/API comparison evidence is tied to the same frozen model version. It does not invent stream messages, tool responses or fills. Scene PNGs, hashes, timestamps and recording metadata accompany the video.

| Time | View | Narrative |
|---|---|---|
| 0:00-0:20 | Chart workspace | Five-year PostgreSQL coverage, timeframes, studies and drawing controls; stored-price recording disclosed |
| 0:20-0:35 | Market | Price, volume, positioning, sentiment and actual observation times |
| 0:35-0:55 | Prediction | Current policy outcome, reason codes and model associations |
| 0:55-1:15 | Research coverage | All 24 months, 209,983 eligible observations, 106 features and 62 affected positioning rows quarantined |
| 1:15-1:35 | Research outcomes | Untouched holdout AUC 0.5388, Brier 0.2511, balanced accuracy 50.65%; zero strategy trades |
| 1:35-2:00 | Paper and verification | Actual forward decisions/equity, pending monitor reload, 88 Python and 14 browser cases, nine SDK tool/API comparisons and verified database recovery |

Additional evidence: [architecture](architecture.md), [verification](verification.md), [MCP setup](mcp-setup.md), [evaluation](evaluation-report.md), [model card](model-card.md).

Resume wording supported by the verified local work:

> Built a Bitcoin ML research platform using 209,983 observations and 106 features, with calibrated walk-forward evaluation, cost-aware perpetual-futures simulation, a React/FastAPI dashboard and nine MCP tools verified against shared API services.

Public deployment is deferred. Local PostgreSQL, periodic persistence and database recovery have actual execution evidence. Instrumented run history still needs activation, sustained uptime remains unmeasured, and actual browser WebSocket latency is unverified here. The measured research results do not establish profitable live trading.
