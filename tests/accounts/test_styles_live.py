"""accounts-v2 (A10) live engine: four styles, four ledgers, retired A8 accounts, journal and restarts."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.accounts import journal as account_journal
from quantpilot.accounts import styles_live as live
from quantpilot.rules import journal
from quantpilot.strategies import base, breakout, range_trader, trend
from style_fixtures import synthetic

START = pd.Timestamp('2026-01-05', tz='UTC')


def cfg(review_days=30):
    common = dict(risk=.01, leverage=2.0, daily_brake=.03)
    return dict(version=2, round_length=30, floor=5000.0, balance=10_000.0, hash='v2test', retired=['acct-a'],
                accounts=[dict(common, id='acct-trend', name='Trend Rider', style='T1'),
                          dict(common, id='acct-range', name='Range Trader', style='R1'),
                          dict(common, id='acct-breakout', name='Breakout Hunter', style='B1'),
                          dict(id='acct-carry', name='Funding Collector', style='C1', review_days=review_days)])


@pytest.fixture(scope='module')
def market():
    candles = synthetic(days=45, seed=11, start=str(START.date()))
    times = pd.date_range(START, START+pd.Timedelta(days=45), freq='8h', tz='UTC')
    funding = pd.DataFrame(dict(event_time=times, available_at=times+pd.Timedelta(seconds=30), funding_rate=.0001))
    return dict(candles=candles, funding=funding)


def fast_until(market, now):
    c = market['candles']
    return c[c.available_at <= now][['available_at', 'open', 'high', 'low', 'close']]


def frames_until(market, now):
    return {k: v[pd.to_datetime(v.available_at, utc=True) <= now] for k, v in market.items()}


def step(engine, market, now, **kw):
    live._cache.clear()
    return live.step_all(engine, frames_until(market, now), fast_until(market, now), now, [], cfg=kw.pop('config', cfg()),
                         daily=kw.pop('daily', None), history=market['candles'], **kw)


def test_first_run_creates_four_style_accounts_and_retires_the_old_ones(account, market):
    journal.save_account(account, 'acct-a', dict(status='RUNNING', futures=dict(position=None, equity=10_000.0)),
                         START)
    now = START+pd.Timedelta(days=40, minutes=1)
    out = step(account, market, now)
    assert set(out) >= {'acct-trend', 'acct-range', 'acct-breakout', 'acct-carry'}
    assert journal.load_account(account, 'acct-a')['status'] == 'RETIRED'
    for a in cfg()['accounts']:
        acct = journal.load_account(account, a['id'])
        assert acct['style'] == a['style'] and acct['status'] in ('RUNNING', 'PAUSED_FOR_REVIEW')
        assert acct['active'] and isinstance(acct['active']['reason'], str)
    assert journal.load_account(account, 'acct-carry')['futures']['version'] == 'carry-v1'


def test_a_range_signal_is_taken_journaled_with_the_time_machine_and_filled_next_bar(account, market, monkeypatch):
    t15 = START+pd.Timedelta(days=40, hours=6)
    frame = pd.DataFrame(dict(side=['long'], stop=[1.0], target=[1e9], max_hold_hours=[4.0], reason=['test'],
                              ref=[None], limit=[None], valid_minutes=[None], risk_scale=[1.0]), index=[t15])

    def fake(sf):
        out = frame.copy()
        out['ref'] = sf.close.reindex(out.index).to_numpy()
        out['stop'] = out.ref*.99
        out['target'] = out.ref*1.02
        return out
    monkeypatch.setattr(range_trader, 'signals', fake)
    step(account, market, t15-pd.Timedelta(minutes=30))
    step(account, market, t15+pd.Timedelta(seconds=30))
    rows = [d for d in account_journal.decisions(account, 'acct-range') if d['decision'] == 'TAKEN']
    assert len(rows) == 1
    d = rows[0]
    assert d['hit_id'] == f'R1:{t15.isoformat()}' and d['payload']['style'] == 'R1'
    assert d['payload']['time_machine']['paths'] == 2000 and d['payload']['sizing']['risk_amount'] > 0
    assert journal.load_account(account, 'acct-range')['futures']['pending']['action'] == 'enter'
    step(account, market, t15+pd.Timedelta(minutes=6))
    p = journal.load_account(account, 'acct-range')['futures']['position']
    assert p is not None and p['plan']['style'] == 'R1' and p['max_hold_hours'] == 4.0
    for other in ('acct-trend', 'acct-breakout'):
        assert not journal.load_account(account, other)['futures']['position']


def test_the_same_decision_is_not_taken_twice_after_a_restart(account, market, monkeypatch):
    t15 = START+pd.Timedelta(days=40, hours=9)
    def fake(sf):
        if t15 not in sf.index:
            return pd.DataFrame(columns=['side'])
        c = sf.close[t15]
        return pd.DataFrame(dict(side=['long'], stop=[c*.99], target=[c*1.02], max_hold_hours=[4.0], reason=['x'], ref=[c],
                                 limit=[None], valid_minutes=[None], risk_scale=[1.0]), index=[t15])
    monkeypatch.setattr(range_trader, 'signals', fake)
    step(account, market, t15-pd.Timedelta(minutes=30))
    step(account, market, t15+pd.Timedelta(seconds=30))
    step(account, market, t15+pd.Timedelta(seconds=50))
    assert sum(d['decision'] == 'TAKEN' for d in account_journal.decisions(account, 'acct-range')) == 1


def test_trend_decides_once_per_daily_close_from_daily_bars(account, market, monkeypatch):
    idx = pd.date_range(START-pd.Timedelta(days=200), START+pd.Timedelta(days=41), freq='D', tz='UTC')
    close = 50_000*np.exp(np.linspace(0, .8, len(idx)))
    daily = pd.DataFrame(dict(open=close, high=close*1.01, low=close*.99, close=close, volume=1.0), index=idx)
    day = START+pd.Timedelta(days=41)
    step(account, market, day-pd.Timedelta(minutes=30), daily=daily)
    step(account, market, day+pd.Timedelta(minutes=0, seconds=40), daily=daily)
    taken = [d for d in account_journal.decisions(account, 'acct-trend') if d['decision'] == 'TAKEN']
    assert len(taken) == 1 and taken[0]['payload']['side'] == 'long' and taken[0]['payload']['plan']['target'] is None
    step(account, market, day+pd.Timedelta(minutes=16), daily=daily)
    assert len([d for d in account_journal.decisions(account, 'acct-trend') if d['decision'] == 'TAKEN']) == 1


def test_the_funding_collector_opens_earns_and_pauses_for_review_after_its_days(account, market):
    t = START+pd.Timedelta(days=30, minutes=1)
    step(account, market, t, config=cfg(review_days=3))
    acct = journal.load_account(account, 'acct-carry')
    assert acct['futures']['open'] is True
    step(account, market, t+pd.Timedelta(days=2), config=cfg(review_days=3))
    acct = journal.load_account(account, 'acct-carry')
    assert acct['futures']['cycle']['funding'] > 0 and acct['status'] == 'RUNNING'
    step(account, market, t+pd.Timedelta(days=3, minutes=5), config=cfg(review_days=3))
    acct = journal.load_account(account, 'acct-carry')
    assert acct['status'] == 'PAUSED_FOR_REVIEW' and acct['futures']['open'] is True   # the hedge is kept
