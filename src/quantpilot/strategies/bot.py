"""The Combined Bot's single book (A11): one futures-v1 ledger for price trades plus, while idle, a carry-v1 hedge.

The hedge is sized from the futures equity and its result is handed back to the futures cash when it closes, so the
daily brake and the floor keep watching one number. Only one of the two is ever open. Used by the live account and
by the history test, so both run the same rules. State is plain JSON.
"""
import pandas as pd

from quantpilot.simulation import carry as ledger
from quantpilot.simulation import futures
from quantpilot.strategies import combined


def new(balance, now=None, daily_brake=.03, floor=5_000.0, bar_minutes=1):
    return dict(futures=futures.initial(balance, bar_minutes=bar_minutes, daily_loss=daily_brake, max_drawdown=None,
                                        floor=floor),
                hedge=None, flat_since=None if now is None else pd.Timestamp(now).isoformat(), cycles=[], leader=None)


def flat(st):
    s = st['futures']
    return s['position'] is None and s['pending'] is None


def equity(st, price=None):
    h = st['hedge']
    extra = 0.0
    if h:
        extra = ledger.equity(h, price if price is not None else h['last_price'])-h['balance']
    return st['futures']['equity']+extra


def on_bar(st, t, o, h, l, c, rates=()):
    s = st['futures']
    t = pd.Timestamp(t)
    futures.step(s, dict(kind='bar', time=t.isoformat(), open=o, high=h, low=l, close=c, mark=c), inplace=True)
    for rate in rates:
        futures.step(s, dict(kind='funding', time=t.isoformat(), rate=float(rate), mark=c), inplace=True)
    hedge = st['hedge']
    if hedge and hedge['open']:
        ledger.maybe_rebalance(hedge, c, t)
        ledger.mark(hedge, c, t)
    if flat(st):
        st['flat_since'] = st['flat_since'] or t.isoformat()
        st['leader'] = None
    else:
        st['flat_since'] = None


def _open_hedge(st, price, t):
    h = ledger.initial(st['futures']['equity'])
    ledger.open_(h, price, t)
    st['hedge'] = h


def _close_hedge(st, price, t, reason):
    h = st['hedge']
    ledger.close(h, price, t, reason)
    delta = h['cash']-h['balance']
    st['futures']['cash'] += delta
    st['futures']['equity'] += delta
    st['cycles'].append(h['trades'][-1])
    st['cycles'] = st['cycles'][-200:]
    st['hedge'] = None


def on_settlement(st, t, rate, price, history, allow_park=True):
    """At a funding settlement: earn on the hedge and close it if funding turned, or park idle money.
    Returns 'parked', 'unparked' or None."""
    h = st['hedge']
    if h and h['open']:
        ledger.fund(h, rate, price, t)
        if combined.unpark(history):
            _close_hedge(st, price, t, 'FUNDING_TURNED')
            return 'unparked'
        return None
    s = st['futures']
    if allow_park and flat(st) and not s['paused'] and not s.get('floor_hit') and \
            combined.park(st['flat_since'], t, history):
        _open_hedge(st, price, t)
        return 'parked'
    return None


def enter(st, plan, price, t, leader=None):
    """Close the idle hedge (at `price`) and place the entry, which fills at the next bar."""
    if st['hedge']:
        _close_hedge(st, price, t, 'TRADE_SIGNAL')
    futures.step(st['futures'], dict(kind='action', time=pd.Timestamp(t).isoformat(), action='enter', plan=plan), inplace=True)
    st['leader'] = leader or plan.get('playbook')


def exit_(st, t):
    futures.step(st['futures'], dict(kind='action', time=pd.Timestamp(t).isoformat(), action='exit'), inplace=True)
