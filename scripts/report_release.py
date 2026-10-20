"""Generate human-readable release reports from frozen measured artifacts only."""
import json
from pathlib import Path
from quantpilot.config import settings


def pct(value): return f"{100*value:.2f}%"
def num(value): return "undefined" if value is None else f"{value:.4f}"


def main():
    evaluation=json.loads((settings.artifact_dir/"evaluation.json").read_text(encoding="utf-8"))
    meta=evaluation["metadata"]
    quality=meta["data_quality"]
    full=next(row for row in evaluation["comparisons"] if row["group"]=="full")
    rows=[]
    for row in evaluation["comparisons"]:
        m,t=row["holdout"],row["trading"]
        rows.append(f"| {row['group']} | {row['kind']} | {m['brier']:.4f} | {num(m['roc_auc'])} | {pct(m['balanced_accuracy'])} | {pct(t['net_return'])} | {pct(t['max_drawdown'])} | {t['trades']} |")
    benchmarks=[f"| {name} | {pct(result['net_return'])} | {pct(result['max_drawdown'])} |" for name,result in evaluation["benchmarks"].items()]
    policy=[f"| {row['test_start'][:10]} | {row['kind']} | {row['threshold']:.2f} | {pct(row['net_return'])} | {pct(row['max_drawdown'])} | {row['trades']} |" for row in full["walk_forward_policy"]]
    calibration=[f"| {row['predicted']:.4f} | {row['observed']:.4f} |" for row in full["holdout"]["calibration"]]
    text=f'''# Measured evaluation report

Frozen experiment: **{meta['version']}**. Research period: {meta['start']} through {meta['end']}. Holdout begins **{meta['holdout_start']}**.

All four feature groups share **{meta['eligible_rows']:,} eligible timestamps**, drawn from all 24 calendar months. There are {len(meta['feature_schema'])} catalogue columns. The final two months were reserved from model/threshold selection. Publication uses the full feature group by design, not the best holdout result. Models are selected by mean validation Brier score; confidence thresholds are selected using validation net returns after costs.

## Untouched holdout comparison

| Features | Selected model | Brier | ROC-AUC | Balanced accuracy | Net return | Max drawdown | Closed trades |
|---|---|---:|---:|---:|---:|---:|---:|
'''+'\n'.join(rows)+'''

Returns are simulated and net of modeled fees, slippage and realized funding. Probability metrics and trading outcomes answer different questions; a low Brier score does not establish a profitable policy. Profitability is not a release requirement.

## Holdout benchmarks

| Strategy | Net return | Max drawdown |
|---|---:|---:|
'''+'\n'.join(benchmarks)+'''

The passive comparator is fully collateralized BTC perpetual exposure, with entry/exit costs and actual funding. The moving-average comparator uses the shared active risk/accounting engine. No-trade holds cash.

## Full-group walk-forward policy evidence

Each expanding fold fits candidates on past data, calibrates separately, selects its model and threshold using validation only, then simulates its subsequent test month. A 60-minute purge separates boundaries.

| Test month start | Selected model | Confidence threshold | Net return | Max drawdown | Closed trades |
|---|---|---:|---:|---:|---:|
'''+'\n'.join(policy)+'''

## Full-group holdout calibration

| Mean predicted positive-return probability | Observed positive-return frequency |
|---|---:|
'''+'\n'.join(calibration)+f'''

## Coverage and exclusions

Strict source completeness is false. The user-authorized policy quarantines {quality['quarantine']['source_rows']} affected positioning rows and dependent feature windows without interpolation. All families and calendar months are retained. There are {quality['positioning_lookback_exclusions']} positioning/lookback exclusions; total research exclusions are {quality['excluded_rows']} including the final label/purge boundary. Exact invalid-feature windows are listed in [excluded-windows.csv](excluded-windows.csv); monthly eligibility is in [data-quality.json](evidence/data-quality.json).

## Costs, identity and limits

Starting balance 10,000 USDT; maximum active notional 25% of equity; risk budget 0.25%; stop 1.5 ATR; maximum hold 60 minutes; daily-loss entry halt 2%; 5 bps fees and 3 bps spread/slippage per side; 5 bps additional edge buffer. Funding enters predictions only after its observed availability and settles in accounting at its actual timestamp.

Dataset hashes, package versions, seed {meta['seed']}, feature schema, costs and code SHA256 **{meta['code_sha256']}** are embedded in the evaluation artifact. The source commit is `{meta['commit']}`. The trusted local artifacts contain full fold metrics, fills, sampled curves, frozen thresholds and models.

This is historical simulation. Bar-based stop fills, assumed historical availability, simplified isolated margin and deployment scheduling remain approximations. Drawdown statistics use five-minute equity observations; plotted curves are hourly samples. Forward paper uptime and execution evidence have not yet accumulated.
'''
    Path("docs/evaluation-report.md").write_text(text,encoding="utf-8")
    m=full["holdout"]
    card=f'''# Model card

**Released local research model:** {meta['version']}; algorithm **{full['kind']}**, sigmoid-calibrated for learned models. Published feature group: full technical, derivatives and sentiment catalogue. Confidence threshold: **{full['threshold']:.2f}**, frozen using validation results.

**Intended use:** BTCUSDT USD-M perpetual research and cost-aware simulated decisions. No real-money order execution.

**Target:** positive versus non-positive 60-minute forward close return, sampled at five-minute prediction cutoffs. Zero belongs to the non-positive class.

**Data:** 24 complete calendar months, 40-day preceding warm-up, {meta['eligible_rows']:,} common eligible timestamps and {len(meta['feature_schema'])} feature columns. The user-authorized quarantine policy preserves the period and all feature families while excluding incomplete positioning histories. Raw gaps remain disclosed. See [coverage report](coverage-report.md).

**Training:** expanding walk-forward evaluation; train-only scaling and univariate feature selection up to 40 inputs; separate sigmoid calibration; 60-minute purges; model selection by validation Brier score; cost-aware validation threshold selection. Final two months are held out. The full group is predetermined for publication; holdout comparisons do not select a different release model.

**Measured full-group holdout:** Brier {m['brier']:.4f}; ROC-AUC {num(m['roc_auc'])}; balanced accuracy {pct(m['balanced_accuracy'])}; simulated net return {pct(full['trading']['net_return'])}; maximum drawdown {pct(full['trading']['max_drawdown'])}; closed trades {full['trading']['trades']}. See [evaluation report](evaluation-report.md) for all groups and benchmarks.

**Explanations:** standardized coefficient-times-value associations for logistic regression, SHAP raw-model associations for XGBoost, and a fixed-prior explanation for the constant baseline. Contributions are before probability calibration and do not establish causation.

**Identity:** seed {meta['seed']}; source commit `{meta['commit']}`; source SHA256 `{meta['code_sha256']}`. Dataset hashes, package versions, parameters, feature schema and costs are embedded in trusted local artifacts. Do not load joblib files from uploads or public inputs.

**Limitations:** regime change; overlapping labels at five-minute sampling; correlated features; assumed historical publication delays; sparse source gaps and quarantined windows; bar-based execution and drawdown sampling; approximate margin; no order-book reconstruction. Historical performance does not measure forward paper uptime or live execution. A stale or incomplete current observation must produce no trade.
'''
    Path("docs/model-card.md").write_text(card,encoding="utf-8")
    print(json.dumps(dict(version=meta["version"],groups=len(rows),eligible_rows=meta["eligible_rows"])))


if __name__ == "__main__": main()
