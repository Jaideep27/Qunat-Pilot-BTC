import pandas as pd
import pytest
from quantpilot.config import RiskConfig
from quantpilot.simulation.engine import initial, gate, reduce_event, close_position
from quantpilot.storage.database import apply_events, portfolio, LedgerEvent
from sqlalchemy.orm import Session

T = "2024-01-01T00:00:00+00:00"
def f(p=.9, **kwargs):
    return dict(probability=p, up_magnitude=.03, down_magnitude=.02, atr=1,
                observed_at=T, model_version="fixture", complete=True, funding_observable=True, **kwargs)

def enter(side=1, config=None):
    c = config or RiskConfig(fee_bps=0, slippage_bps=0)
    s = initial(c)
    s = reduce_event(s, dict(kind="quote",time=T,price=100))
    s = reduce_event(s, dict(kind="signal",time=T,forecast=f(.9 if side == 1 else .1)))
    assert not s["position"]
    s = reduce_event(s, dict(kind="quote",time="2024-01-01T00:00:02+00:00",price=100))
    assert s["position"]
    return s,c

@pytest.mark.parametrize("side",[1,-1])
def test_long_short_pnl_and_settlement(side):
    s,c = enter(side)
    qty = s["position"]["quantity"]
    s = reduce_event(s,dict(kind="funding",time="2024-01-01T00:01:00+00:00",mark=100,rate=.001))
    assert s["funding_total"] == pytest.approx(-side*qty*.1)
    close_position(s,110,"2024-01-01T00:02:00+00:00","TEST",c)
    assert s["cash"] == pytest.approx(10000+side*qty*10-side*qty*.1)

def test_fees_slippage_and_exposure():
    s,c = enter(config=RiskConfig())
    p = s["position"]
    assert p["entry"] == pytest.approx(100.03)
    assert s["fees"] == pytest.approx(p["quantity"]*p["entry"]*.0005)
    assert p["quantity"]*p["entry"] <= 2500
    before=s["cash"]
    close_position(s,100,T,"TEST",c)
    assert s["cash"] < before
    assert s["cash"] == pytest.approx(10000+s["realized_pnl"]-s["fees"]+s["funding_total"])

@pytest.mark.parametrize("side,open_,low,high",[(1,95,94,96),(-1,105,104,106)])
def test_gap_stop_fills_adversely(side,open_,low,high):
    s,c=enter(side)
    qty=s["position"]["quantity"]
    s=reduce_event(s,dict(kind="bar",time="2024-01-01T00:05:00+00:00",price=open_,open=open_,low=low,high=high))
    assert s["position"] is None
    assert s["fills"][-1]["reason"] == "STOP"
    assert s["fills"][-1]["price"] == open_
    assert s["realized_pnl"] == pytest.approx(-5*qty)

def test_holding_limit_and_replayed_entries():
    s,c=enter()
    s=reduce_event(s,dict(kind="quote",time="2024-01-01T01:00:03+00:00",price=101,replayed=True))
    assert s["position"] is None
    assert s["fills"][-1]["reason"] == "TIME_EXIT"
    assert s["fills"][-1]["replayed"]
    s=initial(c)
    s=reduce_event(s,dict(kind="quote",time=T,price=100))
    s=reduce_event(s,dict(kind="signal",time=T,forecast=f()))
    s=reduce_event(s,dict(kind="quote",time="2024-01-01T00:00:02+00:00",price=100,replayed=True))
    assert not s["position"]
    assert s["decisions"][-1]["reasons"] == ["REPLAY_ENTRY_FORBIDDEN"]

def test_cost_staleness_loss_missing_and_frozen_threshold():
    s=initial()
    c=RiskConfig()
    assert gate(s,f(),100,T,c)["action"] == "LONG"
    assert "INSUFFICIENT_EDGE" in gate(s,f(),100,T,RiskConfig(fee_bps=500))["reasons"]
    assert "STALE_DATA" in gate(s,f(),100,"2024-01-01T01:00:00Z",c)["reasons"]
    missing=f();missing["complete"]=False
    assert "MISSING_CRITICAL_INPUT" in gate(s,missing,100,T,c)["reasons"]
    s["cash"]=9700
    assert "DAILY_LOSS_LIMIT" in gate(s,f(),100,T,c)["reasons"]
    s=initial()
    assert "WEAK_FORECAST" in gate(s,f(.65,threshold=.7),100,T,c)["reasons"]
    with pytest.raises(ValueError): RiskConfig(max_exposure=2)

def test_transactional_retries_restart_and_rollback(account):
    events=[dict(id="q",kind="quote",time=T,price=100),
            dict(id="s",kind="signal",time=T,forecast=f()),
            dict(id="e",kind="quote",time="2024-01-01T00:00:02+00:00",price=100)]
    first=apply_events(account,events)
    assert apply_events(account,events) == first
    assert portfolio(account) == first
    with Session(account) as session: assert session.query(LedgerEvent).count() == 3
    with pytest.raises(ValueError):
        apply_events(account,[dict(id="later",kind="quote",time="2024-01-01T00:00:03+00:00",price=101),
                              dict(id="bad",kind="unknown",time="2024-01-01T00:00:04+00:00")])
    assert portfolio(account) == first
    with Session(account) as session: assert session.get(LedgerEvent,"later") is None

def test_margin_approximation_closes_short():
    s,c=enter(-1)
    s=reduce_event(s,dict(kind="quote",time="2024-01-01T00:05:00+00:00",price=210,mark=210))
    assert s["position"] is None
    assert s["fills"][-1]["reason"] == "MARGIN_APPROXIMATION"
    assert s["cash"] >= 10000-2500-1e-8

def test_pause_clears_pending_and_still_exits():
    s,c=enter()
    s=reduce_event(s,dict(kind="control",time="2024-01-01T00:01:00+00:00",paused=True))
    assert s["paused"]
    s=reduce_event(s,dict(kind="bar",time="2024-01-01T00:05:00+00:00",price=95,open=95,high=96,low=94))
    assert s["position"] is None


def test_extreme_short_stop_gap_cannot_borrow_free_collateral():
    s,c=enter(-1)
    collateral=s['position']['collateral']
    s=reduce_event(s,dict(kind='bar',time='2024-01-01T00:05:00+00:00',price=100,mark=100,
                          open=1000,high=1000,low=99))
    assert s['position'] is None
    assert s['cash'] == pytest.approx(10000-collateral)
    assert s['fills'][-1]['margin_adjustment'] > 0
    assert s['fills'][-1]['reason'] == 'MARGIN_APPROXIMATION'

def test_owner_pause_preserves_unprocessed_market_history(account):
    events = [
        dict(id="cursor-quote", kind="quote", time=T, price=100),
        dict(id="cursor-signal", kind="signal", time=T, forecast=f()),
        dict(id="cursor-entry", kind="quote", time="2024-01-01T00:00:02+00:00", price=100),
    ]
    opened = apply_events(account, events)
    paused = apply_events(account, [dict(id="pause-later", kind="control",
        time="2024-01-01T00:12:00+00:00", paused=True)])
    assert paused["last_event"] == opened["last_event"]
    assert paused["last_control_at"] == "2024-01-01T00:12:00+00:00"
    reconciled = apply_events(account, [
        dict(id="missed-funding", kind="funding", time="2024-01-01T00:01:00+00:00", mark=100, rate=.001, replayed=True),
        dict(id="missed-stop", kind="bar", time="2024-01-01T00:05:00+00:00",
             price=95, open=95, low=94, high=96, replayed=True),
    ])
    assert reconciled["paused"]
    assert reconciled["position"] is None
    assert reconciled["funding_total"] < 0
    assert reconciled["fills"][-1]["replayed"]
    assert apply_events(account, [dict(id="pause-later", kind="control",
        time="2024-01-01T00:12:00+00:00", paused=True)]) == reconciled


def test_owner_control_cannot_reset_daily_accounting_across_midnight():
    s, _ = enter()
    before = (s["last_event"], s["day"], s["day_start"])
    s = reduce_event(s, dict(kind="control", time="2024-01-02T00:01:00+00:00", paused=True))
    assert (s["last_event"], s["day"], s["day_start"]) == before
    with pytest.raises(ValueError, match="owner control"):
        reduce_event(s, dict(kind="control", time="2024-01-01T23:59:00+00:00", paused=False))


def test_future_funding_cannot_change_an_earlier_backtest():
    from quantpilot.simulation.engine import backtest, market_events
    bars = pd.DataFrame({
        "event_time": pd.date_range(T, periods=3, freq="5min"),
        "available_at": pd.date_range("2024-01-01T00:05:00+00:00", periods=3, freq="5min"),
        "open":100., "high":100.1, "low":99.9, "close":100.,
    })
    funding = pd.DataFrame({
        "event_time":pd.to_datetime(["2024-01-01T08:00:00Z", "2024-02-01T00:00:00Z"]),
        "funding_rate":[.5, -.5],
    })
    forecast=f()
    forecast["observed_at"]="2024-01-01T00:05:00+00:00"
    signals={forecast["observed_at"]:forecast}
    clean=backtest(bars,signals,config=RiskConfig(fee_bps=0,slippage_bps=0))
    with_future=backtest(bars,signals,funding,config=RiskConfig(fee_bps=0,slippage_bps=0))
    assert with_future == clean
    assert all(e["kind"] != "funding" for e in market_events(bars,funding))


def test_settlement_is_applied_before_an_exit_at_the_same_timestamp():
    from quantpilot.simulation.engine import EVENT_PRIORITY
    s,c=enter()
    quantity=s["position"]["quantity"]
    events=[
        dict(kind="quote",time="2024-01-01T01:00:02+00:00",price=100),
        dict(kind="funding",time="2024-01-01T01:00:02+00:00",mark=100,rate=.001),
    ]
    for event in sorted(events,key=lambda e:EVENT_PRIORITY[e["kind"]]):
        s=reduce_event(s,event,c)
    assert s["position"] is None
    assert s["funding_total"] == pytest.approx(-quantity*.1)
    assert s["fills"][-1]["reason"] == "TIME_EXIT"

