"""A11 Combined Bot rules: the regime picks leaders, the daily trend vetoes, a second opinion is required."""
import pandas as pd
import pytest

from quantpilot.strategies import combined

T = pd.Timestamp('2026-11-01 10:00', tz='UTC')


def cand(source, side='long', rr=1.5, t=T, ref=100.0):
    stop = ref*.99 if side == 'long' else ref*1.01
    return dict(source=source, side=side, time=t, ref=ref, stop=stop, target=ref*(1+.015) if side == 'long' else ref*.985,
                reward_risk=rr, max_hold_hours=12, limit=None, valid_minutes=None, reason=source)


ROW = dict(close=100.0, atr_1h=1.0, support_1h=95.0, support_4h=90.0, resistance_1h=105.0, resistance_4h=110.0)


def test_families_and_roles():
    assert combined.family('PB08L') == 'PB08' and combined.family('R1') == 'R1'
    assert combined.leads('TREND_UP', 'PB08S') and combined.leads('TREND_UP', 'T1') and not combined.leads('TREND_UP', 'R1')
    assert combined.leads('RANGE', 'R1') and combined.leads('RANGE', 'PB10L') and not combined.leads('RANGE', 'PB08L')
    assert combined.leads('HIGH_VOL', 'PB01') and not combined.leads('HIGH_VOL', 'B1')


def test_a_leader_needs_a_second_opinion():
    out = combined.decide('RANGE', 0, [cand('R1')], [], ROW)
    assert out['chosen'] is None and out['considered'][0]['status'] == 'NO_SECOND_OPINION'
    out = combined.decide('RANGE', 0, [cand('R1'), cand('PB10L')], [], ROW)
    assert out['chosen']['source'] in ('R1', 'PB10L') and out['chosen']['agreements']


def test_a_recent_signal_from_another_source_counts_but_the_same_family_does_not():
    recent = [cand('PB09L', t=T-pd.Timedelta(minutes=30))]
    assert combined.decide('RANGE', 0, [cand('R1')], recent, ROW)['chosen']['source'] == 'R1'
    same = [cand('PB10L', t=T-pd.Timedelta(minutes=30))]
    assert combined.decide('RANGE', 0, [cand('PB10S', side='long')], same, ROW)['chosen'] is None
    old = [cand('PB09L', t=T-pd.Timedelta(minutes=90))]
    assert combined.decide('RANGE', 0, [cand('R1')], old, ROW)['chosen'] is None   # older than the 4 closes before


def test_a_level_counts_as_a_second_opinion():
    near = dict(ROW, support_1h=99.6)            # 0.4 ATR below the close
    out = combined.decide('RANGE', 0, [cand('R1')], [], near)
    assert out['chosen']['source'] == 'R1' and 'support_1h' in out['chosen']['agreements']
    short = dict(ROW, resistance_4h=100.3)
    assert combined.decide('RANGE', 0, [cand('R1', side='short')], [], short)['chosen']['side'] == 'short'
    assert combined.decide('RANGE', 0, [cand('R1', side='short')], [], near)['chosen'] is None   # support does not back a short


def test_the_daily_trend_vetoes_counter_trend_trades_and_is_not_a_second_opinion():
    both = [cand('PB08S', side='short'), cand('B1', side='short')]
    out = combined.decide('TREND_DOWN', 1, both, [], ROW)
    assert out['chosen'] is None and {c['status'] for c in out['considered']} == {'VETOED'}
    assert combined.decide('TREND_DOWN', -1, both, [], ROW)['chosen'] is not None
    assert combined.decide('TREND_UP', 1, [cand('PB08L')], [], ROW)['chosen'] is None    # vote agrees, still alone


def test_non_leaders_only_confirm():
    out = combined.decide('TREND_UP', 0, [cand('R1'), cand('PB08L')], [], ROW)
    assert out['chosen']['source'] == 'PB08L'
    assert next(c for c in out['considered'] if c['source'] == 'R1')['status'] == 'NOT_LEADER'


def test_ties_go_to_more_opinions_then_leader_order_then_reward_risk():
    three = [cand('PB08L', rr=1.2), cand('B1', rr=3.0), cand('PB04', rr=2.0)]
    out = combined.decide('TREND_UP', 0, three, [], ROW)
    assert out['chosen']['source'] == 'PB04'          # PB04 comes before PB08 and B1 in the trend leader order
    near = dict(ROW, support_1h=99.6)
    out = combined.decide('TREND_UP', 0, three, [], near)
    assert len(out['chosen']['agreements']) == 3 and out['chosen']['source'] == 'PB04'


def test_idle_money_parks_after_a_flat_day_with_positive_funding():
    assert combined.park(flat_since=T-pd.Timedelta(hours=25), now=T, rates=[.0001]*21) is True
    assert combined.park(flat_since=T-pd.Timedelta(hours=23), now=T, rates=[.0001]*21) is False
    assert combined.park(flat_since=T-pd.Timedelta(hours=25), now=T, rates=[-.0001]*21) is False
    assert combined.unpark(rates=[.0003]*12+[-.0001]*9) is True and combined.unpark(rates=[.0001]*21) is False


def test_the_plain_reason_names_the_leaders_and_the_veto():
    on, reason = combined.active('TREND_UP', 1)
    assert on and 'trend' in reason.lower() and 'shorts' in reason.lower()
    on, reason = combined.active('RANGE', 0)
    assert on and 'sideways' in reason.lower()
