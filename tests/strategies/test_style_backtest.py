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
