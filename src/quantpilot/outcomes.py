"""Outcome of one trade plan on 5m bars, the same fill rules as `futures-v1` (design spec, section 3.6).

A decision at bar i's close fills at bar i+1's open with slippage against the trader. Inside each bar the
stop counts before the target, a gap through the stop fills at the open, and every exit pays slippage.
Optional management: the stop moves to break-even (entry plus round-trip costs) once the trade has gone
`breakeven_r` R in its favour; an abort mask exits at the next open; `max_bars` exits at a close.
R is the entry-to-stop price distance, so a stop-out after costs is slightly worse than -1R.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd

from .futures import FEE, SLIPPAGE

POST_BARS = 48   # 4 hours after the exit, for stop-hunt diagnosis


class Bars(NamedTuple):
    t: pd.DatetimeIndex   # bar close times (availability)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


def from_candles(candles):
    c = candles.sort_values('available_at')
    return Bars(t=pd.DatetimeIndex(pd.to_datetime(c.available_at, utc=True)),
                **{k[0]: c[k].to_numpy(float) for k in ('open', 'high', 'low', 'close')})


def simulate(bars, i, side, stop, target, max_bars, breakeven_r=None, abort=None, funding=None, slippage=SLIPPAGE,
             fee=FEE, post_bars=POST_BARS):
    """The trade decided at bar `i`'s close; None if there is no next bar to fill on."""
    n = len(bars.c)
    if i+1 >= n or stop is None or target is None:
        return None
    s = 1 if side == 'long' else -1
    entry = bars.o[i+1]*(1+s*slippage)
    risk = abs(entry-stop)
    if risk <= 0 or s*(target-entry) <= 0 or s*(entry-stop) <= 0:
        return dict(valid=False, reason='INVALID_AT_FILL', entry=entry, entry_index=i+1)
    current, moved = stop, False
    best = worst = entry
    exit_price, reason, j, at_open = None, None, None, False
    last = min(n-1, i+max_bars)
    for j in range(i+1, last+1):
        o, h, l, c = bars.o[j], bars.h[j], bars.l[j], bars.c[j]
        if j > i+1 and abort is not None and abort[j-1]:
            exit_price, reason, at_open = o, 'ABORT', True
            break
        stop_kind = 'BREAKEVEN_STOP' if moved else 'STOP'
        if s*(current-o) >= 0 and j > i+1:
            best, worst = (max(best, h), min(worst, l)) if s == 1 else (min(best, l), max(worst, h))
            exit_price, reason, at_open = o, stop_kind, True
            break
        best, worst = (max(best, h), min(worst, l)) if s == 1 else (min(best, l), max(worst, h))
        if (l <= current) if s == 1 else (h >= current):
            exit_price, reason = current, stop_kind
            break
        if (h >= target) if s == 1 else (l <= target):
            exit_price, reason = target, 'TARGET'
            break
        if breakeven_r is not None and not moved and s*(best-entry) >= breakeven_r*risk:
            level = entry*(1+s*(2*fee+2*slippage))
            if s*(level-current) > 0:
                current, moved = level, True
        if j == last:
            exit_price, reason = c, 'TIME' if j == i+max_bars else 'END_OF_DATA'
    fill = exit_price*(1-s*slippage)
    entry_time = bars.t[i]
    exit_time = bars.t[j-1] if at_open else bars.t[j]   # an open fill happens at the previous bar's close time
    paid = 0.0
    if funding is not None and len(funding[0]):
        times, rates = funding
        crossed = (times > entry_time) & (times <= exit_time)
        paid = float(-s*np.sum(rates[crossed]))
    gross = s*(fill-entry)/entry
    net = gross-2*fee+paid
    post = slice(j+1, min(n, j+1+post_bars))
    post_high = float(np.max(bars.h[post])) if post.start < post.stop else np.nan
    post_low = float(np.min(bars.l[post])) if post.start < post.stop else np.nan
    target_after = bool(post.start < post.stop and ((post_high >= target) if s == 1 else (post_low <= target)))
    return dict(valid=True, side=side, entry_index=i+1, entry_time=entry_time, entry=entry, stop=stop, target=target,
                exit_index=j, exit_time=exit_time, exit=fill, reason=reason, gross=gross, fees=2*fee, funding=paid,
                net=net, r=net/(risk/entry), risk_fraction=risk/entry, reward_risk=s*(target-entry)/risk,
                mfe_r=s*(best-entry)/risk, mae_r=s*(worst-entry)/risk, bars_held=j-i, breakeven_moved=moved,
                post_high=post_high, post_low=post_low, target_after_exit=target_after)
