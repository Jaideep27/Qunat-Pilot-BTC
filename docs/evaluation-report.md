# Measured evaluation report

Frozen experiment: **24c8238525d4**. Research period: 2024-10-01 00:00:00+00:00 through 2026-10-01 00:00:00+00:00. Holdout begins **2026-08-01 00:00:00+00:00**.

All four feature groups share **209,983 eligible timestamps**, drawn from all 24 calendar months. There are 106 catalogue columns. The final two months were reserved from model/threshold selection. Publication uses the full feature group by design, not the best holdout result. Models are selected by mean validation Brier score; confidence thresholds are selected using validation net returns after costs.

## Untouched holdout comparison

| Features | Selected model | Brier | ROC-AUC | Balanced accuracy | Net return | Max drawdown | Closed trades |
|---|---|---:|---:|---:|---:|---:|---:|
| price | xgboost | 0.2514 | 0.5311 | 50.06% | 0.00% | 0.00% | 0 |
| technical | xgboost | 0.2511 | 0.5388 | 50.65% | 0.00% | 0.00% | 0 |
| derivatives | xgboost | 0.2511 | 0.5388 | 50.65% | 0.00% | 0.00% | 0 |
| full | xgboost | 0.2511 | 0.5388 | 50.65% | 0.00% | 0.00% | 0 |

Returns are simulated and net of modeled fees, slippage and realized funding. Probability metrics and trading outcomes answer different questions; a low Brier score does not establish a profitable policy. Profitability is not a release requirement.

## Holdout benchmarks

| Strategy | Net return | Max drawdown |
|---|---:|---:|
| no_trade | 0.00% | 0.00% |
| buy_hold | 31.44% | -8.89% |
| moving_average | -56.82% | -56.82% |

The passive comparator is fully collateralized BTC perpetual exposure, with entry/exit costs and actual funding. The moving-average comparator uses the shared active risk/accounting engine. No-trade holds cash.

## Full-group walk-forward policy evidence

Each expanding fold fits candidates on past data, calibrates separately, selects its model and threshold using validation only, then simulates its subsequent test month. A 60-minute purge separates boundaries.

| Test month start | Selected model | Confidence threshold | Net return | Max drawdown | Closed trades |
|---|---|---:|---:|---:|---:|
| 2025-06-01 | logistic | 0.60 | 0.00% | 0.00% | 0 |
| 2025-07-01 | constant | 0.60 | 0.00% | 0.00% | 0 |
| 2025-08-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2025-09-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2025-10-01 | logistic | 0.60 | -0.25% | -0.25% | 1 |
| 2025-11-01 | logistic | 0.60 | 0.00% | 0.00% | 0 |
| 2025-12-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2026-01-01 | constant | 0.60 | 0.00% | 0.00% | 0 |
| 2026-02-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2026-03-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2026-04-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2026-05-01 | xgboost | 0.60 | 0.00% | 0.00% | 0 |
| 2026-06-01 | logistic | 0.60 | 0.00% | 0.00% | 0 |
| 2026-07-01 | logistic | 0.60 | 0.00% | 0.00% | 0 |

## Full-group holdout calibration

| Mean predicted positive-return probability | Observed positive-return frequency |
|---|---:|
| 0.3929 | 0.5064 |
| 0.4592 | 0.5025 |
| 0.5142 | 0.6007 |

## Coverage and exclusions

Strict source completeness is false. The user-authorized policy quarantines 62 affected positioning rows and dependent feature windows without interpolation. All families and calendar months are retained. There are 177 positioning/lookback exclusions; total research exclusions are 257 including the final label/purge boundary. Exact invalid-feature windows are listed in [excluded-windows.csv](excluded-windows.csv); monthly eligibility is in [data-quality.json](evidence/data-quality.json).

## Costs, identity and limits

Starting balance 10,000 USDT; maximum active notional 25% of equity; risk budget 0.25%; stop 1.5 ATR; maximum hold 60 minutes; daily-loss entry halt 2%; 5 bps fees and 3 bps spread/slippage per side; 5 bps additional edge buffer. Funding enters predictions only after its observed availability and settles in accounting at its actual timestamp.

Dataset hashes, package versions, seed 42, feature schema, costs and code SHA256 **12eee9f0740bf5a2837b945877f2eac52d983944b7ab52a325fa7a5e84625128** are embedded in the evaluation artifact. The source commit is `uncommitted`. The trusted local artifacts contain full fold metrics, fills, sampled curves, frozen thresholds and models.

This is historical simulation. Bar-based stop fills, assumed historical availability, simplified isolated margin and deployment scheduling remain approximations. Drawdown statistics use five-minute equity observations; plotted curves are hourly samples. Forward paper uptime and execution evidence have not yet accumulated.
