"""Trigger detection, watch windows and episode facts."""
import pandas as pd
import pytest

from quantpilot import rules, triggers

T0 = pd.Timestamp('2026-03-02 00:05', tz='UTC')


def frame(n=96):
    index = pd.date_range(T0, periods=n, freq='5min')
    pf = pd.DataFrame(index=index)
    pf['move_z_15m'], pf['volume_z_15m'], pf['move_z_1h'] = 0.0, 0.0, 0.0
    pf['close'] = 100.0
    pf['high'], pf['low'] = 100.5, 99.5
    pf['open_interest'] = 1000.0
    pf['support'], pf['resistance'], pf['atr_1h'] = 98.0, 103.0, 1.0
    pf['sweep_low'] = 0.0
    pf['bar15_close'] = pd.Series(index, index=index).dt.floor('15min')
    pf['high_15m'], pf['low_15m'] = 101.0, 99.0
    return pf


@pytest.fixture(scope='module')
def book():
    return rules.load()


def test_an_anomaly_fires_once_per_episode(book):
    pf = frame()
    pf.loc[pf.index[20:24], ['move_z_15m', 'volume_z_15m']] = [-6.0, 3.0]
    pf.loc[pf.index[40], ['move_z_15m', 'volume_z_15m']] = [-5.0, 0.5]       # no volume: not a dump
    table = triggers.detect(pf, book)
    dumps = table[table.type == 'DUMP']
    assert list(dumps.time) == [pf.index[20]]
    row = dumps.iloc[0]
    assert row.direction == -1 and row.support == 98.0 and row.start == pf.index[20]-pd.Timedelta(minutes=15)


def test_15m_triggers_fire_only_at_15m_closes(book):
    pf = frame()
    pf.loc[pf.index[10:14], 'sweep_low'] = 1.0          # forward-filled state of the 15m bar closing at index 11
    table = triggers.detect(pf, book)
    sweeps = table[table.type == 'LEVEL_SWEEP_LOW']
    closes = [t for t in sweeps.time]
    assert closes and all(t == t.floor('15min') for t in closes)
    assert sweeps.iloc[0].level == 98.0


def test_macro_release_trigger_carries_the_first_post_release_range(book):
    pf = frame()
    release = pf.index[30].floor('15min')
    events = [dict(kind='cpi', label='CPI', scheduled_at=release.isoformat())]
    table = triggers.detect(pf, book, macro_events=events)
    macro = table[table.type == 'MACRO_RELEASE'].iloc[0]
    assert macro.time == release+pd.Timedelta(minutes=15)
    assert (macro.range_high, macro.range_low, macro.range_mid) == (101.0, 99.0, 100.0)


def test_news_triggers_use_availability_and_direction(book):
    pf = frame()
    items = [dict(available_at=(T0+pd.Timedelta(minutes=33)).isoformat(), impact='high', direction='bearish',
                  confidence=.8, category='hack', id='n1'),
             dict(available_at=(T0+pd.Timedelta(minutes=50)).isoformat(), impact='low', direction='bullish',
                  confidence=.9, category='adoption', id='n2')]
    table = triggers.detect(pf, book, news_events=items)
    news = table[table.type.str.startswith('NEWS')]
    assert list(news.type) == ['NEWS_SHOCK_DOWN'] and news.iloc[0].time == T0+pd.Timedelta(minutes=35)
    assert news.iloc[0].confidence == .8


def test_active_window_and_episode_facts(book):
    pf = frame()
    pf.loc[pf.index[20], ['move_z_15m', 'volume_z_15m']] = [-6.0, 3.0]
    pf.loc[pf.index[19:23], 'low'] = [99.0, 96.0, 95.5, 97.0]
    pf.loc[pf.index[26]:, 'open_interest'] = 980.0
    index = triggers.TriggerIndex(triggers.detect(pf, book))
    spec = rules.playbook(book, 'PB01')['trigger']                     # DUMP within 6 bars, at least 1
    assert index.active(pf.index[21], spec) is None                    # too early: same 15m bar
    t = pf.index[27]
    trig = index.active(t, spec)
    assert trig is not None and trig.type == 'DUMP'
    facts = triggers.episode(trig, t, pf)
    assert facts['trigger.low'] == 95.5
    assert facts['trigger.oi_change'] == pytest.approx(-.02)
    assert facts['trigger.low_to_support_atr'] == pytest.approx(-2.5)
    assert facts['trigger.age_bars'] == 2
    assert index.active(pf.index[20]+pd.Timedelta(hours=2), spec) is None   # beyond 6 bars
