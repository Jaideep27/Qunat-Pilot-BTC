"""The live rule engine: minute cycles on the experimental futures-v1 account, journal and diagnosis."""
import copy

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from agent_fixtures import START, frames as five_minute
from quantpilot import journal, rule_live, rules


def minute_bars(candles):
    """Five 1m bars per 5m bar: same open and close, the 5m high in bar 2 and low in bar 4."""
    rows = []
    for bar in candles.itertuples():
        path = np.linspace(bar.open, bar.close, 6)
        for k in range(5):
            o, c = path[k], path[k+1]
            t = bar.event_time+pd.Timedelta(minutes=k)
            rows.append(dict(event_time=t, available_at=t+pd.Timedelta(minutes=1), open=o, close=c,
                             high=bar.high if k == 1 else max(o, c), low=bar.low if k == 3 else min(o, c),
                             volume=bar.volume/5, taker_buy_volume=bar.taker_buy_volume/5))
    return pd.DataFrame(rows)


def book():
    b = copy.deepcopy(rules.load())
    b['triggers']['EVERY_BAR'] = dict(direction=0, at='15m', when=['close', '>', 0])
    common = dict(family='test', source=[], thesis='', regimes=['RANGE', 'TREND_UP', 'TREND_DOWN', 'HIGH_VOL', 'PANIC'],
                  trigger={'any': ['EVERY_BAR'], 'within_bars': 0, 'min_bars': 0}, conditions=[['close', '>', 0]],
                  stop={'anchor': 'close', 'buffer_atr': 1.0}, target={'candidates': [], 'cap': None, 'fallback_r': 3.0},
                  abort=[], time_stop_hours=1, breakeven_at_r=None, may_flip=False, side='long')
    b['playbooks'] = [dict(common, id='L1', name='L1', priority=1), dict(common, id='L2', name='L2', priority=2)]
    b['hash'] = 'livetest'
    return b


@pytest.fixture(scope='module')
def market():
    data = five_minute(days=42)
    return data, minute_bars(data['candles'])


def run(engine, now, market, b):
    data, fast = market
    return rule_live.cycle(engine, now, book=b, frames=data, fast=fast, macro_events=[], news_events=[])


def test_a_15m_close_decision_fills_at_the_next_minute_and_is_journalled(account, market):
    b = book()
    t15 = START+pd.Timedelta(days=41, hours=3)
    first = run(account, t15+pd.Timedelta(seconds=20), market, b)
    assert first['decision']['action'] == 'enter' and first['decision']['chosen']['playbook'] == 'L1'
    hits = journal.hits(account)
    assert {h['playbook']: h['status'] for h in hits} == {'L1': 'TAKEN', 'L2': 'SKIPPED_LOWER_PRIORITY'}
    state = rule_live.load_account(account)['futures']
    assert state['pending']['action'] == 'enter' and state['position'] is None
    run(account, t15+pd.Timedelta(minutes=1, seconds=20), market, b)
    state = rule_live.load_account(account)['futures']
    nxt = market[1].set_index('event_time').open.loc[t15]
    assert state['position']['entry'] == pytest.approx(nxt*(1+state['position']['slippage']))
    again = run(account, t15+pd.Timedelta(minutes=1, seconds=40), market, b)      # same minute: nothing new
    assert again['bars'] == 0 and again['decision'] is None and len(journal.hits(account)) == 2


def test_missed_minutes_are_stepped_but_past_closes_are_not_decided(account, market):
    b = book()
    t15 = START+pd.Timedelta(days=41, hours=3)
    run(account, t15+pd.Timedelta(seconds=20), market, b)
    later = run(account, t15+pd.Timedelta(hours=2, minutes=7), market, b)
    assert later['bars'] == 127 and later['decision'] is None                     # 2h07m of bars, 15m close too old
    trades = journal.trades(account)
    assert len(trades) == 1 and trades[0]['playbook'] == 'L1' and trades[0]['diagnosis'] is None


def test_trades_and_skipped_hits_are_diagnosed_after_their_windows(account, market):
    b = book()
    t15 = START+pd.Timedelta(days=41, hours=3)
    run(account, t15+pd.Timedelta(seconds=20), market, b)
    run(account, t15+pd.Timedelta(hours=2, minutes=7), market, b)
    out = run(account, t15+pd.Timedelta(hours=6, minutes=3), market, b)
    assert out['diagnosed']['trades'] == 1 and out['diagnosed']['hits'] >= 1
    trade = journal.trades(account)[0]
    assert trade['diagnosis'] in rule_live.diagnosis.ADVICE
    skipped = [h for h in journal.hits(account) if h['playbook'] == 'L2'][0]
    assert skipped['diagnosis'].startswith('SKIP_') and skipped['outcome']['valid']
    taken = [h for h in journal.hits(account) if h['playbook'] == 'L1'][0]
    assert taken['diagnosis'] == trade['diagnosis']


def test_new_triggers_are_recorded_once(account, market):
    b = book()
    t15 = START+pd.Timedelta(days=41, hours=3)
    run(account, t15+pd.Timedelta(seconds=20), market, b)
    n = len(journal.triggers(account, limit=10_000))
    run(account, t15+pd.Timedelta(minutes=1, seconds=20), market, b)
    assert n > 0 and len(journal.triggers(account, limit=10_000)) == n


def test_fast_candles_are_fetched_into_their_own_market_key(account):
    from quantpilot.market_store import MarketCandle, PERP
    now = pd.Timestamp('2026-03-01 12:00:30', tz='UTC')
    raw = [[int(t.timestamp()*1000), 100, 101, 99, 100.5, 3, 0, 0, 5, 1, 1.5, 0]
           for t in pd.date_range(now.floor('min')-pd.Timedelta(minutes=3), periods=4, freq='min')]
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=raw))) as client:
        assert rule_live.fetch_fast(account, client, now) == 4
    with account.connect() as connection:
        markets = connection.execute(select(MarketCandle.market, MarketCandle.interval)).all()
    assert set(markets) == {(rule_live.FAST, '1m')} and rule_live.FAST != PERP
    bars = rule_live.fast_bars(account, now-pd.Timedelta(hours=1), now)
    assert len(bars) == 3                                                           # the forming minute is excluded


def test_the_rl_manager_is_recorded_in_shadow_and_acts_only_when_live(account, market):
    b = book()
    t15 = START+pd.Timedelta(days=41, hours=3)
    exit_always = lambda state: 'exit'
    data, fast = market
    rule_live.cycle(account, t15+pd.Timedelta(seconds=20), book=b, frames=data, fast=fast, macro_events=[], news_events=[])
    rule_live.cycle(account, t15+pd.Timedelta(minutes=6, seconds=20), book=b, frames=data, fast=fast, macro_events=[],
                    news_events=[], manager=exit_always)
    acct = rule_live.load_account(account)
    assert acct['manage']['action'] == 'exit' and acct['manage']['mode'] == 'shadow' and acct['futures']['position']
    rule_live.cycle(account, t15+pd.Timedelta(minutes=12, seconds=20), book=b, frames=data, fast=fast, macro_events=[],
                    news_events=[], manager=exit_always, modes={('manage_rl', 'L1'): 'live'})
    acct = rule_live.load_account(account)
    assert acct['futures']['position'] is None and acct['futures']['trades'][-1]['reason'] == 'EXIT_ACTION'


def test_fetch_marks_upserts_mark_and_index(account):
    from quantpilot.market_store import MarketObservation, PERP
    now = pd.Timestamp('2026-03-01 12:02:30', tz='UTC')
    seen = []

    def reply(request):
        seen.append(request.url)
        opens = pd.date_range('2026-03-01 11:50', periods=3, freq='5min', tz='UTC')     # 12:00 is still forming
        price = 100.0 if 'markPrice' in str(request.url) else 101.0
        return httpx.Response(200, json=[[int(t.timestamp()*1000), price, price, price, price, 0, 0, 0, 0, 0, 0, 0]
                                         for t in opens])
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        assert rule_live.fetch_marks(account, client, now) == 4
    assert any('pair=BTCUSDT' in str(u) and 'indexPrice' in str(u) for u in seen)
    with account.connect() as connection:
        rows = connection.execute(select(MarketObservation.family, MarketObservation.available_at, MarketObservation.values)
                                  .where(MarketObservation.market == PERP).order_by(MarketObservation.family,
                                                                                    MarketObservation.event_time)).all()
    assert [r[0] for r in rows] == ['index', 'index', 'mark', 'mark']
    assert pd.Timestamp(rows[-1][1]).tz_localize(None) == pd.Timestamp('2026-03-01 12:00')
    assert rows[0][2] == {'index_price': 101.0} and rows[-1][2] == {'mark_price': 100.0}


def test_market_cache_reloads_when_marks_arrive(account):
    from quantpilot.market_store import save_frame
    rule_live._cache.clear()
    now = pd.Timestamp('2026-03-01 12:02:30', tz='UTC')

    def obs(family, t):
        return pd.DataFrame([{'event_time': t-pd.Timedelta(minutes=5), 'available_at': t, 'received_at': now,
                              'source': 'test', f'{family}_price': 100.0}])
    save_frame(account, 'candles', pd.DataFrame([dict(event_time=now.floor('5min')-pd.Timedelta(minutes=5),
                                                      available_at=now.floor('5min'), received_at=now, source='test',
                                                      open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)]))
    save_frame(account, 'mark', obs('mark', now.floor('5min')-pd.Timedelta(minutes=5)))
    first = rule_live._market(account, now)
    save_frame(account, 'mark', obs('mark', now.floor('5min')))
    second = rule_live._market(account, now)
    assert len(first['mark']) == 1 and len(second['mark']) == 2
