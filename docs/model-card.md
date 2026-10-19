# Model card

**Released local research model:** 24c8238525d4; algorithm **xgboost**, sigmoid-calibrated for learned models. Published feature group: full technical, derivatives and sentiment catalogue. Confidence threshold: **0.60**, frozen using validation results.

**Intended use:** BTCUSDT USD-M perpetual research and cost-aware simulated decisions. No real-money order execution.

**Target:** positive versus non-positive 60-minute forward close return, sampled at five-minute prediction cutoffs. Zero belongs to the non-positive class.

**Data:** 24 complete calendar months, 40-day preceding warm-up, 209,983 common eligible timestamps and 106 feature columns. The user-authorized quarantine policy preserves the period and all feature families while excluding incomplete positioning histories. Raw gaps remain disclosed. See [coverage report](coverage-report.md).

**Training:** expanding walk-forward evaluation; train-only scaling and univariate feature selection up to 40 inputs; separate sigmoid calibration; 60-minute purges; model selection by validation Brier score; cost-aware validation threshold selection. Final two months are held out. The full group is predetermined for publication; holdout comparisons do not select a different release model.

**Artifact audit:** the final fitted scaler/classifier use 174,848 training rows. The selector retained 40 technical features, zero derivatives-specific features and zero sentiment-specific features. An independent historical-data check reproduces training-only preprocessing, the calibration probabilities and the reported holdout metrics. All 13 focused training/feature tests pass. Exact UTC split boundaries, row counts and audit details are in [training format](training-format.md) and its [machine-readable evidence](training-format-verification.json). The five-year price store has not been used for a five-year full-feature training experiment.

**Measured full-group holdout:** Brier 0.2511; ROC-AUC 0.5388; balanced accuracy 50.65%; simulated net return 0.00%; maximum drawdown 0.00%; closed trades 0. See [evaluation report](evaluation-report.md) for all groups and benchmarks.

**Explanations:** standardized coefficient-times-value associations for logistic regression, SHAP raw-model associations for XGBoost, and a fixed-prior explanation for the constant baseline. Contributions are before probability calibration and do not establish causation.

**Identity:** seed 42; source commit `uncommitted`; source SHA256 `12eee9f0740bf5a2837b945877f2eac52d983944b7ab52a325fa7a5e84625128`. Dataset hashes, package versions, parameters, feature schema and costs are embedded in trusted local artifacts. Do not load joblib files from uploads or public inputs.

**Limitations:** regime change; overlapping labels at five-minute sampling; correlated features; assumed historical publication delays; sparse source gaps and quarantined windows; bar-based execution and drawdown sampling; approximate margin; no order-book reconstruction. Historical performance does not measure forward paper uptime or live execution. A stale or incomplete current observation must produce no trade.
