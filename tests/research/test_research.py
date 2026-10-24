import numpy as np
import pandas as pd
import pytest
from quantpilot.research.walkforward import splits, fit_model, train, PURGE

def test_expanding_folds_purge_and_holdout():
    index=pd.date_range("2024-01-01","2026-01-01",freq="5min",tz="UTC")
    folds=list(splits(index,index[0],index[-1]))
    assert len(folds)>5
    for f in folds:
        assert f["train"].max()+PURGE < f["calibration"].min()
        assert f["calibration"].max()+PURGE < f["validation"].min()
        assert f["validation"].max()+PURGE < f["test"].min()
        assert f["test"].max() < index[-1]-pd.DateOffset(months=2)
    assert len(folds[-1]["train"])>len(folds[0]["train"])

def test_future_values_do_not_change_fitted_preprocessing_or_past_predictions():
    rng=np.random.default_rng(42)
    x=pd.DataFrame(rng.normal(size=(300,4)),columns=list("abcd"))
    y=(x.a+x.b>0).astype(int)
    model=fit_model("logistic",x.iloc[:100],y.iloc[:100],x.iloc[110:180],y.iloc[110:180])
    frozen=model.calibrated_classifiers_[0].estimator
    pipe=getattr(frozen,"estimator",frozen)
    np.testing.assert_allclose(pipe["scale"].mean_,x.iloc[:100].mean())
    before=model.predict_proba(x.iloc[180:220])
    x.iloc[220:]=1e8
    np.testing.assert_allclose(before,model.predict_proba(x.iloc[180:220]))
    assert np.isfinite(model.predict_proba(x.iloc[180:220])).all()

def test_training_refuses_unverified_history(tmp_path):
    with pytest.raises(ValueError, match="MODEL_RELEASE_BLOCKED"):
        train(tmp_path,tmp_path/"artifacts","2026-10-18")
    assert not (tmp_path/"artifacts"/"released.joblib").exists()


def test_policy_window_contains_exit_tail_but_cannot_reach_future_test_prices(candles, monkeypatch):
    from quantpilot.research import walkforward as research
    from quantpilot.simulation.engine import initial
    data=candles.copy()
    data.index=pd.DatetimeIndex(data.available_at, name="cutoff")
    data["funding_rate"]=0.0
    features=pd.DataFrame({"atr":1.0},index=data.index)
    probabilities=pd.Series(.9,index=data.index[100:105])
    captured={}
    def fake_backtest(bars, forecasts, funding, config):
        captured["end"]=bars.available_at.max()
        captured["forecasts"]=forecasts
        return initial(config)
    monkeypatch.setattr(research,"backtest",fake_backtest)
    research.strategy(probabilities,features,data,None,.03,.02)
    assert captured["end"] == probabilities.index[-1]+pd.Timedelta(minutes=60)
    assert max(captured["forecasts"]) == probabilities.index[-1]
    monkeypatch.setenv("QUANTPILOT_RISK_MAX_HOLD_MINUTES","1440")
    research.strategy(probabilities,features,data,None,.03,.02)
    assert captured["end"] == probabilities.index[-1]+pd.Timedelta(minutes=60)

