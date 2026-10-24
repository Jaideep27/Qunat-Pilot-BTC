"""Deterministically replay an already frozen holdout policy; never fit or tune."""
import json
import joblib
import pandas as pd
from quantpilot.data.archives import load_frames, dataset_hashes, utc
from quantpilot.features.core import build_features
from quantpilot.research.walkforward import strategy

def replay(root, artifacts, group="full"):
    evaluation=json.loads((artifacts/"evaluation.json").read_text(encoding="utf-8-sig"))
    if dataset_hashes(root) != evaluation["metadata"]["dataset_hashes"]:
        raise ValueError("Dataset differs from the frozen experiment")
    bundle=joblib.load(artifacts/f"{group}.joblib")
    frames=load_frames(root)
    features,labels,data=build_features(frames["candles"],frames)
    eligible=features.dropna().index.intersection(labels.dropna().index)
    meta=evaluation["metadata"]
    test=eligible[(eligible >= utc(meta["holdout_start"])) & (eligible < utc(meta["end"])-pd.Timedelta(hours=1))]
    probabilities=pd.Series(bundle["model"].predict_proba(features.loc[test,bundle["columns"]])[:,1],index=test)
    result=strategy(probabilities,features,data,frames["funding"],bundle["up"],bundle["down"],bundle["threshold"])
    output=dict(status="frozen_policy_replay",group=group,model_version=meta["version"],trading=result)
    (artifacts/f"replay-{group}.json").write_text(json.dumps(output,indent=2,allow_nan=False),encoding="utf-8")
    return output

