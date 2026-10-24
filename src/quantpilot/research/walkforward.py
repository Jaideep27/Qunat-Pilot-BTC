"""Expanding walk-forward experiments with purged boundaries and one-shot final holdout."""
from __future__ import annotations
import hashlib
from importlib.metadata import version as package_version
import json
import subprocess
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.dummy import DummyClassifier
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.frozen import FrozenEstimator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
from quantpilot.config import RiskConfig
from quantpilot.data.archives import audit_frames, dataset_hashes, load_frames, utc
from quantpilot.features.core import build_features, feature_groups
from quantpilot.simulation.engine import backtest, performance

SEED = 42
PURGE = pd.Timedelta(hours=1)

def metrics(y, p):
    fraction, predicted = calibration_curve(y, p, n_bins=10, strategy="uniform")
    return dict(balanced_accuracy=float(balanced_accuracy_score(y, p >= .5)),
                roc_auc=float(roc_auc_score(y, p)) if len(set(y)) > 1 else None,
                brier=float(brier_score_loss(y, p)),
                calibration=[dict(predicted=float(a), observed=float(b)) for a,b in zip(predicted, fraction)])

def fit_model(kind, x, y, cal_x, cal_y):
    if len(set(y)) < 2 or len(set(cal_y)) < 2: raise ValueError("each fitting window needs both classes")
    if kind == "constant":
        model = DummyClassifier(strategy="prior").fit(x, y)
        return model
    model = LogisticRegression(max_iter=1000, random_state=SEED) if kind == "logistic" else XGBClassifier(
        n_estimators=150, max_depth=3, learning_rate=.04, subsample=.8, colsample_bytree=.8,
        reg_lambda=5, random_state=SEED, n_jobs=2, eval_metric="logloss")
    # Feature selector is fit ONLY on training labels, together with scaler.
    selector = SelectKBest(f_classif, k=min(40, x.shape[1]))
    pipe = Pipeline([("scale", StandardScaler()), ("select", selector), ("model", model)])
    pipe.fit(x, y)
    calibrated = CalibratedClassifierCV(FrozenEstimator(pipe), method="sigmoid")
    calibrated.fit(cal_x, cal_y)
    return calibrated

def splits(index, start, end):
    holdout = end-pd.DateOffset(months=2)
    test_start = start+pd.DateOffset(months=8)
    while test_start+pd.DateOffset(months=1) <= holdout:
        val_start, test_end = test_start-pd.DateOffset(months=1), test_start+pd.DateOffset(months=1)
        midpoint = val_start+(test_start-val_start)/2
        yield dict(train=index[(index >= start) & (index < val_start-PURGE)],
                   calibration=index[(index >= val_start) & (index < midpoint-PURGE)],
                   validation=index[(index >= midpoint) & (index < test_start-PURGE)],
                   test=index[(index >= test_start) & (index < test_end-PURGE)])
        test_start = test_end

def magnitudes(labels, index):
    r = labels.loc[index, "forward_return"]
    return float(r[r>0].mean()), float(-r[r<=0].mean())

def forecasts(probabilities, features, data, up, down, version):
    result = {}
    for t, p in probabilities.items():
        result[t] = dict(probability=float(p), up_magnitude=up, down_magnitude=down,
                         atr=float(features.loc[t, "atr"]), observed_at=t.isoformat(),
                         model_version=version, complete=True, funding_observable=True,
                         funding_rate=float(data.loc[t, "funding_rate"]))
    return result

def strategy(probabilities, features, data, funding, up, down, threshold=.6):
    if probabilities.empty: raise ValueError("empty evaluation window")
    c = RiskConfig(threshold=threshold)
    # Reserve the purged tail for delayed entry and the last trade\'s full holding period.
    delay_bars = max(1, int(np.ceil(c.execution_delay_seconds/300)))
    # A policy with a longer custom holding limit must still stop at the purged split boundary.
    tail_minutes = min(c.max_hold_minutes+5*delay_bars, int(PURGE.total_seconds()/60))
    execution_end = probabilities.index.max()+pd.Timedelta(minutes=tail_minutes)
    candles = data.loc[probabilities.index.min():execution_end].reset_index()
    state = backtest(candles, forecasts(probabilities, features, data, up, down, "research"), funding, c)
    # Keep sampled curves compact; trades remain complete.
    curve = state["curve"][::12]
    if state["curve"] and (not curve or curve[-1] != state["curve"][-1]): curve.append(state["curve"][-1])
    return {**performance(state), "curve":curve, "fills":state["fills"]}

def explanations(model, x, kind):
    if kind == "constant": return dict(method="constant prior", contributions=[])
    pipe = model.calibrated_classifiers_[0].estimator
    pipe = getattr(pipe, "estimator", pipe)
    scale, selector, estimator = pipe["scale"], pipe["select"], pipe["model"]
    selected = list(x.columns[selector.get_support()])
    transformed = selector.transform(scale.transform(x.tail(1)))
    if kind == "logistic":
        values = transformed[0]*estimator.coef_[0]
        method = "standardized coefficient × value; raw model log-odds associations before calibration"
    else:
        import shap
        values = np.asarray(shap.TreeExplainer(estimator).shap_values(transformed))[0]
        method = "SHAP raw model associations before calibration"
    ranked = sorted(zip(selected, values), key=lambda pair: abs(pair[1]), reverse=True)[:12]
    return dict(method=method, contributions=[dict(feature=k, value=float(v)) for k,v in ranked])

def train(root: Path, artifacts: Path, as_of=None):
    frames = load_frames(root)
    # Audit again; a stale/edited JSON flag alone can never authorize release.
    acquisition = json.loads((root / "coverage.json").read_text(encoding="utf-8-sig")) if (root / "coverage.json").exists() else {}
    report = audit_frames(frames, as_of, acquisition.get("coverage_policy"))
    hashes = dataset_hashes(root)
    if not report["release_ready"] or not acquisition.get("release_ready") or hashes != acquisition.get("dataset_hashes"):
        raise ValueError("MODEL_RELEASE_BLOCKED: coverage or dataset hashes failed; run quantpilot audit")
    artifacts.mkdir(parents=True, exist_ok=True)
    holdout_marker = artifacts / "holdout.lock"
    # Exclusive creation stops accidental repeated holdout tuning in this experiment directory.
    with holdout_marker.open("x", encoding="utf-8") as f:
        f.write("Reserved. Do not tune from holdout; a failed run requires documented recovery.\n")
    features, labels, data = build_features(frames["candles"], frames)
    start, end = utc(report["start"]), utc(report["end_exclusive"])
    eligible = features.dropna().index.intersection(labels.dropna().index)
    eligible = eligible[(eligible >= start) & (eligible < end-PURGE)]
    quality = dict(coverage_policy=report.get("coverage_policy"), quarantine=report.get("quarantine"),
        expected_research_rows=int((end-start)/pd.Timedelta(minutes=5)), eligible_rows=len(eligible),
        excluded_rows=int((end-start)/pd.Timedelta(minutes=5))-len(eligible),
        positioning_lookback_exclusions=int(features.loc[(features.index>=start)&(features.index<end-PURGE),
            ["open_interest","global_ratio","top_account_ratio","top_position_ratio","oi_change","price_oi"]].isna().any(axis=1).sum()),
        eligible_by_month={str(month):int(count) for month,count in pd.Series(1,index=eligible).groupby(eligible.strftime("%Y-%m")).sum().items()})
    (artifacts / "data-quality.json").write_text(json.dumps(quality,indent=2),encoding="utf-8")
    rejected = features.loc[(features.index>=start)&(features.index<end-PURGE)]
    rejected = rejected[rejected.isna().any(axis=1)]
    pd.DataFrame({"cutoff":rejected.index, "missing_features":rejected.apply(
        lambda row: ";".join(row.index[row.isna()]),axis=1).values}).to_csv(artifacts/"excluded-windows.csv",index=False)
    groups = feature_groups(features.columns)
    all_results, models = [], {}
    holdout_start = end-pd.DateOffset(months=2)
    final_val = holdout_start-pd.DateOffset(months=2)
    midpoint = final_val+(holdout_start-final_val)/2
    final = dict(train=eligible[eligible < final_val-PURGE],
                 calibration=eligible[(eligible >= final_val) & (eligible < midpoint-PURGE)],
                 validation=eligible[(eligible >= midpoint) & (eligible < holdout_start-PURGE)],
                 test=eligible[eligible >= holdout_start])
    for group, columns in groups.items():
        x = features[columns]
        scores = {kind:[] for kind in ("constant", "logistic", "xgboost")}
        folds, policy_folds = [], []
        for fold_number, fold in enumerate(splits(eligible, start, end), 1):
            print(f"Training {group}: walk-forward fold {fold_number}, {fold['test'].min()}", flush=True)
            if min(map(len, fold.values())) < 100: raise ValueError("insufficient common timestamps")
            fold_models = {}
            for kind in scores:
                model = fit_model(kind, x.loc[fold["train"]], labels.loc[fold["train"], "target"],
                                  x.loc[fold["calibration"]], labels.loc[fold["calibration"], "target"])
                val_p = model.predict_proba(x.loc[fold["validation"]])[:,1]
                score = brier_score_loss(labels.loc[fold["validation"], "target"], val_p)
                scores[kind].append(float(score))
                fold_models[kind] = model
                p = model.predict_proba(x.loc[fold["test"]])[:,1]
                folds.append(dict(kind=kind, test_start=str(fold["test"].min()),
                                  test_end=str(fold["test"].max()), validation_brier=float(score),
                                  **metrics(labels.loc[fold["test"], "target"], p)))
            fold_kind = min(scores, key=lambda kind: scores[kind][-1])
            fold_model = fold_models[fold_kind]
            fold_up, fold_down = magnitudes(labels, fold["train"])
            fold_vp = pd.Series(fold_model.predict_proba(x.loc[fold["validation"]])[:,1], index=fold["validation"])
            fold_policies = {t:strategy(fold_vp, features, data, frames["funding"], fold_up, fold_down, t) for t in (.60,.65,.70,.75)}
            fold_threshold = max(fold_policies, key=lambda t: fold_policies[t]["net_return"])
            fold_tp = pd.Series(fold_model.predict_proba(x.loc[fold["test"]])[:,1], index=fold["test"])
            tested = strategy(fold_tp, features, data, frames["funding"], fold_up, fold_down, fold_threshold)
            policy_folds.append(dict(test_start=str(fold["test"].min()), test_end=str(fold["test"].max()),
                                     kind=fold_kind, threshold=fold_threshold, **tested))
        selected = min(scores, key=lambda kind: np.mean(scores[kind]))
        model = fit_model(selected, x.loc[final["train"]], labels.loc[final["train"],"target"],
                          x.loc[final["calibration"]], labels.loc[final["calibration"],"target"])
        up, down = magnitudes(labels, final["train"])
        vp = pd.Series(model.predict_proba(x.loc[final["validation"]])[:,1], index=final["validation"])
        candidates = {t:strategy(vp, features, data, frames["funding"], up, down, t) for t in (.60,.65,.70,.75)}
        threshold = max(candidates, key=lambda t: candidates[t]["net_return"])
        # Frozen before any holdout access.
        frozen = dict(kind=selected, columns=columns, threshold=threshold, up=up, down=down)
        (artifacts / f"{group}-frozen.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        p = pd.Series(model.predict_proba(x.loc[final["test"]])[:,1], index=final["test"])
        result = dict(group=group, kind=selected, threshold=threshold, walk_forward=folds, walk_forward_policy=policy_folds,
                      mean_validation_brier={k:float(np.mean(v)) for k,v in scores.items()},
                      holdout=metrics(labels.loc[final["test"],"target"], p),
                      trading=strategy(p, features, data, frames["funding"], up, down, threshold),
                      explanation=explanations(model, x.loc[final["test"]], selected))
        all_results.append(result)
        bundle = dict(model=model, **frozen)
        joblib.dump(bundle, artifacts / f"{group}.joblib")
        models[group] = bundle
    # Passive funding and drawdown share the same chronological accounting implementation.
    from quantpilot.simulation.benchmarks import buy_and_hold
    end_of_test = min(data.index.max(), final["test"].max()+PURGE)
    hold = data.loc[final["test"].min():end_of_test].reset_index()
    passive = buy_and_hold(hold, frames["funding"])
    ma_p = ((features.loc[final["test"],"ema_21_distance"] < features.loc[final["test"],"ema_50_distance"]).astype(float))
    ma = strategy(ma_p, features, data, frames["funding"], up, down, .60)
    try:
        commit = subprocess.check_output(["git","rev-parse","HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception: commit = "uncommitted"
    code_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    packages = {name:package_version(name) for name in ("numpy", "pandas", "pyarrow", "scikit-learn", "xgboost", "shap", "joblib")}
    version = hashlib.sha256(json.dumps({"datasets":hashes, "code":code_hash, "packages":packages, "risk":RiskConfig().model_dump(), "seed":SEED}, sort_keys=True).encode()).hexdigest()[:12]
    metadata = dict(version=version, package_versions=packages, seed=SEED, commit=commit, code_sha256=code_hash, dataset_hashes=hashes,
                    model_parameters={group:{k:repr(v) for k,v in bundle["model"].get_params().items()} for group,bundle in models.items()},
                    feature_schema=list(features.columns), eligible_rows=len(eligible), start=str(start),
                    end=str(end), holdout_start=str(holdout_start), risk=RiskConfig().model_dump(),
                    availability_assumptions=report["availability_assumptions"], data_quality=quality)
    output = dict(status="evaluated", metadata=metadata, comparisons=all_results,
                  benchmarks={"no_trade":{"net_return":0, "max_drawdown":0},
                              "buy_hold":passive,
                              "moving_average":ma})
    (artifacts / "evaluation.json").write_text(json.dumps(output, indent=2, allow_nan=False), encoding="utf-8")
    # Final bundle includes metadata so inference can verify the input schema and dataset identity.
    joblib.dump({**models["full"], "metadata":metadata}, artifacts / "released.joblib")
    return output

