"""Step B: trade plans entered by pre-registered rules, run through the futures simulator."""
import numpy as np
import pandas as pd
import pytest

from agent_fixtures import START, frames
from quantpilot.research import agent as ar
from quantpilot.research import selective
from simulation.test_tradeplan import ACCOUNT, ctx


def test_trend_rule_needs_1h_and_4h_to_agree():
    up = ctx(support=99_000.0, resistance=104_000.0, trend=1.0)
    assert ar.decide(up, 'B1', ACCOUNT).side == 'long'
    mixed = up.model_copy(update=dict(timeframes={**up.timeframes, '4h': {**up.timeframes['4h'], 'trend': -1.0}}))
    assert ar.decide(mixed, 'B1', ACCOUNT) is None


def test_pullback_rule_buys_dips_in_an_uptrend_only():
    assert ar.decide(ctx(support=99_000.0, resistance=104_000.0, trend=1.0, rsi=35.0), 'B3', ACCOUNT).side == 'long'
    assert ar.decide(ctx(support=99_000.0, resistance=104_000.0, trend=1.0, rsi=55.0), 'B3', ACCOUNT) is None


def test_best_plan_rule_takes_the_valid_side_with_the_higher_reward_risk():
    # Long: target 104,000 (4%) vs stop 98,900; short: target 99,000 (1%) vs stop at 1.5 ATR -> long wins.
    plan = ar.decide(ctx(support=99_000.0, resistance=104_000.0), 'B2', ACCOUNT)
    assert plan.side == 'long' and plan.valid


def test_control_rule_is_a_reproducible_coin_flip():
    c = ctx()      # no nearby structure: 1.5-ATR stops and range targets make both sides valid
    a = [getattr(ar.decide(c, 'B0', ACCOUNT, rng=rng), 'side', None) for rng in [np.random.default_rng(0)]*6]
    b = [getattr(ar.decide(c, 'B0', ACCOUNT, rng=rng), 'side', None) for rng in [np.random.default_rng(0)]*6]
    assert a == b and len(set(a)) == 2


def test_rules_never_return_an_invalid_plan():
    assert ar.decide(ctx(support=99_000.0, resistance=100_300.0), 'B1', ACCOUNT) is None     # reward:risk too low
    strict = ar.decide(ctx(support=99_000.0, resistance=102_200.0), 'B1', ACCOUNT, min_reward_risk=2.0)
    assert strict is None and ar.decide(ctx(support=99_000.0, resistance=102_200.0), 'B1', ACCOUNT) is not None


@pytest.fixture(scope='module')
def synthetic():
    data = frames(days=40, seed=3, drift=.0002)                    # steady uptrend
    contexts = ar.decision_contexts(data, START+pd.Timedelta(days=10), START+pd.Timedelta(days=40), warmup_days=10)
    return data, contexts


def test_decisions_are_taken_only_at_hourly_closes(synthetic):
    _, contexts = synthetic
    assert (contexts.index.minute == 0).all() and contexts.index.min() >= START+pd.Timedelta(days=10)


def test_a_run_fills_after_the_decision_and_records_trades(synthetic):
    data, contexts = synthetic
    result = ar.run(data, contexts, 'B1', 1.5, macro_events=[])
    trades = result['trades']
    assert trades, 'an uptrend with structure should produce trend trades'
    for trade in trades:
        assert pd.Timestamp(trade['entry_time']).minute % 60 == 5             # next 5m bar after an hourly close
        assert trade['reason'] in {'STOP', 'TARGET', 'TIME_EXIT', 'DAILY_LOSS_HALT', 'DRAWDOWN_HALT', 'LIQUIDATION'}
    assert result['state']['version'] == 'futures-v1'


def test_no_entries_inside_a_macro_blackout(synthetic):
    data, contexts = synthetic
    free = ar.run(data, contexts, 'B1', 1.5, macro_events=[])['trades']
    first = pd.Timestamp(free[0]['entry_time'])-pd.Timedelta(minutes=5)              # the decision time
    blackout = [dict(kind='cpi', label='CPI', scheduled_at=(first+pd.Timedelta(minutes=10)).isoformat())]
    blocked = ar.run(data, contexts, 'B1', 1.5, macro_events=blackout)['trades']
    assert all(pd.Timestamp(t['entry_time']) != pd.Timestamp(free[0]['entry_time']) for t in blocked)


def test_trade_returns_are_net_of_costs_and_relative_to_notional():
    trades = [dict(entry_time='2026-01-01T00:05:00+00:00', exit_time='2026-01-01T03:00:00+00:00', side=1,
                   quantity=.01, entry=100_000.0, net=-12.0, reason='STOP')]
    calls = ar.trade_calls(trades)
    assert calls.net.iloc[0] == pytest.approx(-12.0/1000.0) and calls.hit.iloc[0] == 0


def test_the_registry_counts_earlier_intraday_trials(tmp_path):
    prior = tmp_path/'prior.jsonl'
    for k in range(3):
        selective.register(prior, 'intraday', 'I1', dict(k=k), dict(sharpe=.01*k, calls=50))
    registry = ar.seed_registry(tmp_path/'run'/'trials.jsonl', [prior])
    selective.register(registry, 'intraday', 'TP', dict(rule='B1'), dict(sharpe=.2, calls=60))
    assert selective.trial_count(registry, 'intraday') == 4
