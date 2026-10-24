"""Versioned futures simulator (`futures-v1`): one pure reducer for historical replay and the forward
experimental paper account. Separate from the frozen `engine.py`, which is unchanged.

A decision taken at a bar's close fills at the NEXT bar's open, with slippage against the trader.
Existing protection is evaluated within each bar before the next decision; a bar touching both stop
and target counts as the stop; a gap through the stop fills at the open. Liquidation uses the mark
price under isolated margin (maintenance approximation). Stops only tighten and targets only move
closer; no adding, averaging down or same-step reversal. State is plain JSON for persistence.
See docs/design/specs/2026-10-20-trading-agent-step-a-design.md.
"""
from __future__ import annotations

import copy

import pandas as pd

from quantpilot.config import RiskConfig
from quantpilot.simulation.tradeplan import PLAN_RULES, reward_risk

VERSION = 'futures-v1'
_RISK = RiskConfig()
FEE, SLIPPAGE = _RISK.fee_bps/1e4, _RISK.slippage_bps/1e4
ROUND_TRIP = 2*(FEE+SLIPPAGE)
MAINTENANCE = _RISK.maintenance_margin
MAX_HOLD = pd.Timedelta(hours=24)
DAILY_LOSS, MAX_DRAWDOWN = _RISK.daily_loss, .10
BAR = pd.Timedelta(minutes=5)


def initial(balance=10_000.0, bar_minutes=5, daily_loss=DAILY_LOSS, max_drawdown=MAX_DRAWDOWN, floor=None):
    """`max_drawdown` or `floor` None switches that halt off (A8 accounts use a floor instead of the 10% halt)."""
    return dict(version=VERSION, bar_minutes=bar_minutes, balance=float(balance), cash=float(balance), equity=float(balance),
                peak=float(balance), day=None, day_start=float(balance), halted_day=False, drawdown_halt=False,
                paused=False, mark=None, last_time=None, position=None, pending=None, trades=[], rejections=[],
                funding=[], decisions=[], daily_loss=daily_loss, max_drawdown=max_drawdown, floor=floor, floor_hit=False)


def unrealized(s, price):
    p = s['position']
    return p['side']*p['quantity']*(price-p['entry']) if p else 0.0


def liquidation_price(side, entry, leverage):
    return entry*(1-1/leverage)/(1-MAINTENANCE) if side == 1 else entry*(1+1/leverage)/(1+MAINTENANCE)


def _reject(s, time, action, reason):
    s['rejections'].append(dict(time=time, action=action, reason=reason))


def _close(s, price, time, reason):
    p = s['position']
    fill = price*(1-p['side']*p.get('slippage', SLIPPAGE))
    pnl = p['side']*p['quantity']*(fill-p['entry'])
    if reason == 'LIQUIDATION':
        pnl = -p['margin']   # isolated margin: the loss is capped at the position's margin
    fee = p['quantity']*fill*FEE
    s['cash'] += pnl-fee
    fees = p['entry_fee']+fee
    s['trades'].append(dict(
        side=p['side'], quantity=p['quantity'], entry=p['entry'], exit=fill, entry_time=p['entry_time'],
        exit_time=time, reason=reason, pnl=pnl, fees=fees, funding=p['funding'], net=pnl-fees+p['funding'],
        return_on_equity=(pnl-fees+p['funding'])/p['equity_at_entry'], max_favorable=p['max_favorable'],
        max_adverse=p['max_adverse'], initial_stop=p['initial_stop'], initial_target=p['initial_target'],
        protection_changes=p['protection_changes'], plan=p['plan'], version=VERSION))
    s['position'] = None


def _open(s, plan, open_price, time):
    side = 1 if plan['side'] == 'long' else -1
    slippage = float(plan.get('slippage') or SLIPPAGE)   # research may stress slippage around anomalies
    fill = open_price*(1+side*slippage)
    ratio = reward_risk(plan['side'], fill, plan['stop'], plan['target'], plan['cost_fraction'])
    minimum = plan.get('min_reward_risk', PLAN_RULES['min_reward_risk'])
    if minimum is None:   # A8 accounts: the score replaced the minimum; the levels must still bracket the fill
        if side*(fill-float(plan['stop'])) <= 0 or side*(float(plan['target'])-fill) <= 0:
            return _reject(s, time, 'enter', 'INVALIDATED_AT_FILL')
    elif ratio is None or ratio < minimum:
        return _reject(s, time, 'enter', 'INVALIDATED_AT_FILL')
    quantity, leverage = float(plan['quantity']), float(plan.get('leverage', PLAN_RULES['leverage']))
    margin = quantity*fill/leverage
    if quantity <= 0 or margin > s['cash']+1e-9:
        return _reject(s, time, 'enter', 'INSUFFICIENT_MARGIN')
    fee = quantity*fill*FEE
    s['cash'] -= fee
    s['position'] = dict(side=side, quantity=quantity, entry=fill, entry_time=time, stop=float(plan['stop']),
                         target=float(plan['target']), initial_stop=float(plan['stop']),
                         initial_target=float(plan['target']), leverage=leverage, margin=margin,
                         liquidation=liquidation_price(side, fill, leverage), entry_fee=fee, funding=0.0,
                         equity_at_entry=s['equity'], max_favorable=0.0, max_adverse=0.0, protection_changes=[],
                         slippage=slippage,
                         plan={k: plan.get(k) for k in ('side', 'stop', 'target', 'reward_risk', 'rules_version',
                                                         'context_cutoff', 'playbook', 'hit_id')})


def _modify(s, kind, price, open_price, time):
    p = s['position']
    side = p['side']
    if kind == 'tighten_stop':
        ok = side*(price-p['stop']) > 0 and side*(open_price-price) > 0     # closer to the price, never past it
        key, reason = 'stop', 'INVALID_STOP_CHANGE'
    else:
        ok = side*(p['target']-price) > 0 and side*(price-open_price) > 0   # closer to the price, never past it
        key, reason = 'target', 'INVALID_TARGET_CHANGE'
    if not ok:
        return _reject(s, time, kind, reason)
    p[key] = float(price)
    p['protection_changes'].append({'time': time, key: float(price)})


def _execute_pending(s, event, time):
    order, s['pending'] = s['pending'], None
    if not order:
        return
    kind, o = order['action'], float(event['open'])
    if kind == 'enter':
        if s['position']:
            return _reject(s, time, kind, 'POSITION_OPEN')
        if s['halted_day'] or s['drawdown_halt'] or s['paused'] or s.get('floor_hit'):
            return _reject(s, time, kind, 'ACCOUNT_HALTED')
        return _open(s, order['plan'], o, time)
    if not s['position']:
        return _reject(s, time, kind, 'NO_POSITION')
    if kind == 'exit':
        return _close(s, o, time, 'EXIT_ACTION')
    return _modify(s, kind, float(order['price']), o, time)


def _protect(s, event, time):
    """Liquidation, stop and target inside the bar (stop first when ambiguous), then the time limit."""
    p = s['position']
    if not p:
        return
    side, o, h, l, c = p['side'], *(float(event[k]) for k in ('open', 'high', 'low', 'close'))
    mark = float(event.get('mark', c))
    p['max_favorable'] = max(p['max_favorable'], side*((h if side == 1 else l)/p['entry']-1))
    p['max_adverse'] = min(p['max_adverse'], side*((l if side == 1 else h)/p['entry']-1))
    if side*(o-p['liquidation']) <= 0:
        return _close(s, o, time, 'LIQUIDATION')
    if side*(p['stop']-o) >= 0:
        return _close(s, o, time, 'STOP')                       # gapped through the stop: fill at the open
    stopped = l <= p['stop'] if side == 1 else h >= p['stop']
    reached = h >= p['target'] if side == 1 else l <= p['target']
    if stopped:
        return _close(s, p['stop'], time, 'STOP')
    if reached:
        return _close(s, p['target'], time, 'TARGET')
    if side*(mark-p['liquidation']) <= 0:
        return _close(s, mark, time, 'LIQUIDATION')
    if pd.Timestamp(time)-pd.Timestamp(p['entry_time']) >= MAX_HOLD:
        return _close(s, c, time, 'TIME_EXIT')


def _bar(s, event):
    time = pd.Timestamp(event['time'])
    if s['last_time'] is not None and time <= pd.Timestamp(s['last_time']):
        raise ValueError('bars must be strictly increasing')
    iso = time.isoformat()
    day = (time-pd.Timedelta(minutes=s.get('bar_minutes', 5))).strftime('%Y-%m-%d')   # the bar's opening day
    if s['day'] != day:
        s.update(day=day, day_start=s['equity'], halted_day=False)
    _execute_pending(s, event, iso)
    _protect(s, event, iso)
    close = float(event['close'])
    s['mark'], s['last_time'] = float(event.get('mark', close)), iso
    s['equity'] = s['cash']+unrealized(s, close)   # funding is already settled into cash
    if s['equity'] <= s['day_start']*(1-s.get('daily_loss', DAILY_LOSS)) and not s['halted_day']:
        if s['position']:
            _close(s, close, iso, 'DAILY_LOSS_HALT')
        s['halted_day'] = True
    s['equity'] = s['cash']+unrealized(s, close)
    s['peak'] = max(s['peak'], s['equity'])
    limit = s['max_drawdown'] if 'max_drawdown' in s else MAX_DRAWDOWN
    if limit is not None and s['equity'] <= s['peak']*(1-limit) and not s['drawdown_halt']:
        if s['position']:
            _close(s, close, iso, 'DRAWDOWN_HALT')
        s['drawdown_halt'] = True
        s['equity'] = s['cash']
    if s.get('floor') is not None and s['equity'] <= s['floor'] and not s.get('floor_hit'):
        if s['position']:
            _close(s, close, iso, 'FLOOR_HIT')
        s['floor_hit'] = True
        s['equity'] = s['cash']


def _funding(s, event):
    p = s['position']
    if not p:
        return
    amount = -p['side']*p['quantity']*float(event['mark'])*float(event['rate'])
    s['cash'] += amount
    p['funding'] += amount
    s['funding'].append(dict(time=pd.Timestamp(event['time']).isoformat(), rate=float(event['rate']), amount=amount))


def _action(s, event):
    kind = event['action']
    s['decisions'].append(dict(time=pd.Timestamp(event['time']).isoformat(), action=kind))
    if kind in ('hold', 'wait'):
        return
    if s['pending']:
        return _reject(s, event['time'], kind, 'ORDER_PENDING')
    if kind == 'enter' and s['position']:
        return _reject(s, event['time'], kind, 'POSITION_OPEN')
    s['pending'] = dict(action=kind, plan=event.get('plan'), price=event.get('price'), decided_at=event['time'])


def step(state, event, inplace=False):
    """Apply one event (bar, funding, action or control) and return the new state."""
    s = state if inplace else copy.deepcopy(state)
    kind = event['kind']
    if kind == 'bar':
        _bar(s, event)
    elif kind == 'funding':
        _funding(s, event)
    elif kind == 'action':
        _action(s, event)
    elif kind == 'control':
        if 'paused' in event:
            s['paused'] = bool(event['paused'])
        if event.get('review'):   # the owner reviewed a drawdown halt: resume and measure from here
            s['drawdown_halt'], s['peak'] = False, s['equity']
    else:
        raise ValueError(f'unknown event kind {kind}')
    return s


def replay(state, events):
    s = copy.deepcopy(state)
    for event in events:
        step(s, event, inplace=True)
    return s
