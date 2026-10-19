# Historical coverage and quarantine report

Status: **ELIGIBLE FOR RESEARCH WITH EXPLICIT QUARANTINE**. Strict completeness remains false.

The user delegated the coverage decision and requested autonomous completion. The accepted policy preserves the full October 2024 through September 2026 research period, 40 preceding warm-up days, every mandatory feature family and the original observations. It allows at most 0.1% affected native positioning rows. Structural, provenance, checksum, and other-family coverage failures still block release.

All 874 original Binance archives and two supplemental daily repair archives have verified cached SHA256 hashes. Daily files recovered the 288 missing mark and 288 missing index observations with no conflicts. These families now pass their automated checks.

## Remaining raw source defects

Positioning has five missing timestamps and 57 rows with invalid values: 62 source rows out of 221,760 expected warm-up/research observations (0.02796%). Five timestamps are absent: 28 October 2024 at 16:25 and 16:30 UTC, and 29 August 2025 at 06:20, 06:25 and 06:30 UTC. Invalid rows span 23 January, 3 February, 15 April, 21 July and 22 July 2025. Exact ranges and per-column counts remain in [the audit JSON](coverage-report.json).

## Quarantine and common eligibility

Missing positioning observations become explicit missing feature inputs, never older substituted values. A bad positioning observation invalidates its cutoff and the next 12 five-minute bars used by the feature lookback. Earlier features cannot change. No source values are invented or interpolated.

- 106 feature columns are computed.
- All four experiments share **209,983 eligible timestamps**, present in all 24 months.
- Of 210,240 expected research cutoffs, 245 are excluded for invalid features and 12 reserved at the final label/purge boundary.
- Positioning/lookback invalidity affects 177 of those excluded cutoffs. Other exclusions include undefined calculations around zero-volume/zero-range observations.
- The final two months remain reserved as a holdout; no missing-data decision uses predictive or trading results.

[Data-quality summary](data-quality.json) records monthly eligibility. [Excluded windows](excluded-windows.csv) records each rejected cutoff and its missing features. The model and policy must be selected on training/validation data, then frozen before holdout evaluation.

## Reproduce

Run `uv run quantpilot audit --as-of 2026-10-18 --offline` to verify cached originals, supplemental evidence and Parquet hashes under the stored policy. Run `uv run quantpilot train --as-of 2026-10-18` once per named experiment; the holdout lock prevents accidental repeated evaluations. Full local provenance is retained in `data/coverage.json` and source ZIPs in `data/raw`.
