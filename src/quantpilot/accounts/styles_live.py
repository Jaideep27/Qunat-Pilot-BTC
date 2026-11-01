"""accounts-v2 live engine (A10): four paper accounts, each running its own style, every minute.

Called from `rule_live.cycle` after the reference account; it replaces the A8 score-model fan-out, whose four
accounts are marked RETIRED (their trades and reviews stay in the journal). Trend Rider decides at each daily close,
Range Trader and Breakout Hunter at each 15m close, the Funding Collector at each funding settlement. Rules never
change here; each account pauses for a review with the owner (after 30 trades, or 30 days for the collector).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from quantpilot.accounts import config as accounts
from quantpilot.accounts import journal as account_journal
from quantpilot.accounts import review as account_review
from quantpilot.rules import journal
from quantpilot.simulation import carry as ledger
from quantpilot.simulation import futures
from quantpilot.strategies import base, bot, breakout, carry, combined, market, range_trader, timemachine, trend

TREND_BOT = SimpleNamespace(NAME=combined.TREND_NAME, RULES=combined.TREND_RULES)
MODULES = dict(T1=trend, R1=range_trader, B1=breakout, C1=carry, X1=combined, X2=TREND_BOT)
HIT_OK = ('TAKEN', 'SKIPPED_LOWER_PRIORITY', 'POSITION_OPEN')     # A11: setups whose plan was valid
STOPPED = ('PAUSED_FOR_REVIEW', 'FLOOR_HIT')
FRESH = pd.Timedelta(minutes=5)
DAILY_FRESH = pd.Timedelta(hours=1)
SETTLEMENT = pd.Timedelta(hours=8)
_cache: dict = {}


def new_state(cfg, account, now, last_bar):
    if account['style'] in ('X1', 'X2'):
        st = bot.new(cfg['balance'], now=now, daily_brake=account['daily_brake'], floor=cfg['floor'], bar_minutes=1)
        iso = pd.Timestamp(now).isoformat()
        return dict(st, style=account['style'], blocked=None, status='RUNNING', round=1, in_round=0, number=0, last_bar=last_bar, last_decision=None,
                    last_funding=None, recent=[], members=None, extra={}, created=iso, round_started=iso, active=None,
                    config=cfg.get('hash'))
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


def _rates(funding):
    if funding is None or not len(funding):
        return None
    f = funding.assign(event_time=pd.to_datetime(funding.event_time, utc=True),
                       available_at=pd.to_datetime(funding.available_at, utc=True)).sort_values('event_time')
    return f.drop_duplicates('event_time', keep='last')


def _plain_candidate(c):
    return {k: (pd.Timestamp(v).isoformat() if k == 'time' else v) for k, v in c.items()}


def _candidates(sf, t15, hits, stops, f, daily_close):
    out = []
    for source, module in (('R1', range_trader), ('B1', breakout)):
        sig = module.signals(sf.loc[:t15])
        if t15 in sig.index:
            out += combined.from_signals(sig.loc[[t15]], source).get(t15, [])
    for h in hits or []:
        if h.get('plan') and h.get('status') in HIT_OK and pd.Timestamp(h['time']) == t15:
            out.append(combined.from_hit(dict(h, time=t15), stops))
    if daily_close is not None and f is not None:
        it = trend.intent(f, daily_close)
        if it is not None:
            out.append(combined.from_trend(dict_intent(it, t15)))
    return out


def dict_intent(it, t):
    it.time = pd.Timestamp(t)
    return it


def _step_combined(engine, account, acct, sf, frames, new, now, macro_events, daily, history, market_engine, hits,
                   round_length, mode='X1'):
    s = acct['futures']
    before = len(s['trades'])
    rates = _rates(frames.get('funding'))
    stopped = acct['status'] in STOPPED
    previous = pd.Timestamp(acct['last_funding']) if acct['last_funding'] else pd.Timestamp(now)-SETTLEMENT
    for bar in new.itertuples():
        t = bar.available_at
        due = rates[(rates.available_at > previous) & (rates.available_at <= t)] if rates is not None else None
        position_rates = due.funding_rate.astype(float).tolist() if due is not None else []
        bot.on_bar(acct, t, bar.open, bar.high, bar.low, bar.close, position_rates)
        for row in (due.itertuples() if due is not None else []):
            history_rates = rates[rates.event_time <= row.event_time].funding_rate.astype(float).iloc[-21:].tolist()
            bot.on_settlement(acct, t, float(row.funding_rate), bar.close, history_rates, allow_park=not stopped)
            previous = row.available_at
            acct['last_funding'] = pd.Timestamp(row.available_at).isoformat()
        acct['last_bar'] = t.isoformat()
    for tr in s['trades'][before:]:
        if mode == 'X2' and tr['reason'] == 'STOP':
            acct['blocked'] = 'long' if tr['side'] == 1 else 'short'
    _record(engine, account, acct, before, round_length)
    for cycle in acct['cycles']:      # idle-hedge cycles: journaled, not counted toward the review
        account_journal.add_trade(engine, account['id'], acct['round'], 0, cycle)
    if acct['status'] not in STOPPED:
        acct['status'] = 'RUNNING'
    closes = sf.index[sf.index <= now]
    if not len(closes):
        return
    t15 = closes[-1]
    f = trend.frame(daily) if daily is not None and len(daily) else None
    vote = int(f.vote.asof(t15)) if f is not None and t15 >= f.index[0] else 0
    row = sf.loc[t15].to_dict()
    regime = row.get('regime')
    on, reason = combined.active(regime, vote)
    if mode == 'X2':
        side = {1: 'long', -1: 'short'}.get(vote)
        on = side is not None
        reason = (f"On: the daily trend is {'up' if vote == 1 else 'down'}; it waits for another strategy or "
                  f"{'support' if vote == 1 else 'resistance'} to time a {side}.") if on else \
            'Off: no clear daily trend (20/60/120-day trends and the 50-day average disagree), so it stays out.'
        if acct.get('blocked') and vote != (1 if acct['blocked'] == 'long' else -1) and f is not None and t15 in f.index:
            acct['blocked'] = None
    acct['active'] = dict(on=on, reason=reason, as_of=t15.isoformat())
    hedge = acct.get('hedge')
    acct['members'] = dict(regime=regime, vote=vote, leaders=list(combined.LEADERS.get(regime, ())),
                           hedge=bool(hedge and hedge['open']), as_of=t15.isoformat(),
                           range_trader=range_trader.active(sf, t15)[1], breakout=breakout.active(sf, t15)[1])
    if acct['last_decision'] and t15 <= pd.Timestamp(acct['last_decision']):
        return
    acct['last_decision'] = t15.isoformat()
    if now-t15 > FRESH:
        return
    daily_close = t15 if f is not None and t15 in f.index else None
    p = s['position']
    if daily_close is not None and acct.get('leader') == 'T1' and p and s['pending'] is None and             not trend.holds(f, daily_close, 'long' if p['side'] == 1 else 'short'):
        bot.exit_(acct, t15)
    from quantpilot.rules import rulebook
    current = _candidates(sf, t15, hits, combined.time_stops(rulebook.cached()), f, daily_close if mode == 'X1' else None)
    recent = [dict(c, time=pd.Timestamp(c['time'])) for c in acct.get('recent') or []]
    recent = [c for c in recent if c['time'] >= t15-combined.WINDOW]
    acct['recent'] = [_plain_candidate(c) for c in recent+current]
    if mode == 'X2':
        if not vote or not bot.flat(acct):
            return
        drow = f.loc[f.index[f.index <= t15][-1]].to_dict()
        out = combined.trend_led(vote, drow, current, recent, row, t15, acct.get('blocked'))
        out.update(leaders=['T1'])
        if out['chosen'] is None and not current:
            return          # nothing to record: no member spoke and no level was near
    elif not current:
        return
    else:
        out = combined.decide(regime, vote, current, recent, row)
    chosen = out['chosen']
    if chosen is None and mode == 'X2':
        status = out['status']
    elif chosen is None:
        status = sorted((c['status'] for c in out['considered']),
                        key=['NO_SECOND_OPINION', 'VETOED', 'NOT_LEADER'].index)[0]
    elif acct['status'] in STOPPED or s['paused']:
        status = 'PAUSED'
    elif s['halted_day'] or s.get('floor_hit'):
        status = 'HALTED'
    elif not bot.flat(acct):
        status = 'POSITION_OPEN'
    elif base.in_blackout(t15, macro_events):
        status = 'MACRO_BLACKOUT'
    else:
        status = 'TAKEN'
    sizing, tm = None, None
    hit_id = f'{mode}:{t15.isoformat()}'
    if status == 'TAKEN':
        plan = combined.to_plan(chosen, s['equity'], t15)
        plan['hit_id'] = hit_id
        ref = chosen['limit'] if chosen['limit'] is not None else chosen['ref']
        sizing = dict(quantity=plan['quantity'], leverage=plan['leverage'], risk_fraction=base.RISK*chosen['risk_scale'],
                      risk_amount=plan['quantity']*abs(ref-chosen['stop']), notional=plan['quantity']*ref)
        try:
            hours = 24*30 if chosen['source'] == 'T1' else chosen['max_hold_hours']
            tm = timemachine.odds(_closes(history, market_engine, t15), t15, chosen['side'], ref, chosen['stop'],
                                  chosen['target'], hours)
        except Exception as exc:
            tm = dict(error=f'{type(exc).__name__}: {exc}'[:200])
        acct['extra'][hit_id] = dict(time_machine=tm, reason=f"{chosen['source']}: {chosen['reason']}",
                                     agreements=chosen['agreements'])
        price = float(new.close.iloc[-1]) if len(new) else float(row.get('close'))
        bot.enter(acct, plan, price, t15, leader=chosen['source'])
    plain = lambda c: {k: v for k, v in _plain_candidate(c).items() if k not in ('reason',)} | dict(reason=c.get('reason'))
    account_journal.add_decisions(engine, [dict(
        account=account['id'], hit_id=hit_id, time=t15, decision=status, score=None, confidence=None, cutoff=None,
        trade_id=f"{account['id']}:{hit_id}" if status == 'TAKEN' else None,
        payload=dict(style=mode, regime=regime, vote=vote, leaders=out['leaders'], considered=[plain(c) for c in out['considered']],
                     chosen=plain(chosen) if chosen else None, side=chosen['side'] if chosen else None,
                     reason=f"{chosen['source']} led, backed by {', '.join(chosen['agreements'])}" if chosen else None,
                     sizing=sizing, time_machine=tm, snapshot=_snapshot(row, frames.get('funding')), active=acct['active']))])


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


def step_all(engine, frames, fast, now, macro_events, cfg=None, daily=None, history=None, market_engine=None, hits=None):
    """One minute for every accounts-v2 account. Returns {account id: summary}."""
    cfg = cfg or accounts.load()
    now = pd.Timestamp(now)
    retire(engine, cfg.get('retired'), now)
    sf = _frame(frames)
    if daily is None and market_engine is not None and any(a['style'] in ('T1', 'X1', 'X2') for a in cfg['accounts']):
        daily = _daily(market_engine, now)
    out = {}
    for account in cfg['accounts']:
        acct = journal.load_account(engine, account['id'])
        if acct is None:
            last = fast.available_at.max().isoformat() if len(fast) else now.floor('min').isoformat()
            acct = new_state(cfg, account, now, last)
        new = fast[fast.available_at > pd.Timestamp(acct['last_bar'])] if acct['last_bar'] else fast.iloc[0:0]
        if account['style'] in ('X1', 'X2'):
            _step_combined(engine, account, acct, sf, frames, new, now, macro_events, daily, history, market_engine or engine,
                           hits, cfg['round_length'], mode=account['style'])
        elif account['style'] == 'C1':
            acct['floor'] = cfg['floor']
            _step_carry(engine, account, acct, frames, new, now, account.get('review_days', 30),
                        float(fast.close.iloc[-1]) if len(fast) else None)
        else:
            _step_price_style(engine, account, acct, sf, frames, new, now, macro_events, daily, history,
                              market_engine or engine, cfg['round_length'])
        s = acct['futures']
        s['decisions'], s['rejections'] = s.get('decisions', [])[-500:], s.get('rejections', [])[-500:]
        journal.save_account(engine, account['id'], acct, now)
        equity = bot.equity(acct) if account['style'] in ('X1', 'X2') else s['equity']
        out[account['id']] = dict(style=account['style'], status=acct['status'], equity=equity, trades=acct['number'],
                                  active=(acct.get('active') or {}).get('on'))
    try:
        out['diagnosed'] = account_review.diagnose_trades(engine, fast, now)
    except Exception as exc:
        out['diagnosed'] = f'{type(exc).__name__}: {exc}'[:200]
    return out
