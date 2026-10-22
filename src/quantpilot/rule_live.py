"""Live rule engine: the experimental `futures-v1` paper account driven by rulebook v1 (design spec 3.7).

Each minute: fetch completed 1m candles, step the account through them (stops, targets, funding, the
break-even stop and, at 15m closes, aborts and time stops), record new triggers, and at a fresh 15m close
(at most 2 minutes old) let the arbiter decide. Missed minutes are reconciled by stepping bars; entries
are never made at past closes. Trades and skipped hits are diagnosed once their windows end.
The frozen release's paper account is not touched. Nothing here places real orders.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sqlalchemy import select

from . import account_live, arbiter, diagnosis, futures, journal, outcomes, patterns, rules, setup_history, triggers
from .market_store import PERP, MarketCandle, _upsert, feed_status

ACCOUNT_ID = 'experimental-v1'
FAST = PERP+'_1M'
LOOKBACK = pd.Timedelta(days=40)
FRESH = pd.Timedelta(minutes=2)
POST = pd.Timedelta(hours=4)
STRESS = ('DUMP', 'SPIKE')
OVERLAY = ('close', 'support', 'resistance', 'support_1h', 'resistance_1h', 'support_4h', 'resistance_4h',
           'prior_day_high', 'prior_day_low', 'prior_week_high', 'prior_week_low', 'vwap', 'vwap_upper2', 'vwap_lower2',
           'band1_high', 'band1_low', 'band4_high', 'band4_low', 'move_z_15m', 'volume_z_15m', 'funding_z',
           'range_pos_4h', 'atr_1h')
_cache = {}


def fetch_fast(engine, client, now):
    """Upsert the latest 1m klines (from the last stored complete minute, at most 2 days back)."""
    now = pd.Timestamp(now)
    with engine.connect() as connection:
        last = connection.execute(select(MarketCandle.event_time).where(
            MarketCandle.market == FAST, MarketCandle.complete.is_(True)).order_by(MarketCandle.event_time.desc())
            .limit(1)).scalar()
    since = max(pd.Timestamp(last).tz_localize('UTC') if last is not None and pd.Timestamp(last).tzinfo is None
                else pd.Timestamp(last) if last is not None else now-pd.Timedelta(days=2), now-pd.Timedelta(days=2))
    response = client.get('https://fapi.binance.com/fapi/v1/klines',
                          params=dict(symbol='BTCUSDT', interval='1m', startTime=int(since.timestamp()*1000), limit=1000))
    response.raise_for_status()
    rows = []
    for bar in response.json():
        o, h, l, c, v = map(float, bar[1:6])
        if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c):
            raise ValueError('invalid provider OHLC')
        event = pd.to_datetime(bar[0], unit='ms', utc=True)
        rows.append(dict(market=FAST, interval='1m', event_time=event.to_pydatetime(),
                         available_at=(event+pd.Timedelta(minutes=1)).to_pydatetime(), received_at=now.to_pydatetime(),
                         source='https://fapi.binance.com/fapi/v1/klines', open=o, high=h, low=l, close=c, volume=v,
                         trade_count=float(bar[8]), taker_buy_volume=float(bar[9]),
                         complete=event+pd.Timedelta(minutes=1) <= now))
    table = MarketCandle.__table__
    return _upsert(engine, table, rows, ['market', 'interval', 'event_time'],
                   lambda stmt: (stmt.excluded.received_at >= table.c.received_at) & ((~table.c.complete) | stmt.excluded.complete))


def fast_bars(engine, start, end):
    with engine.connect() as connection:
        rows = connection.execute(select(MarketCandle.event_time, MarketCandle.available_at, MarketCandle.open,
                                         MarketCandle.high, MarketCandle.low, MarketCandle.close, MarketCandle.volume,
                                         MarketCandle.taker_buy_volume).where(
            MarketCandle.market == FAST, MarketCandle.complete.is_(True),
            MarketCandle.available_at > pd.Timestamp(start).to_pydatetime(),
            MarketCandle.available_at <= pd.Timestamp(end).to_pydatetime()).order_by(MarketCandle.event_time)).all()
    frame = pd.DataFrame(rows, columns=['event_time', 'available_at', 'open', 'high', 'low', 'close', 'volume',
                                        'taker_buy_volume'])
    for column in ('event_time', 'available_at'):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def _market(engine, now):
    """The last 41 days of market families, reloaded only when a new completed 5m candle exists."""
    from sqlalchemy import func
    from .event_study import load_market
    with engine.connect() as connection:
        latest = connection.execute(select(func.max(MarketCandle.available_at)).where(
            MarketCandle.market == PERP, MarketCandle.interval == '5m', MarketCandle.complete.is_(True))).scalar()
    if _cache.get('market_key') != latest or 'frames' not in _cache:
        _cache.update(market_key=latest, frames=load_market(engine, now-LOOKBACK-pd.Timedelta(days=1),
                                                            now+pd.Timedelta(minutes=1)))
    return _cache['frames']


def load_account(engine):
    return journal.load_account(engine, ACCOUNT_ID)


def _new_account(book, now, last_bar):
    return dict(futures=futures.initial(10_000.0, bar_minutes=1), last_bar=last_bar, last_decision=None,
                last_pattern=None, flip_plan=None, extra={}, rulebook=book['hash'], created=pd.Timestamp(now).isoformat())


def _truncate(frames, now):
    return {k: v[pd.to_datetime(v.available_at, utc=True) <= now] for k, v in frames.items() if v is not None}


def _patterns(book, frames, now, macro_events, news_events):
    """Pattern frame and trigger table, rebuilt only when a new 5m close is available."""
    candles = frames['candles']
    last5 = pd.to_datetime(candles.available_at, utc=True).max()
    key = (book['hash'], last5, len(news_events or []))
    if _cache.get('key') != key:
        recent = {k: v[pd.to_datetime(v.available_at, utc=True) > now-LOOKBACK] for k, v in frames.items()}
        pf = patterns.pattern_frame(recent)
        _cache.update(key=key, pf=pf, table=triggers.detect(pf, book, macro_events, news_events))
    return _cache['pf'], _cache['table']


def _position(acct, p):
    info = acct['extra'].get(p['plan'].get('hit_id'), {})
    return dict(side=p['side'], entry=p['entry'], stop=p['stop'], initial_stop=p['initial_stop'],
                max_favorable=p['max_favorable'], entry_time=p['entry_time'], anchor=info.get('anchor'),
                open_interest=info.get('open_interest'), playbook=p['plan'].get('playbook'))


def _row(pf, t):
    k = pf.index.searchsorted(t, side='right')-1
    return pf.iloc[k].to_dict() if k >= 0 else {}


def _manage_state(p, t, pb):
    """The trade-management RL state for an open position at bar time `t` (see rl_manage.STATE)."""
    risk = abs(p['entry']-p['initial_stop'])/p['entry']
    held = (pd.Timestamp(t)-pd.Timestamp(p['entry_time']))/pd.Timedelta(hours=pb['time_stop_hours'])
    price = p.get('last_close', p['entry'])
    s = p['side']
    return np.array([s*(price/p['entry']-1)/risk, p['max_favorable']/risk, p['max_adverse']/risk, min(1.0, held),
                     s*(p['target']/price-1)/risk, s*(p['stop']/p['entry']-1)/risk], np.float32)


def _step_bars(book, acct, bars, pf, funding, manager=None, modes=None):
    """Reconcile: every new 1m bar, funding settlements, break-even moves, and at 15m closes the
    position's aborts and time stop (standing management, never new entries)."""
    state, playbooks = acct['futures'], {pb['id']: pb for pb in book['playbooks']}
    settle = funding.sort_values('event_time') if funding is not None and len(funding) else None
    previous = pd.Timestamp(acct['last_bar']) if acct['last_bar'] else None
    for bar in bars.itertuples():
        t = bar.available_at
        futures.step(state, dict(kind='bar', time=t.isoformat(), open=bar.open, high=bar.high, low=bar.low,
                                 close=bar.close, mark=bar.close), inplace=True)
        if settle is not None and previous is not None:
            times = pd.to_datetime(settle.event_time, utc=True)
            for rate in settle.funding_rate[(times > previous) & (times <= t)]:
                futures.step(state, dict(kind='funding', time=t.isoformat(), rate=float(rate), mark=bar.close),
                             inplace=True)
        previous = t
        acct['last_bar'] = t.isoformat()
        if state['pending']:
            continue
        p = state['position']
        if p is None:
            if acct.get('flip_plan'):
                futures.step(state, dict(kind='action', time=t.isoformat(), action='enter', plan=acct['flip_plan']),
                             inplace=True)
            acct['flip_plan'] = None
            continue
        acct['flip_plan'] = None
        pb = playbooks.get(p['plan'].get('playbook'))
        if pb is None:
            continue
        act = None
        if manager is not None and t.minute % 5 == 0:   # the RL manager was trained on 5m bars
            p['last_close'] = bar.close
            choice = manager(_manage_state(p, t, pb))
            acct['manage'] = dict(time=t.isoformat(), action=choice, mode=(modes or {}).get(('manage_rl', pb['id']), 'shadow'))
            if acct['manage']['mode'] == 'live' and choice != 'hold':
                level = p['entry']*(1+p['side']*arbiter.ROUND_TRIP) if choice == 'breakeven' else \
                    (p['entry']*(1+p['side']*p['max_favorable'])-p['side']*abs(p['entry']-p['initial_stop']))
                act = dict(action='exit') if choice == 'exit' else (
                    dict(action='tighten_stop', price=level) if p['side']*(level-p['stop']) > 0 else None)
        if act is None and t == t.floor('15min'):
            act = arbiter.manage(pb, _position(acct, p), _row(pf, t), t, book['defaults'])
        if act is None and pb.get('breakeven_at_r') is not None:
            risk = abs(p['entry']-p['initial_stop'])/p['entry']
            level = p['entry']*(1+p['side']*arbiter.ROUND_TRIP)
            if p['max_favorable'] >= pb['breakeven_at_r']*risk and p['side']*(level-p['stop']) > 0:
                act = dict(action='tighten_stop', price=level)
        if act:
            futures.step(state, dict(kind='action', time=t.isoformat(), action=act['action'], price=act.get('price')),
                         inplace=True)


def _decide(book, acct, pf, table, t15, layers, modes, macro_events):
    state = acct['futures']
    p = state['position']
    account = dict(equity=state['equity'], cash=state['cash'], halted=state['halted_day'] or state['drawdown_halt'],
                   paused=state['paused'], position=dict(side=p['side'], playbook=p['plan'].get('playbook')) if p else None)
    history = [dict(playbook=tr['plan'].get('playbook'), exit_time=tr['exit_time'], net=tr['net'])
               for tr in state['trades']]
    out = arbiter.decide(book, t15, pf, triggers.TriggerIndex(table), account, history, layers, modes, macro_events)
    chosen, iso = out['chosen'], pd.Timestamp(t15).isoformat()
    if chosen and not state['pending']:
        stressed = bool(len(table) and ((table.type.isin(STRESS)) & (table.time > t15-pd.Timedelta(hours=1))
                                        & (table.time <= t15)).any())
        plan = dict(chosen['plan'], context_cutoff=iso, slippage=futures.SLIPPAGE*(
            book['defaults']['anomaly_slippage_multiplier'] if stressed else 1))
        acct['extra'][chosen['id']] = dict(anchor=plan.get('anchor'), open_interest=_row(pf, t15).get('open_interest'))
        if out['action'] == 'flip':
            futures.step(state, dict(kind='action', time=iso, action='exit'), inplace=True)
            acct['flip_plan'] = plan
        else:
            futures.step(state, dict(kind='action', time=iso, action='enter', plan=plan), inplace=True)
    acct['last_decision'] = iso
    return out


def _bars_for(frames, fast):
    """5m bars for outcome simulation and 1m/5m highs and lows for the post-exit window."""
    c = frames['candles'].assign(available_at=lambda d: pd.to_datetime(d.available_at, utc=True)).sort_values('available_at')
    c = c.drop_duplicates('available_at')
    return outcomes.Bars(t=pd.DatetimeIndex(c.available_at), o=c.open.to_numpy(float), h=c.high.to_numpy(float),
                         l=c.low.to_numpy(float), c=c.close.to_numpy(float))


def _diagnose(engine, book, frames, fast, now):
    done = dict(trades=0, hits=0)
    window = fast if fast is not None and len(fast) else frames['candles']
    times = pd.to_datetime(window.available_at, utc=True)
    for trade in journal.trades(engine, undiagnosed=True):
        exit_time = pd.Timestamp(trade['exit_time'])
        if exit_time+POST > now:
            continue
        after = window[(times > exit_time) & (times <= exit_time+POST)]
        outcome = diagnosis.from_trade(trade['payload'], float(after.high.max()) if len(after) else None,
                                       float(after.low.min()) if len(after) else None)
        result = diagnosis.code(outcome, taken=True)
        journal.diagnose_trade(engine, trade['id'], result['code'], dict(result, outcome=outcome), now)
        journal.diagnose_hit(engine, trade['payload']['plan']['hit_id'], outcome, result['code'], now) \
            if trade['payload']['plan'].get('hit_id') else None
        done['trades'] += 1
    bars = _bars_for(frames, fast)
    playbooks = {pb['id']: pb for pb in book['playbooks']}
    for hit in journal.hits(engine, pending_only=True):
        pb, plan = playbooks.get(hit['playbook']), hit['payload'].get('plan')
        if hit['status'] == 'TAKEN' or pb is None or not plan:
            continue
        t = pd.Timestamp(hit['time'])
        if t+pd.Timedelta(hours=pb['time_stop_hours'])+POST > now:
            continue
        i = bars.t.searchsorted(t, side='right')-1
        if i < 0 or bars.t[i] != t:
            journal.diagnose_hit(engine, hit['id'], dict(valid=False, reason='NO_BARS'), 'UNKNOWN', now)
            continue
        result = outcomes.simulate(bars, i, pb['side'], plan['stop'], plan['target'],
                                   int(pb['time_stop_hours']*12), pb.get('breakeven_at_r'))
        code = diagnosis.code(result if result and result.get('valid') else None, taken=False)['code']
        journal.diagnose_hit(engine, hit['id'], result or dict(valid=False), code, now)
        done['hits'] += 1
    return done


def cycle(engine, now=None, book=None, frames=None, fast=None, macro_events=None, news_events=None, layers=None,
          modes=None, client=None, market_engine=None, manager=None, accounts_cfg=None, scorer=None):
    """One minute of the live rule engine (reference account, then the four A8 accounts). Returns a summary."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    book = book or rules.cached()
    market_engine = market_engine or engine
    if frames is None:
        frames = _market(market_engine, now)
    frames = _truncate(frames, now)
    if fast is None:
        if client is not None:
            fetch_fast(market_engine, client, now)
        fast = fast_bars(market_engine, now-pd.Timedelta(days=2), now)
    fast = fast[pd.to_datetime(fast.available_at, utc=True) <= now].assign(
        available_at=lambda d: pd.to_datetime(d.available_at, utc=True))
    acct = load_account(engine)
    if acct is None:
        acct = _new_account(book, now, fast.available_at.max().isoformat() if len(fast) else now.floor('min').isoformat())
    pf, table = _patterns(book, frames, now, macro_events, news_events)
    if len(pf):
        last = pf.iloc[-1]
        acct['overlay'] = dict(time=pf.index[-1].isoformat(), regime=patterns.regime(last.to_dict()),
                               **{k: rules._number(last.get(k)) for k in OVERLAY})
    recorded = journal.add_triggers(engine, [dict(id=f'{r.type}:{pd.Timestamp(r.time).isoformat()}', time=r.time,
                                                  **{k: v for k, v in r._asdict().items() if k not in ('Index', 'time')})
                                             for r in table[table.time > now-pd.Timedelta(days=1)].itertuples()])
    new = fast[fast.available_at > pd.Timestamp(acct['last_bar'])] if acct['last_bar'] else fast.iloc[0:0]
    before = len(acct['futures']['trades'])
    _step_bars(book, acct, new, pf, frames.get('funding'), manager, modes)
    decision = None
    closes = pf.index[(pf.index <= now) & (pf.index == pf.index.floor('15min'))]
    t15 = closes[-1] if len(closes) else None
    if t15 is not None and now-t15 <= FRESH and (acct['last_decision'] is None or t15 > pd.Timestamp(acct['last_decision'])):
        decision = _decide(book, acct, pf, table, t15, layers, modes, macro_events)
        for h in decision['hits']:
            h['history'] = setup_history.lookup(h['playbook'])   # what happened the last times this setup appeared
        journal.add_hits(engine, [h for h in decision['hits'] if h['status'] != 'NOT_CONFIRMED'])
        acct['checklist'] = [dict(playbook=h['playbook'], status=h['status'], passed=sum(c['ok'] for c in h['checks']),
                                  total=len(h['checks']), missing=h['missing'], trigger=h['trigger']['type'],
                                  time=h['time'], history=h.get('history'), layers=h.get('layers'))
                             for h in decision['hits']]
    for i, trade in enumerate(acct['futures']['trades'][before:], start=before):
        journal.add_trade(engine, trade['plan'].get('hit_id') or f'trade-{i}', trade)
    state = acct['futures']
    state['decisions'], state['rejections'] = state['decisions'][-500:], state['rejections'][-500:]
    journal.save_account(engine, ACCOUNT_ID, acct, now)
    inputs = decision.pop('features', {}) if decision else {}
    try:   # the four A8 accounts; a failure there never stops the reference account
        accounts = account_live.step_all(engine, book, pf, table, fast, decision, inputs, now, frames, macro_events,
                                         news_events, cfg=accounts_cfg, scorer=scorer)
    except Exception as exc:
        accounts = dict(error=f'{type(exc).__name__}: {exc}'[:300])
    diagnosed = _diagnose(engine, book, frames, fast, now)
    summary = dict(time=now.isoformat(), bars=len(new), triggers=recorded, decision=decision, diagnosed=diagnosed,
                   equity=state['equity'], position=bool(state['position']), pending=bool(state['pending']), accounts=accounts)
    try:
        feed_status(market_engine, 'rules:engine', dict(status='healthy', updated_at=now.isoformat(), bars=len(new),
                                                        last_bar=acct['last_bar'], last_decision=acct['last_decision'],
                                                        equity=state['equity'], rulebook=book['hash']))
    except Exception:
        pass
    return json.loads(json.dumps(journal.plain(summary), default=str))
