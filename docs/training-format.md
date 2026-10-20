# Historical ML training audit

Model **24c8238525d4** is an actual fitted, sigmoid-calibrated XGBoost classifier. The experiment evaluated a constant-probability baseline, logistic regression and XGBoost across four feature groups and 14 expanding walk-forward folds per group. The released model forecasts the probability of a positive close return over the next 60 minutes.

The research dataset covers October 2024 through September 2026, with preceding indicator warm-up. It contains 209,983 common eligible five-minute cutoffs and 106 candidate features. The five-year PostgreSQL price store is separate and has not been used for a five-year version of this full-feature experiment.

## Input and target format

Each row is a unique, chronological UTC prediction cutoff after a five-minute candle completes. Inputs are numeric features from completed candles, completed higher-timeframe context and external observations available at that cutoff. The label is stored separately: 1 when the close 60 minutes later is higher, otherwise 0. No target or future-return column is included among model inputs.

The audit independently looks up the close at the cutoff plus one elapsed hour to verify label alignment. It also compares the saved scaler's row count, column names, means and population variances against training rows, and the saved feature-selector scores against training-only labels.

## Verified final release split

| Purpose | First cutoff, UTC | Last cutoff, UTC | Rows |
|---|---|---|---:|
| Fit scaler, feature selector and classifier | 2024-10-01 00:00 | 2026-05-31 22:55 | 174,848 |
| Fit sigmoid probability calibration | 2026-06-01 00:00 | 2026-07-01 10:55 | 8,772 |
| Select policy threshold | 2026-07-01 12:00 | 2026-07-31 22:55 | 8,772 |
| Held out from fitting and selection | 2026-08-01 00:00 | 2026-09-30 22:55 | 17,555 |

The calibration/validation boundary is the time midpoint of the two-month pre-holdout window, so it falls at noon on July 1. Sixty-minute purges separate the windows. The dataset's eligible total also includes boundary rows deliberately omitted from these partitions.

Candidate algorithms are selected using walk-forward validation Brier scores. Scaling and univariate feature selection fit on training data only. Probabilities are calibrated on a later, separate window. Conditional return magnitudes use training rows only. Policy confidence thresholds are chosen on validation net returns after costs; the released threshold is 0.60. Neither the model nor threshold is selected using holdout results.

The released selector retained 40 technical features. It selected zero derivatives-specific and zero sentiment-specific features. These families remain available in the catalogue and dashboard, but the current experiment does not establish an incremental predictive benefit from them. Full, technical and derivatives-group holdout results are identical in the saved evaluation.

## Verification and performance

[Machine-readable audit](evidence/training-format-verification.json) records the checks, split counts, selected feature names, dataset/model hashes and reproduced metrics. All 13 focused research/feature tests passed, including future-row mutation, completed-timeframe alignment and purged-window checks.

An isolated sigmoid-calibration replica was fitted around the already-frozen base pipeline using only the designated calibration rows. Its probabilities on validation rows match the released model. The saved scaler, selector, classifier, original calibrator, model files and policy threshold were not changed. Holdout probabilities were reproduced to check the existing report; no tuning used those outcomes.

The reproduced holdout ROC-AUC is **0.5388**, Brier score **0.2511**, and balanced accuracy **50.65%**. The frozen trading policy produced **zero holdout trades**. Predictive performance is modest and does not establish profitability.

Positioning gaps remain subject to the documented authorized quarantine policy. Full five-year derivatives/sentiment coverage has not been established. This release provides next-hour direction probabilities; separate price-level and multi-horizon forecast models have not been trained.

Reproduce the audit against trusted frozen local artifacts:

```powershell
uv run pytest tests/test_research.py tests/test_features.py -q --junitxml=.verification/training-format-pytest.xml
uv run python scripts/verify_training.py
```

See the [model card](model-card.md), [evaluation report](evaluation-report.md) and [coverage report](coverage-report.md).
