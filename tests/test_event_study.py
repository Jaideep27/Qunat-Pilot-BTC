import numpy as np
import pandas as pd
import pytest

from quantpilot import event_study as es


def hourly_path(closes, start='2025-01-01'):
    """Five-minute candles whose hourly closes follow `closes` exactly (flat within each hour)."""
    rows = []
    t0 = pd.Timestamp(start, tz='UTC')
    for h, close in enumerate(closes):
        opened = closes[h-1] if h else close
        for k in range(12):
            t = t0+pd.Timedelta(hours=h, minutes=5*k)
            price = close if k == 11 else opened
            rows.append(dict(event_time=t, available_at=t+pd.Timedelta(minutes=5), open=price, high=max(price, opened),
                             low=min(price, opened), close=price, volume=10.0, taker_buy_volume=5.0,
                             trade_count=1.0, source='fixture', received_at=t+pd.Timedelta(minutes=5)))
    return pd.DataFrame(rows)


def test_hourly_panel_rows_only_use_data_known_at_their_cutoff():
    closes = list(np.linspace(100, 110, 300))
    base = es.hourly_panel(dict(candles=hourly_path(closes)))
    changed = hourly_path(closes[:200]+[50.0]*100)  # rewrite everything after hour 200
    altered = es.hourly_panel(dict(candles=changed))
    cutoff = base.index[199]  # close of hour 199 is known at the start of hour 200
    pd.testing.assert_frame_equal(base.loc[:cutoff].drop(columns=es.OUTCOMES),
                                  altered.loc[:cutoff].drop(columns=es.OUTCOMES))
    assert base.index[0] == pd.Timestamp('2025-01-01T01:00', tz='UTC')  # first hour known at its close


def test_episodes_merge_overlapping_triggers_and_separate_kinds():
    index = pd.date_range('2025-01-01', periods=100, freq='h', tz='UTC')
    ret = pd.Series(0.0, index=index)
    ret.iloc[[30, 31, 40]] = -.06   # one drop episode: 31 and 40 fall inside the 24h cooldown
    ret.iloc[60] = -.051            # a second drop after the cooldown
    ret.iloc[35] = .07              # a rally is tracked independently
    found = es.find_episodes(pd.DataFrame({'ret_24h': ret}))
    assert [(e['kind'], e['time']) for e in found] == [
        ('drop', index[30]), ('rally', index[35]), ('drop', index[60])]


def test_controls_match_hour_and_stay_quiet_and_away_from_episodes():
    index = pd.date_range('2025-01-01', periods=24*30, freq='h', tz='UTC')
    panel = pd.DataFrame({'ret_24h': 0.0}, index=index)
    panel.loc[index[24*10+5:24*10+8], 'ret_24h'] = -.03  # not quiet enough to be a control
    episode = dict(kind='drop', time=index[24*12+5])
    controls = es.match_controls(panel, [episode], per_episode=3)
    times = [c['time'] for c in controls]
    assert len(times) == 3 and all(t.hour == 5 for t in times)
    assert all(abs(t-episode['time']) >= pd.Timedelta(hours=48) for t in times)
    assert index[24*10+5] not in times
    assert all(c['matched_to'] == episode['time'] for c in controls)


def test_outcomes_report_forward_returns_and_intrahorizon_extremes():
    closes = [100.0]*30+[100, 98, 97, 99]+[95.0]*30
    panel = es.hourly_panel(dict(candles=hourly_path(closes)))
    t = panel.index[29]  # close of hour 29 = 100
    row = panel.loc[t]
    assert row['fwd_ret_1h'] == pytest.approx(0.0)
    assert row['fwd_ret_4h'] == pytest.approx(-.01)
    assert row['fwd_ret_24h'] == pytest.approx(-.05)
    assert row['max_drawdown_24h'] == pytest.approx(-.05)
    assert bool(row['drop5_touch_24h']) is True


def test_precursor_table_counts_future_drops_by_state_bucket():
    index = pd.date_range('2025-01-01', periods=10, freq='h', tz='UTC')
    panel = pd.DataFrame({'funding_rate': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10.],
                          'drop5_touch_24h': [0, 0, 0, 0, 0, 0, 0, 0, 1, 1.]}, index=index)
    table = es.precursor_table(panel, 'funding_rate', bins=2)
    assert [b['hours'] for b in table['buckets']] == [5, 5]
    assert [b['drop_rate'] for b in table['buckets']] == [0.0, .4]
    assert table['base_rate'] == pytest.approx(.2)


def test_bootstrap_difference_is_deterministic_and_brackets_the_estimate():
    a, b = np.array([-.03, -.01, -.02, -.04]), np.array([.01, .0, .02, -.01])
    first = es.bootstrap_difference(a, b, seed=7)
    assert first == es.bootstrap_difference(a, b, seed=7)
    assert first['difference'] == pytest.approx(-.03)  # mean(a) -.025 minus mean(b) .005
    assert first['ci95'][0] <= first['difference'] <= first['ci95'][1]


def test_market_frames_load_from_the_durable_store(account):
    from quantpilot.market_store import save_frame
    candles = hourly_path([100.0, 101.0])
    save_frame(account, 'candles', candles)
    frames = es.load_market(account, pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2025-01-02', tz='UTC'))
    assert len(frames['candles']) == 24 and frames['candles'].close.iloc[-1] == 101.0
