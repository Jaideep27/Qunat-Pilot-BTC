"""Audit a trusted frozen model against historical data without fitting or tuning it."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import joblib
import numpy as np
import pandas as pd
from sklearn.feature_selection import f_classif
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import balanced_accuracy_score,brier_score_loss,roc_auc_score
from quantpilot.config import settings
from quantpilot.data import load_frames,dataset_hashes,utc
from quantpilot.features import build_features,feature_groups
from quantpilot.research import PURGE


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    artifacts=settings.artifact_dir
    bundle=joblib.load(artifacts/'released.joblib')
    metadata=bundle['metadata']
    assert dataset_hashes(settings.data_dir)==metadata['dataset_hashes'],'Frozen dataset identity differs'
    print('Frozen dataset hashes match; computing historical features...',flush=True)
    frames=load_frames(settings.data_dir)
    features,labels,data=build_features(frames['candles'],frames)
    assert list(features.columns)==metadata['feature_schema']
    assert not {'target','forward_return'}.intersection(bundle['columns'])
    start,end=utc(metadata['start']),utc(metadata['end'])
    eligible=features.dropna().index.intersection(labels.dropna().index)
    eligible=eligible[(eligible>=start)&(eligible<end-PURGE)]
    assert len(eligible)==metadata['eligible_rows']
    assert str(eligible.tz)=='UTC' and eligible.is_monotonic_increasing and eligible.is_unique
    # Independent timestamp lookup checks that the label means elapsed 60 minutes,
    # rather than merely assuming twelve rows always represent an hour.
    expected=data.close.reindex(eligible+pd.Timedelta(hours=1)).to_numpy()/data.close.loc[eligible].to_numpy()-1
    np.testing.assert_allclose(labels.loc[eligible,'forward_return'].to_numpy(),expected)
    np.testing.assert_array_equal(labels.loc[eligible,'target'].to_numpy(),(expected>0).astype(float))
    holdout=end-pd.DateOffset(months=2)
    calibration_start=holdout-pd.DateOffset(months=2)
    validation_start=calibration_start+(holdout-calibration_start)/2
    split=dict(train=eligible[eligible<calibration_start-PURGE],
        calibration=eligible[(eligible>=calibration_start)&(eligible<validation_start-PURGE)],
        validation=eligible[(eligible>=validation_start)&(eligible<holdout-PURGE)],
        holdout=eligible[eligible>=holdout])
    for left,right in zip(list(split.values())[:-1],list(split.values())[1:]):
        assert not len(left.intersection(right))
        assert left.max()+PURGE<right.min()
    calibrated=bundle['model'].calibrated_classifiers_[0].estimator
    pipeline=getattr(calibrated,'estimator',calibrated)
    x=features.loc[split['train'],bundle['columns']]
    scaler=pipeline['scale'];selector=pipeline['select']
    assert int(scaler.n_samples_seen_)==len(x)
    assert list(scaler.feature_names_in_)==bundle['columns']
    np.testing.assert_allclose(scaler.mean_,x.mean().to_numpy(),rtol=1e-10,atol=1e-10)
    np.testing.assert_allclose(scaler.var_,x.var(ddof=0).to_numpy(),rtol=1e-9,atol=1e-10)
    scores,_=f_classif(scaler.transform(x),labels.loc[split['train'],'target'])
    np.testing.assert_allclose(selector.scores_,scores,rtol=1e-8,atol=1e-8,equal_nan=True)
    selected=list(np.asarray(bundle['columns'])[selector.get_support()])
    print('Training-only preprocessing matches; verifying the separate calibration window...',flush=True)
    # Fit an isolated calibration replica around the already-frozen base pipeline.
    # This does not fit the scaler, selector or classifier, alter the saved model,
    # or use validation/holdout labels for fitting.
    replica=CalibratedClassifierCV(FrozenEstimator(pipeline),method='sigmoid')
    replica.fit(features.loc[split['calibration'],bundle['columns']],labels.loc[split['calibration'],'target'])
    validation_x=features.loc[split['validation'],bundle['columns']]
    np.testing.assert_allclose(replica.predict_proba(validation_x),bundle['model'].predict_proba(validation_x),rtol=1e-10,atol=1e-10)
    # A deterministic verification of an already frozen holdout, with no fitting
    # or model/threshold selection from these observations.
    probabilities=bundle['model'].predict_proba(features.loc[split['holdout'],bundle['columns']])[:,1]
    truth=labels.loc[split['holdout'],'target']
    actual=dict(brier=float(brier_score_loss(truth,probabilities)),roc_auc=float(roc_auc_score(truth,probabilities)),
        balanced_accuracy=float(balanced_accuracy_score(truth,probabilities>=.5)))
    evaluation=json.loads((artifacts/'evaluation.json').read_text(encoding='utf-8'))
    full=next(item for item in evaluation['comparisons'] if item['group']=='full')
    for key,value in actual.items():np.testing.assert_allclose(value,full['holdout'][key],rtol=1e-10,atol=1e-10)
    assert full['threshold']==bundle['threshold']
    assert digest(artifacts/'released.joblib')==digest('release/released.joblib')
    tests=ET.parse('.verification/training-format-pytest.xml').getroot().find('testsuite')
    assert tests is not None and tests.attrib['failures']==tests.attrib['errors']=='0'
    groups=feature_groups(features.columns)
    derivative_only=set(groups['derivatives'])-set(groups['technical'])
    sentiment_only=set(groups['full'])-set(groups['derivatives'])
    report=dict(status='verified',checked_at=datetime.now(timezone.utc).isoformat(),
        model_version=metadata['version'],algorithm=bundle['kind'],calibration=bundle['model'].method,
        forecast='Probability of positive versus non-positive close return over the next 60 minutes',
        row_format='One UTC completed five-minute cutoff per row; numeric historical features; target stored separately',
        research_start=start.isoformat(),research_end_exclusive=end.isoformat(),eligible_dataset_rows=len(eligible),
        catalogue_features=features.shape[1],selected_features=selected,
        selected_by_family=dict(technical=sum(name in groups['technical'] for name in selected),
            derivatives=sum(name in derivative_only for name in selected),sentiment=sum(name in sentiment_only for name in selected)),
        split={name:dict(rows=len(index),first=index.min().isoformat(),last=index.max().isoformat()) for name,index in split.items()},
        purge_minutes=60,threshold=bundle['threshold'],seed=metadata['seed'],
        checks=['Frozen dataset SHA256 identity','Chronological unique UTC cutoffs','Elapsed-hour target alignment',
            'No target columns in model inputs','Disjoint purged fitting/calibration/validation/holdout windows',
            'Saved scaler row count, feature names, means and variances match training only',
            'Saved feature-selection scores match training-only labels',
            'Isolated calibration replica reproduces saved probabilities using calibration rows only','Frozen holdout probability metrics reproduce',
            'Released/exported model bytes match'],
        holdout_metrics=actual,holdout_trades=full['trading']['trades'],focused_tests_passed=int(tests.attrib['tests']),
        released_model_modified_or_retuned_by_this_audit=False,
        base_model_retrained_by_this_audit=False,isolated_calibration_replica_fitted_for_verification=True,
        five_year_history_used_for_this_experiment=False,
        limitations=['Research spans 24 months; five-year storage coverage applies to price candles',
            'Source positioning gaps use the documented authorized quarantine policy',
            'Current forecast is direction classification; no separate price-level or multi-horizon model is trained',
            'Modest holdout performance and zero holdout trades do not establish profitability'],
        dataset_sha256=metadata['dataset_hashes'],model_sha256=digest(artifacts/'released.joblib'),
        source_sha256={path:digest(path) for path in ('src/quantpilot/research.py','src/quantpilot/features.py','scripts/verify_training.py')})
    Path('docs/training-format-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:report[key] for key in ('status','model_version','split','selected_by_family','holdout_metrics','focused_tests_passed')},indent=2))


if __name__=='__main__':main()
