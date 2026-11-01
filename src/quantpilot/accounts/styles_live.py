"""accounts-v2 live engine (A10): four paper accounts, each running its own style, every minute.

Called from `rule_live.cycle` after the reference account; it replaces the A8 score-model fan-out, whose four
accounts are marked RETIRED (their trades and reviews stay in the journal). Trend Rider decides at each daily close,
Range Trader and Breakout Hunter at each 15m close, the Funding Collector at each funding settlement. Rules never
change here; each account pauses for a review with the owner (after 30 trades, or 30 days for the collector).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from quantpilot.accounts import config as accounts
from quantpilot.accounts import journal as account_journal
from quantpilot.accounts import review as account_review
from quantpilot.rules import journal
from quantpilot.simulation import carry as ledger
from quantpilot.simulation import futures
from quantpilot.strategies import base, breakout, carry, market, range_trader, timemachine, trend

MODULES = dict(T1=trend, R1=range_trader, B1=breakout, C1=carry)
STOPPED = ('PAUSED_FOR_REVIEW', 'FLOOR_HIT')
FRESH = pd.Timedelta(minutes=5)
DAILY_FRESH = pd.Timedelta(hours=1)
SETTLEMENT = pd.Timedelta(hours=8)
_cache: dict = {}


def new_state(cfg, account, now, last_bar):
    if account['style'] == 'C1':
        state = ledger.initial(cfg['balance'])
    else:
        state = futures.initial(cfg['balance'], bar_minutes=1, daily_loss=account['daily_brake'], max_drawdown=None,
                                floor=cfg['floor'])
    iso = pd.Timestamp(now).isoformat()
    return dict(futures=state, style=account['style'], status='RUNNING', round=1, in_round=0, number=0, last_bar=last_bar,
                last_decision=None, last_daily=None, last_funding=None, blocked=None, extra={}, created=iso,
                round_started=iso, active=None, config=cfg.get('hash'))


def _frame(frames):
    candles = frames['candles']
    key = (len(candles), str(pd.to_datetime(candles.available_at, utc=True).max()))
    if _cache.get('key') != key:
        _cache.update(key=key, sf=market.style_frame(candles))
    return _cache['sf']


def _daily(market_engine, now):
    day = pd.Timestamp(now).floor('D')
    if _cache.get('daily_day') != day:
        from quantpilot.research.event_study import load_market
        frames = load_market(market_engine, day-pd.Timedelta(days=200), pd.Timestamp(now)+pd.Timedelta(minutes=1), families=())
        _cache.update(daily_day=day, daily=market.daily_bars(frames['candles']))
    return _cache['daily']


def _closes(history, market_engine, t):
    if history is None:
        from quantpilot.research.event_study import load_market
        history = load_market(market_engine, t-pd.Timedelta(days=61), t+pd.Timedelta(minutes=1), families=())['candles']
    c = market.candles_of(history)
    c = c[c.available_at <= t]
    return pd.Series(c.close.to_numpy(float), index=pd.DatetimeIndex(c.available_at))


def _history(style):
    """The style's development-history trades in the 2 years before the development cutoff (A10 Time Machine)."""
    runs = sorted(Path('artifacts/style-research').glob(f'run-*/runs/{style}.json'))
    if not runs:
        return dict(available=False, note='History test still running.')
    trades = json.loads(runs[-1].read_text(encoding='utf-8'))['trades']
    cut = pd.Timestamp('2023-10-20', tz='UTC')
    recent = [t for t in trades if pd.Timestamp(t['entry_time']) >= cut]
    nets = [t['net'] for t in recent]
    return dict(available=True, trades=len(recent), win_rate=sum(n > 0 for n in nets)/len(nets) if nets else None,
                mean_return=sum(t['return_on_equity'] for t in recent)/len(recent) if recent else None)


def _record(engine, account, acct, before, round_length):
    trades = acct['futures']['trades']
    for i, trade in enumerate(trades[before:], start=before):
        acct['number'] += 1
        acct['in_round'] += 1
        account_journal.add_trade(engine, account['id'], acct['round'], acct['number'], trade, i)
    acct['futures']['trades'] = trades[-200:]       # the journal keeps every trade; the state keeps recent ones
    if acct['futures'].get('floor_hit'):
        acct['status'] = 'FLOOR_HIT'
    elif account['style'] != 'C1' and acct['in_round'] >= round_length and acct['status'] != 'PAUSED_FOR_REVIEW':
        acct['status'], acct['futures']['paused'] = 'PAUSED_FOR_REVIEW', True
    return len(acct['futures']['trades'])


def _settlements(funding, after, now):
    if funding is None or not len(funding):
        return funding.iloc[0:0] if funding is not None else None
    f = funding.assign(available_at=pd.to_datetime(funding.available_at, utc=True),
                       event_time=pd.to_datetime(funding.event_time, utc=True)).sort_values('event_time')
    lo = pd.Timestamp(after) if after else pd.Timestamp(now)-SETTLEMENT
    return f[(f.available_at > lo) & (f.available_at <= now)]


def _step_futures(acct, bars, funding):
    s, previous = acct['futures'], pd.Timestamp(acct['last_bar']) if acct['last_bar'] else None
    rates = None
    if funding is not None and len(funding):
        rates = funding.assign(t=pd.to_datetime(funding.event_time, utc=True)).set_index('t').funding_rate.astype(float)
    for bar in bars.itertuples():
        t = bar.available_at
        futures.step(s, dict(kind='bar', time=t.isoformat(), open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                             mark=bar.close), inplace=True)
        if rates is not None and previous is not None:
            for rate in rates[(rates.index > previous) & (rates.index <= t)]:
                futures.step(s, dict(kind='funding', time=t.isoformat(), rate=float(rate), mark=bar.close), inplace=True)
        previous = t
        acct['last_bar'] = t.isoformat()


def _snapshot(row, funding):
    num = lambda v: None if v is None or pd.isna(v) else float(v)
    rate = None if funding is None or not len(funding) else num(funding.sort_values('event_time').funding_rate.iloc[-1])
    if row is None:
        return dict(funding_rate=rate)
    return dict(close=num(row.get('close')), regime=row.get('regime'), atr_pct_1h=num(row.get('atr_pct_1h')),
                band4_high=num(row.get('band4_high')), band4_low=num(row.get('band4_low')), funding_rate=rate)


def _decision(account, acct, intent, status, snapshot, sizing=None, tm=None):
    return dict(account=account['id'], hit_id=intent.id, time=intent.time, decision=status, score=None, confidence=None,
                cutoff=None, trade_id=f"{account['id']}:{intent.id}" if status == 'TAKEN' else None,
                payload=dict(style=intent.style, side=intent.side, reason=intent.reason, regime=snapshot.get('regime'),
                             plan=dict(ref=intent.ref, stop=intent.stop, target=intent.target, limit=intent.limit,
                                       valid_minutes=intent.valid_minutes, max_hold_hours=intent.max_hold_hours,
                                       risk_scale=intent.risk_scale),
                             sizing=sizing, snapshot=snapshot, time_machine=tm, active=acct.get('active')))


def _try_enter(engine, account, acct, intent, snapshot, macro_events, history, market_engine):
    s = acct['futures']
    if acct['status'] in STOPPED or s['paused']:
        status = 'PAUSED'
    elif s['halted_day'] or s.get('floor_hit'):
        status = 'HALTED'
    elif s['position']:
        status = 'POSITION_OPEN'
    elif s['pending']:
        status = 'ORDER_PENDING'
    elif base.in_blackout(intent.time, macro_events):
        status = 'MACRO_BLACKOUT'
    elif acct.get('blocked') == intent.side and intent.style == 'T1':
        status = 'WAITING_AFTER_STOP'
    else:
        status = 'TAKEN'
    sizing, tm = None, None
    if status == 'TAKEN':
        plan = base.plan(intent, s['equity'])
        sizing = dict(quantity=plan['quantity'], leverage=plan['leverage'], risk_fraction=base.RISK*intent.risk_scale,
                      risk_amount=plan['quantity']*abs(intent.ref-intent.stop), notional=plan['quantity']*intent.ref)
        try:
            hours = 24*30 if intent.style == 'T1' else intent.max_hold_hours
            tm = timemachine.odds(_closes(history, market_engine, intent.time), intent.time, intent.side, intent.ref,
                                  intent.stop, intent.target, hours)
        except Exception as exc:     # the Time Machine is a record, never a gate
            tm = dict(error=f'{type(exc).__name__}: {exc}'[:200])
        tm['history'] = _history(intent.style)
        acct['extra'][intent.id] = dict(time_machine=tm, reason=intent.reason)
        futures.step(s, dict(kind='action', time=intent.time.isoformat(), action='enter', plan=plan), inplace=True)
    account_journal.add_decisions(engine, [_decision(account, acct, intent, status, snapshot, sizing, tm)])
    return status


def _step_price_style(engine, account, acct, sf, frames, new, now, macro_events, daily, history, market_engine, round_length):
    style, module = account['style'], MODULES[account['style']]
    s = acct['futures']
    before = len(s['trades'])
    _step_futures(acct, new, frames.get('funding'))
    for tr in s['trades'][before:]:
        if style == 'T1' and tr['reason'] == 'STOP':
            acct['blocked'] = 'long' if tr['side'] == 1 else 'short'
    _record(engine, account, acct, before, round_length)
    if acct['status'] not in STOPPED:
        acct['status'] = 'RUNNING'
    if style == 'T1':
        if daily is None or not len(daily):
            acct['active'] = dict(on=False, reason='Off: waiting for daily history.')
            return
        f = trend.frame(daily)
        closes = f.index[f.index <= now]
        if not len(closes):
            return
        d = closes[-1]
        on, reason = trend.active(f, d)
        acct['active'] = dict(on=on, reason=reason, as_of=d.isoformat())
        vote = int(f.loc[d].vote)
        if acct.get('blocked') and vote != (1 if acct['blocked'] == 'long' else -1):
            acct['blocked'] = None
        if acct['last_daily'] and d <= pd.Timestamp(acct['last_daily']):
            return
        acct['last_daily'] = d.isoformat()
        if now-d > DAILY_FRESH:
            return
        p = s['position']
        if p is not None and s['pending'] is None and not trend.holds(f, d, 'long' if p['side'] == 1 else 'short'):
            futures.step(s, dict(kind='action', time=d.isoformat(), action='exit'), inplace=True)
            return
        it = trend.intent(f, d)
        if it is not None and p is None:
            _try_enter(engine, account, acct, it, dict(_snapshot(None, frames.get('funding')), close=float(f.loc[d].close)),
                       macro_events, history, market_engine)
        return
    closes = sf.index[sf.index <= now]
    if not len(closes):
        return
    t15 = closes[-1]
    on, reason = module.active(sf, t15)
    acct['active'] = dict(on=on, reason=reason, as_of=t15.isoformat())
    if acct['last_decision'] and t15 <= pd.Timestamp(acct['last_decision']):
        return
    acct['last_decision'] = t15.isoformat()
    if now-t15 > FRESH:
        return
    sig = module.signals(sf.loc[:t15])
    if t15 in sig.index:
        it = base.to_intent(style, t15, sig.loc[t15].to_dict())
        _try_enter(engine, account, acct, it, _snapshot(sf.loc[t15].to_dict(), frames.get('funding')), macro_events,
                   history, market_engine)


def _step_carry(engine, account, acct, frames, new, now, review_days, last_close=None):
    s = acct['futures']
    funding = frames.get('funding')
    settle = _settlements(funding, acct['last_funding'], now)
    rates_all = funding.assign(t=pd.to_datetime(funding.event_time, utc=True)).sort_values('t') if funding is not None and len(funding) else None
    bars = list(new.itertuples())
    price = s['last_price'] or (float(bars[0].close) if bars else last_close)
    queue = list(settle.itertuples()) if settle is not None else []
    before = len(s['trades'])

    def settle_one(row, price):
        if s['open']:
            ledger.fund(s, row.funding_rate, price, row.event_time)
        history = rates_all[rates_all.t <= row.event_time].funding_rate.tolist() if rates_all is not None else []
        action = carry.decide(history, s['open'])
        if action == 'open' and acct['status'] not in STOPPED:
            ledger.open_(s, price, row.available_at)
        elif action == 'close':
            ledger.close(s, price, row.available_at, 'FUNDING_TURNED')
        acct['last_funding'] = pd.Timestamp(row.available_at).isoformat()

    for bar in bars:
        price = float(bar.close)
        while queue and pd.Timestamp(queue[0].available_at) <= bar.available_at:
            settle_one(queue.pop(0), price)
        if s['open']:
            ledger.maybe_rebalance(s, price, bar.available_at)
            if ledger.equity(s, price) <= acct.get('floor', 5_000.0):
                ledger.close(s, price, bar.available_at, 'FLOOR_HIT')
                s['floor_hit'] = True
        ledger.mark(s, price, bar.available_at)
        acct['last_bar'] = bar.available_at.isoformat()
    for row in queue:
        if price is not None:
            settle_one(row, price)
    _record(engine, account, acct, before, 10**9)
    if acct['status'] not in STOPPED:
        acct['status'] = 'RUNNING'
        if now-pd.Timestamp(acct['round_started']) >= pd.Timedelta(days=review_days):
            acct['status'], s['paused'] = 'PAUSED_FOR_REVIEW', True
    history = rates_all[rates_all.t <= now].funding_rate.tolist() if rates_all is not None else []
    on, reason = carry.active(history, s['open'])
    acct['active'] = dict(on=on, reason=reason, as_of=pd.Timestamp(now).isoformat())


def retire(engine, ids, now):
    """Mark the A8 accounts RETIRED (closing any open paper position at its last price). Their journal stays."""
    for account_id in ids or []:
        acct = journal.load_account(engine, account_id)
        if not acct or acct.get('status') == 'RETIRED':
            continue
        s = acct.get('futures') or {}
        if s.get('position') and s.get('mark'):
            futures._close(s, s['mark'], pd.Timestamp(now).isoformat(), 'RETIRED')
        acct['status'], acct['retired_at'] = 'RETIRED', pd.Timestamp(now).isoformat()
        journal.save_account(engine, account_id, acct, now)


def step_all(engine, frames, fast, now, macro_events, cfg=None, daily=None, history=None, market_engine=None):
    """One minute for every accounts-v2 account. Returns {account id: summary}."""
    cfg = cfg or accounts.load()
    now = pd.Timestamp(now)
    retire(engine, cfg.get('retired'), now)
    sf = _frame(frames)
    if daily is None and market_engine is not None and any(a['style'] == 'T1' for a in cfg['accounts']):
        daily = _daily(market_engine, now)
    out = {}
    for account in cfg['accounts']:
        acct = journal.load_account(engine, account['id'])
        if acct is None:
            last = fast.available_at.max().isoformat() if len(fast) else now.floor('min').isoformat()
            acct = new_state(cfg, account, now, last)
        new = fast[fast.available_at > pd.Timestamp(acct['last_bar'])] if acct['last_bar'] else fast.iloc[0:0]
        if account['style'] == 'C1':
            acct['floor'] = cfg['floor']
            _step_carry(engine, account, acct, frames, new, now, account.get('review_days', 30),
                        float(fast.close.iloc[-1]) if len(fast) else None)
        else:
            _step_price_style(engine, account, acct, sf, frames, new, now, macro_events, daily, history,
                              market_engine or engine, cfg['round_length'])
        s = acct['futures']
        s['decisions'], s['rejections'] = s.get('decisions', [])[-500:], s.get('rejections', [])[-500:]
        journal.save_account(engine, account['id'], acct, now)
        out[account['id']] = dict(style=account['style'], status=acct['status'], equity=s['equity'], trades=acct['number'],
                                  active=(acct.get('active') or {}).get('on'))
    try:
        out['diagnosed'] = account_review.diagnose_trades(engine, fast, now)
    except Exception as exc:
        out['diagnosed'] = f'{type(exc).__name__}: {exc}'[:200]
    return out
