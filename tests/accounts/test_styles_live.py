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


def cfg5():
    c = cfg()
    c['accounts'] = [dict(id='acct-combined', name='Combined Bot', style='X1', risk=.01, leverage=2.0, daily_brake=.03)]+c['accounts']
    return c


def sideways(monkeypatch):
    real = live._frame

    def frame(frames):
        sf = real(frames).copy()
        sf['regime'] = 'RANGE'
        return sf
    monkeypatch.setattr(live, '_frame', frame)


def test_the_combined_bot_takes_a_backed_leader_and_records_every_view(account, market, monkeypatch):
    sideways(monkeypatch)
    t15 = START+pd.Timedelta(days=40, hours=12)

    def fake(sf):
        if t15 not in sf.index:
            return pd.DataFrame(columns=['side'])
        c = sf.close[t15]
        return pd.DataFrame(dict(side=['long'], stop=[c*.99], target=[c*1.01], max_hold_hours=[4.0], reason=['dip'], ref=[c],
                                 limit=[None], valid_minutes=[None], risk_scale=[1.0]), index=[t15])
    monkeypatch.setattr(range_trader, 'signals', fake)
    price = float(market['candles'].close[market['candles'].available_at <= t15].iloc[-1])
    hit = dict(playbook='PB10L', side='long', time=t15, status='SKIPPED_LOWER_PRIORITY',
               plan=dict(entry=price, stop=price*.99, target=price*1.02, reward_risk=2.0))
    step(account, market, t15-pd.Timedelta(minutes=30), config=cfg5())
    step(account, market, t15+pd.Timedelta(seconds=30), config=cfg5(), hits=[hit])
    rows = account_journal.decisions(account, 'acct-combined')
    d = next(r for r in rows if r['hit_id'] == f'X1:{t15.isoformat()}')
    assert d['decision'] == 'TAKEN' and d['payload']['regime'] == 'RANGE'
    assert {c['source'] for c in d['payload']['considered']} == {'R1', 'PB10L'}
    assert d['payload']['chosen']['agreements'] and d['payload']['sizing']['risk_amount'] > 0
    acct = journal.load_account(account, 'acct-combined')
    assert acct['futures']['pending']['action'] == 'enter' and acct['active']['on']
    assert acct['members']['regime'] == 'RANGE'


def test_a_lonely_leader_is_skipped_and_logged(account, market, monkeypatch):
    sideways(monkeypatch)
    t15 = START+pd.Timedelta(days=40, hours=15)
    monkeypatch.setattr(range_trader, 'signals', lambda sf: pd.DataFrame(columns=['side']))
    price = float(market['candles'].close[market['candles'].available_at <= t15].iloc[-1])
    hit = dict(playbook='PB10L', side='long', time=t15, status='TAKEN',
               plan=dict(entry=price, stop=price*.99, target=price*1.02, reward_risk=2.0))
    monkeypatch.setattr(live.combined, 'at_level', lambda side, row: [])
    step(account, market, t15-pd.Timedelta(minutes=30), config=cfg5())
    step(account, market, t15+pd.Timedelta(seconds=30), config=cfg5(), hits=[hit])
    d = next(r for r in account_journal.decisions(account, 'acct-combined') if r['hit_id'] == f'X1:{t15.isoformat()}')
    assert d['decision'] == 'NO_SECOND_OPINION'
    assert not journal.load_account(account, 'acct-combined')['futures']['pending']


def test_the_combined_bot_parks_idle_money_after_a_flat_day(account, market):
    t = START+pd.Timedelta(days=30, minutes=1)
    step(account, market, t, config=cfg5())
    step(account, market, t+pd.Timedelta(days=1, hours=9), config=cfg5())
    acct = journal.load_account(account, 'acct-combined')
    assert acct['hedge'] and acct['hedge']['open'] and acct['futures']['position'] is None


def cfg6():
    c = cfg5()
    c['accounts'].insert(1, dict(id='acct-trend-bot', name='Trend-Led Bot', style='X2', risk=.01, leverage=2.0, daily_brake=.03))
    return c


def test_the_trend_led_bot_enters_with_the_daily_trend_when_another_member_agrees(account, market, monkeypatch):
    t15 = START+pd.Timedelta(days=40, hours=18)
    idx = pd.date_range(START-pd.Timedelta(days=200), START+pd.Timedelta(days=41), freq='D', tz='UTC')
    close = 50_000*np.exp(np.linspace(0, .8, len(idx)))
    daily = pd.DataFrame(dict(open=close, high=close*1.01, low=close*.99, close=close, volume=1.0), index=idx)
    monkeypatch.setattr(range_trader, 'signals', lambda sf: pd.DataFrame(columns=['side']))
    monkeypatch.setattr(breakout, 'signals', lambda sf: pd.DataFrame(columns=['side']))
    monkeypatch.setattr(live.combined, 'at_level', lambda side, row: [])
    price = float(market['candles'].close[market['candles'].available_at <= t15].iloc[-1])
    short = dict(playbook='PB08S', side='short', time=t15, status='TAKEN', plan=dict(entry=price, stop=price*1.01, target=price*.98, reward_risk=2.0))
    long = dict(playbook='PB08L', side='long', time=t15, status='TAKEN', plan=dict(entry=price, stop=price*.99, target=price*1.02, reward_risk=2.0))
    step(account, market, t15-pd.Timedelta(minutes=30), config=cfg6(), daily=daily)
    step(account, market, t15+pd.Timedelta(seconds=30), config=cfg6(), daily=daily, hits=[short])
    assert not journal.load_account(account, 'acct-trend-bot')['futures']['pending']      # against the trend: nothing
    step(account, market, t15+pd.Timedelta(minutes=15, seconds=30), config=cfg6(), daily=daily,
         hits=[dict(long, time=t15+pd.Timedelta(minutes=15))])
    acct = journal.load_account(account, 'acct-trend-bot')
    plan = acct['futures']['pending']['plan']
    assert plan['side'] == 'long' and plan['target'] is None and plan['playbook'] == 'T1' and acct['active']['on']
    d = [r for r in account_journal.decisions(account, 'acct-trend-bot') if r['decision'] == 'TAKEN'][0]
    assert d['payload']['chosen']['agreements'] == ['PB08L'] and d['payload']['style'] == 'X2'


def test_with_parking_off_the_bot_keeps_idle_money_as_cash_and_closes_an_open_hedge(account, market):
    from quantpilot.simulation import carry as ledger
    off = cfg5()
    off['accounts'][0]['park'] = False
    t = START+pd.Timedelta(days=30, minutes=1)
    step(account, market, t, config=off)
    step(account, market, t+pd.Timedelta(days=1, hours=9), config=off)
    acct = journal.load_account(account, 'acct-combined')
    assert not acct['hedge'] and not acct['cycles']
    h = ledger.initial(acct['futures']['equity'])                 # a hedge left open from before the switch
    ledger.open_(h, 80_000.0, t)
    acct['hedge'] = h
    journal.save_account(account, 'acct-combined', acct, t)
    step(account, market, t+pd.Timedelta(days=1, hours=9, minutes=5), config=off)
    acct = journal.load_account(account, 'acct-combined')
    assert acct['hedge'] is None and acct['cycles'][-1]['reason'] == 'PARKING_OFF'


def test_retiring_closes_a_funding_hedge_and_a_bots_idle_hedge(account):
    from quantpilot.simulation import carry as ledger
    from quantpilot.strategies import bot
    t = START+pd.Timedelta(days=30)
    c = ledger.initial(10_000.0)
    ledger.open_(c, 80_000.0, t)
    journal.save_account(account, 'acct-carry', dict(status='RUNNING', style='C1', futures=c), t)
    st = bot.new(10_000.0, now=t)
    h = ledger.initial(10_000.0)
    ledger.open_(h, 80_000.0, t)
    st['hedge'] = h
    journal.save_account(account, 'acct-combined', dict(st, status='RUNNING', style='X1'), t)
    live.retire(account, ['acct-carry', 'acct-combined'], t+pd.Timedelta(hours=1))
    carry_state = journal.load_account(account, 'acct-carry')
    assert carry_state['status'] == 'RETIRED' and carry_state['futures']['open'] is False
    assert carry_state['futures']['trades'][-1]['reason'] == 'RETIRED'
    bot_state = journal.load_account(account, 'acct-combined')
    assert bot_state['status'] == 'RETIRED' and bot_state['hedge'] is None and bot_state['cycles'][-1]['reason'] == 'RETIRED'


def test_the_master_records_every_advisor_view_including_funding_and_volume(account, market):
    master = dict(cfg5(), accounts=[dict(id='acct-master', name='Master Bot', style='X2', risk=.01, leverage=2.0,
                                         daily_brake=.03, park=False)])
    step(account, market, START+pd.Timedelta(days=40, minutes=1), config=master)
    m = journal.load_account(account, 'acct-master')['members']
    assert {'regime', 'vote', 'range_trader', 'breakout', 'funding', 'volume'} <= set(m)
    assert isinstance(m['funding'], str) and 'funding' in m['funding'].lower() and m['volume'] > 0
