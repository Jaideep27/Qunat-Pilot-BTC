"""Report card for a paper account: win rate, profit factor, average win and loss, worst drop and how long it
lasted, losing streaks, and the comparison with simply holding BTC over the same time.

The equity curve is the closed-trade curve (one point per exit). No Sharpe ratio: with a few dozen trades it
mostly measures noise, and the owner asked for plain measures.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import select

from quantpilot.storage.market import PERP, MarketCandle


def _drawdowns(trades, nets, balance):
    """Worst drop from a peak, and the longest time below a peak (to recovery, or to the last exit)."""
    equity = peak = balance
    peak_time = pd.Timestamp(trades[0]['entry_time']) if trades else None
    worst, longest, below = 0.0, 0.0, False
    for t, net in zip(trades, nets):
        equity += net
        when = pd.Timestamp(t['exit_time'])
        if below or equity < peak:
            longest = max(longest, (when-peak_time)/pd.Timedelta(hours=1))
        if equity >= peak:
            peak, peak_time, below = equity, when, False
        else:
            worst, below = min(worst, equity/peak-1), True
    return worst, longest


def report(trades, balance, equity_now, hold=None):
    nets = [float(t['net']) for t in trades]
    wins, losses = [n for n in nets if n > 0], [n for n in nets if n <= 0]
    lost = -sum(losses)
    card = dict(trades=len(nets), wins=len(wins), losses=len(losses), win_rate=len(wins)/len(nets) if nets else None,
                total_net=float(sum(nets)), account_return=equity_now/balance-1, expectancy=float(np.mean(nets)) if nets else None,
                avg_win=float(np.mean(wins)) if wins else None, avg_loss=float(np.mean(losses)) if losses else None,
                profit_factor=sum(wins)/lost if lost > 0 else None)
    card['payoff'] = card['avg_win']/-card['avg_loss'] if card['avg_win'] and card['avg_loss'] else None
    card['max_drawdown'], card['longest_drawdown_hours'] = _drawdowns(trades, nets, balance)
    streak = longest = 0
    for n in nets:
        streak = streak+1 if n <= 0 else 0
        longest = max(longest, streak)
    card['longest_losing_streak'] = longest
    if hold:
        card['hold_return'] = hold['end_price']/hold['start_price']-1
        card['vs_hold'] = card['account_return']-card['hold_return']
    return card


def hold(engine, start, end=None):
    """Buying BTC at the first 5m close at or after `start` and holding to `end` (or the latest close)."""
    start = pd.Timestamp(start)
    base = select(MarketCandle.event_time, MarketCandle.close).where(
        MarketCandle.market == PERP, MarketCandle.interval == '5m', MarketCandle.complete.is_(True))
    with engine.connect() as connection:
        first = connection.execute(base.where(MarketCandle.event_time >= start.to_pydatetime())
                                   .order_by(MarketCandle.event_time).limit(1)).first()
        last_query = base if end is None else base.where(MarketCandle.event_time <= pd.Timestamp(end).to_pydatetime())
        last = connection.execute(last_query.order_by(MarketCandle.event_time.desc()).limit(1)).first()
    if first is None or last is None or last.event_time < first.event_time:
        return None
    return dict(start=pd.Timestamp(first.event_time).isoformat(), end=pd.Timestamp(last.event_time).isoformat(),
                start_price=float(first.close), end_price=float(last.close), **{'return': float(last.close)/float(first.close)-1})
