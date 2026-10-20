"""Deterministic trade plans: structural stops and targets, net reward:risk, sizing and blocks."""
import math

import pytest

from quantpilot import tradeplan
from quantpilot.context import MarketContext

ATR = 400.0
NOW = '2026-01-20T12:00:00+00:00'


def ctx(price=100_000.0, support=None, resistance=None, support_4h=None, resistance_4h=None, day=(None, None),
        week=(None, None), vol=.04, funding=.0001, blackout=False, blocking=(), forecasts=None, **tf1h):
    tf = dict(rsi=50.0, adx=20.0, atr=ATR, ema21_dist=.5, ema50_dist=1.0, macd_hist=.1, trend=1.0) | tf1h
    level = lambda s, r: dict(support=s, resistance=r, support_atr=None, resistance_atr=None)
    return MarketContext(
        cutoff=NOW, price=price, mark=price, index=price, premium=0.0, volume=100.0, relative_volume=1.0,
        taker_share_1h=.5, open_interest=90000.0, oi_change_1h=0.0, oi_change_24h=0.0, global_ratio=1.5,
        top_account_ratio=1.2, top_position_ratio=1.1, funding_rate=funding, volatility_24h=vol,
        timeframes={'5m': tf, '15m': tf, '1h': tf, '4h': dict(tf)},
        levels={'15m': level(None, None), '1h': level(support, resistance), '4h': level(support_4h, resistance_4h),
                'prior_day': dict(high=day[0], low=day[1]), 'prior_week': dict(high=week[0], low=week[1])},
        forecasts=forecasts or {}, macro=dict(blackout=blackout, next=None, last=None), news=dict(count_24h=0, latest=[]),
        account=None, availability={}, blocking=list(blocking))


ACCOUNT = dict(equity=10_000.0, cash=10_000.0, position=None, halted=False, paused=False)
COST = tradeplan.ROUND_TRIP


def test_long_stop_sits_below_support_with_a_quarter_atr_buffer():
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0), 'long', ACCOUNT)
    assert plan.stop == pytest.approx(99_000.0-.25*ATR) and plan.target == pytest.approx(104_000.0)
    assert plan.rejections == []


def test_short_is_the_mirror_image():
    plan = tradeplan.plan(ctx(support=96_000.0, resistance=101_000.0), 'short', ACCOUNT)
    assert plan.stop == pytest.approx(101_000.0+.25*ATR) and plan.target == pytest.approx(96_000.0)
    assert plan.rejections == []


def test_a_stop_closer_than_1_5_atr_is_pushed_out_and_one_wider_than_4_atr_is_rejected():
    near = tradeplan.plan(ctx(support=99_900.0, resistance=104_000.0), 'long', ACCOUNT)
    assert near.stop == pytest.approx(100_000.0-1.5*ATR)
    wide = tradeplan.plan(ctx(support=98_000.0, resistance=110_000.0), 'long', ACCOUNT)
    assert 'STOP_TOO_WIDE' in wide.rejections


def test_without_protective_structure_the_stop_is_1_5_atr():
    plan = tradeplan.plan(ctx(resistance=104_000.0), 'long', ACCOUNT)
    assert plan.stop == pytest.approx(100_000.0-1.5*ATR)


def test_nearest_level_wins_across_timeframes_and_prior_periods():
    plan = tradeplan.plan(ctx(support=98_500.0, support_4h=99_200.0, resistance=106_000.0, day=(103_500.0, 97_000.0)),
                          'long', ACCOUNT)
    assert plan.stop == pytest.approx(99_200.0-.25*ATR) and plan.target == pytest.approx(103_500.0)


def test_without_opposing_structure_the_target_is_the_24h_range_boundary():
    plan = tradeplan.plan(ctx(support=99_000.0, vol=.03), 'long', ACCOUNT)
    assert plan.target == pytest.approx(100_000.0*math.exp(1.2816*.03), rel=1e-4)
    assert plan.range['source'] == 'volatility'


def test_fresh_shadow_forecast_ranges_take_precedence_and_cap_targets():
    forecasts = {'24h': dict(age_minutes=10, return_q10=-.02, return_q90=.025)}
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0, forecasts=forecasts), 'long', ACCOUNT)
    assert plan.range['source'] == 'shadow_forecast' and 'TARGET_OUTSIDE_RANGE' in plan.rejections


def test_reward_risk_is_net_of_costs_and_paid_funding():
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=101_200.0, funding=.0002), 'long', ACCOUNT)
    costs = COST+3*.0002
    expected = (.012-costs)/((100_000-(99_000-.25*ATR))/100_000+costs)
    assert plan.reward_risk == pytest.approx(expected) and 'REWARD_RISK_BELOW_MIN' in plan.rejections
    short = tradeplan.plan(ctx(support=98_800.0, resistance=101_000.0, funding=.0002), 'short', ACCOUNT)
    assert short.cost_fraction == pytest.approx(COST)                       # shorts receive positive funding


def test_size_risks_a_quarter_percent_at_both_balances_and_respects_exchange_minimums():
    for equity in (10_000.0, 1_000.0):
        account = dict(ACCOUNT, equity=equity, cash=equity)
        plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0), 'long', account)
        loss = plan.quantity*((plan.entry-plan.stop)+plan.entry*plan.cost_fraction)
        assert loss <= .0025*equity+1e-9 and plan.quantity == pytest.approx(round(plan.quantity, 3))
        assert plan.margin == pytest.approx(plan.notional/2)
    tiny = tradeplan.plan(ctx(support=96_500.0, resistance=110_000.0, vol=.2), 'long', dict(ACCOUNT, equity=300.0, cash=300.0))
    assert 'BELOW_EXCHANGE_MINIMUM' in tiny.rejections


def test_notional_is_capped_at_equity():
    plan = tradeplan.plan(ctx(resistance=104_000.0, atr=10.0), 'long', ACCOUNT)     # 15-dollar stop: size hits the cap
    assert plan.notional == pytest.approx(ACCOUNT['equity'])   # uncapped size would be about 0.122 BTC


def test_entries_are_blocked_in_blackouts_with_missing_inputs_open_positions_or_halts():
    base = dict(support=99_000.0, resistance=104_000.0)
    assert 'MACRO_BLACKOUT' in tradeplan.plan(ctx(blackout=True, **base), 'long', ACCOUNT).rejections
    assert 'MISSING_CRITICAL_INPUT' in tradeplan.plan(ctx(blocking=['mark'], **base), 'long', ACCOUNT).rejections
    assert 'POSITION_OPEN' in tradeplan.plan(ctx(**base), 'long', dict(ACCOUNT, position={'side': 1})).rejections
    assert 'ACCOUNT_HALTED' in tradeplan.plan(ctx(**base), 'long', dict(ACCOUNT, halted=True)).rejections


def test_liquidation_is_estimated_and_stops_beyond_it_are_rejected():
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0), 'long', ACCOUNT)
    assert plan.liquidation == pytest.approx(100_000.0*(1-1/2)/(1-.005))
    assert plan.stop > plan.liquidation


def test_revalidation_at_the_fill_rejects_an_invalidated_plan():
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0), 'long', ACCOUNT)
    assert tradeplan.revalidate(plan, 100_050.0) == []
    assert tradeplan.revalidate(plan, 103_500.0) == ['INVALIDATED_AT_FILL']      # most of the reward is gone
    assert tradeplan.revalidate(plan, 98_800.0) == ['INVALIDATED_AT_FILL']       # already through the stop


def test_evidence_lists_supporting_and_opposing_factors_without_deciding():
    plan = tradeplan.plan(ctx(support=99_000.0, resistance=104_000.0, rsi=78.0, trend=1.0), 'long', ACCOUNT)
    stances = {e['factor']: e['stance'] for e in plan.evidence}
    assert stances['trend_1h'] == 'supports' and stances['rsi_1h'] == 'opposes'
    assert plan.rejections == []
