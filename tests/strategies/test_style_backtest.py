"""A10 history test plumbing on small synthetic markets (the real run reads the development span only)."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.research import selective, style_backtest as sb
from style_fixtures import synthetic


def test_berdondini_keeps_only_identical_consecutive_pairs():
    out = selective.binomial_luck([1, 1, 0, 0, 1, 0, 1, 1])
    assert out['pairs'] == 3 and out['wins'] == 2
    assert out['p_value'] == pytest.approx(.5)              # P(X >= 2 | n=3, p=.5) = 4/8
    assert selective.binomial_luck([1]*40)['p_value'] < .01


def test_intraday_styles_trade_only_inside_the_span_and_report_the_pass_rule():
    candles = synthetic(days=75, seed=5)
    start = candles.event_time.iloc[0]+pd.Timedelta(days=45)
    end = candles.event_time.iloc[-1]
    for style in ('R1', 'B1'):
        result = sb.run_intraday(style, candles, None, start, end, macro_events=[])
        assert set(result) >= {'trades', 'account'}
        verdict = sb.judge(style, result, registries=[])
        assert set(verdict['criteria']) >= {'enough_trades', 'beats_break_even', 'positive_mean', 'not_luck'}
        assert verdict['trials'] == 4 and isinstance(verdict['passed'], bool)
        for t in result['trades']:
            assert start <= pd.Timestamp(t['entry_time']) <= end+pd.Timedelta(days=1)
        assert result['account']['final_equity'] > 0


def test_trend_backtest_on_daily_bars_goes_long_in_a_rise():
    idx = pd.date_range('2019-01-02', periods=500, freq='D', tz='UTC')
    rng = np.random.default_rng(2)
    close = 5000*np.exp(np.cumsum(rng.normal(.003, .02, 500)))
    daily = pd.DataFrame(dict(open=np.r_[close[0], close[:-1]], high=close*1.02, low=close*.98, close=close,
                              volume=1.0), index=idx)
    result = sb.run_trend(daily, pd.Series(.0003, index=idx), idx[150], idx[-1])
    assert result['trades'] and sum(t['side'] == 1 for t in result['trades']) >= len(result['trades'])/2
    assert result['account']['final_equity'] > 10_000


def test_carry_backtest_earns_positive_funding_and_stays_flat_when_negative():
    times = pd.date_range('2020-01-01', periods=3*120, freq='8h', tz='UTC')
    price = pd.Series(10_000*np.exp(np.cumsum(np.random.default_rng(1).normal(0, .01, len(times)))), index=times)
    good = sb.run_carry(pd.Series(.0002, index=times), price, times[0], times[-1])
    bad = sb.run_carry(pd.Series(-.0002, index=times), price, times[0], times[-1])
    assert good['account']['final_equity'] > 10_000 and good['account']['funding'] > 0
    assert bad['account']['final_equity'] == pytest.approx(10_000) and not bad['trades']


def test_judge_reads_mixed_timestamp_formats():
    trades = [dict(entry_time='2020-03-26T08:00:00+00:00', net=1.0, return_on_equity=.001),
              dict(entry_time='2020-03-26T08:00:00.002000+00:00', net=-1.0, return_on_equity=-.001)]
    verdict = sb.judge('C1', dict(trades=trades, account=dict(final_equity=10_000.0)), registries=[])
    assert verdict['report']['calls'] == 2


def combined_inputs(days=75, seed=7):
    candles = synthetic(days=days, seed=seed)
    start = candles.event_time.iloc[0]+pd.Timedelta(days=45)
    end = candles.event_time.iloc[-1]
    idx = pd.date_range(start-pd.Timedelta(days=200), end+pd.Timedelta(days=1), freq='D', tz='UTC')
    close = 50_000*np.exp(np.linspace(0, .6, len(idx)))
    daily = pd.DataFrame(dict(open=close, high=close*1.01, low=close*.99, close=close, volume=1.0), index=idx)
    times = pd.date_range(candles.event_time.iloc[0].ceil('8h'), end, freq='8h', tz='UTC')
    rates = pd.Series(.0001, index=times)
    t0 = start.ceil('15min')+pd.Timedelta(hours=6)
    price = float(candles.close[candles.available_at <= t0].iloc[-1])
    hits = pd.DataFrame([dict(playbook='PB08L', side='long', time=t0+pd.Timedelta(hours=24*k), tradeable=True,
                              plan_entry=price, plan_stop=price*.99, plan_target=price*1.02, plan_reward_risk=2.0)
                         for k in range(10)])
    return candles, daily, rates, hits, start, end


def test_the_combined_bot_trades_only_vetted_leaders_and_parks_idle_money():
    candles, daily, rates, hits, start, end = combined_inputs()
    stops = {'PB08L': 12}
    park = sb.run_combined(candles, daily, rates, hits, start, end, macro_events=[], stops=stops, park=True)
    flat = sb.run_combined(candles, daily, rates, hits, start, end, macro_events=[], stops=stops, park=False)
    assert set(park) >= {'trades', 'cycles', 'account', 'stats'}
    assert not flat['cycles'] and park['cycles']
    assert park['account']['funding'] > 0
    for t in park['trades']:
        assert t['plan']['style'] == 'X1' and t['plan']['playbook'] in ('T1', 'R1', 'B1', 'PB08L')
    s = park['stats']
    assert s['decisions'] >= s['chosen'] and {'vetoed', 'no_second_opinion', 'not_leader'} <= set(s)
    verdict = sb.judge('X1', park, registries=[])
    assert 'not_luck' in verdict['criteria']


def test_the_trend_led_bot_trades_only_with_the_daily_trend():
    candles, daily, rates, hits, start, end = combined_inputs()
    out = sb.run_combined(candles, daily, rates, hits, start, end, macro_events=[], stops={'PB08L': 12}, park=True, mode='X2')
    assert out['style'] == 'X2' and out['trades']
    assert all(t['side'] == 1 and t['plan']['playbook'] == 'T1' and t['initial_target'] is None for t in out['trades'])
    assert sb.judge('X2', out, registries=[])['criteria']['enough_trades'] is False      # swing track: 30 trades
