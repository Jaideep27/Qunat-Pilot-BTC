import pandas as pd
import pytest
from quantpilot.simulation.benchmarks import buy_and_hold
from quantpilot.config import RiskConfig


def flat_bars():
    times=pd.date_range("2024-01-01",periods=4,freq="5min",tz="UTC")
    return pd.DataFrame(dict(event_time=times,available_at=times+pd.Timedelta(minutes=5),
                             open=100., high=100.1, low=99.9, close=100., mark_price=100.))


def test_passive_costs_equal_exact_entry_and_exit_fills():
    c=RiskConfig()
    result=buy_and_hold(flat_bars(),config=c)
    expected=(1-.0003)*(1-.0005)/((1+.0003)*(1+.0005))-1
    assert result["net_return"] == pytest.approx(expected)
    assert result["max_drawdown"] == pytest.approx(expected)
    assert result["trades"] == 1
    assert result["fees"] > 0


def test_passive_drawdown_includes_each_settlement_and_ignores_future_settlements():
    funding=pd.DataFrame(dict(event_time=pd.to_datetime([
        "2024-01-01T00:05:00Z","2024-01-01T00:10:00Z",
        "2024-01-01T00:15:00Z","2024-01-02T00:00:00Z"]),
        funding_rate=[.9,.01,-.02,.9]))
    result=buy_and_hold(flat_bars(),funding,RiskConfig(fee_bps=0,slippage_bps=0))
    assert result["funding"] == pytest.approx(100.)
    assert result["net_return"] == pytest.approx(.01)
    assert result["max_drawdown"] == pytest.approx(-.01)
    assert len(result["fills"]) == 2

