# Documentation

**Start here**
- [Architecture](architecture.md): processes, one minute of the rule engine, packages and design rules.
- [Rule engine](rule-engine.md): triggers, the rulebook, layers, results and commands.
- [Project guide](QuantPilot-Project-Guide.md): a plain-language tour of the whole project, as of 2026-10-21.

**Research** (`research/`)
- [Pre-registration](research/preregistration.md): every hypothesis and amendment (A1–A9), written down before testing.
- Results:
  - [strategy research](research/strategy-research.md)
  - [forecast research](research/forecast-research.md)
  - [trading-agent research](research/trading-agent-research.md)
  - [event study](research/event-study.md)
  - [evaluation report](research/evaluation-report.md)
  - [model card](research/model-card.md)
- Data:
  - [data dictionary](research/data-dictionary.md)
  - [coverage report](research/coverage-report.md)
  - [training format](research/training-format.md)
- The [prediction plan](research/prediction-plan.md) that led to the selective-strategy study.

**Operations** (`operations/`)
- [Watchdog, alerts and backups](operations/operations.md), plus [recovery](operations/recovery.md).
- [Market platform](operations/market-platform.md), [live data](operations/live-data.md) and [deployment](operations/deployment.md).
- [MCP setup](operations/mcp-setup.md).

**Verification** (`verification/`)
- [Verification](verification/verification.md), [acceptance](verification/acceptance.md) and the [walkthrough](verification/walkthrough.md).
- Dated records: the [verification history](verification/verification-history.md), the [release history](verification/release-history.md) (the original build's README) and the machine-readable [evidence](evidence/).

**Design records** (`design/`)
- A dated spec and implementation plan for each feature: [specs](design/specs/) and [plans](design/plans/).
- The [project log](project-log.md) records what was built, when, and the results.
- The original [requirements report](requirements/QuantPilot_BTC_Requirements_Report_v0.1.docx).

Dated records describe the system as it was on their date. Module and script paths in them may predate the 2026-10-24 package layout (see the [refactor design](design/specs/2026-10-24-modular-refactor-design.md)).
