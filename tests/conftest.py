import numpy as np
import pandas as pd
import pytest
from quantpilot.config import settings
from quantpilot.storage.database import database, initialize

@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "artifact_dir", tmp_path/"artifacts")
    monkeypatch.setattr(settings, "database_url", "sqlite:///"+str(tmp_path/"test.db"))
    monkeypatch.setattr(settings, "market_database_url", None)
    engine = database()
    initialize(engine)
    return engine

@pytest.fixture
def candles():
    rng = np.random.default_rng(42)
    n = 3000
    events = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = 40000*np.exp(np.cumsum(rng.normal(0,.001,n)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(dict(event_time=events, available_at=events+pd.Timedelta(minutes=5),
        open=open_, high=np.maximum(open_,close)*1.001, low=np.minimum(open_,close)*.999,
        close=close, volume=rng.uniform(100,500,n), trade_count=1000,
        taker_buy_volume=rng.uniform(20,80,n), source="fixture", received_at=events+pd.Timedelta(days=1)))

@pytest.fixture
def frames(candles):
    meta = candles[["event_time","available_at","source","received_at"]]
    return dict(candles=candles,
        mark=meta.assign(mark_price=candles.close*1.0001),
        index=meta.assign(index_price=candles.close),
        positioning=meta.assign(open_interest=np.linspace(10000,11000,len(meta)),
            global_ratio=1.1,top_account_ratio=1.2,top_position_ratio=1.05),
        funding=meta.iloc[::96].assign(funding_rate=.0001),
        sentiment=meta.iloc[::288].assign(fear_greed=50.0))

