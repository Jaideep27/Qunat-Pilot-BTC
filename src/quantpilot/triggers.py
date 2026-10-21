"""Triggers: the moments the rule engine looks at the market at all (design spec, section 3.2).

Anomaly triggers (`at: 5m`) fire once per episode, at the first 5m close where their condition holds.
Technical triggers (`at: 15m`) fire at every completed 15m close where theirs holds. Macro releases and
classified news come from their own records. A trigger opens a watch window; `episode` measures what has
happened since it began (extreme price, open-interest change) using rows known at the decision time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import rules

COLUMNS = ['time', 'type', 'direction', 'strength', 'start', 'support', 'resistance', 'atr', 'level',
           'open_interest', 'range_high', 'range_low', 'range_mid', 'confidence', 'ref']
LEVEL = {'LEVEL_SWEEP_LOW': 'support', 'LEVEL_BREAK_DOWN': 'support', 'LEVEL_HOLD_SUPPORT': 'support',
         'DUMP': 'support', 'LIQ_BURST_LONG': 'support', 'LEVEL_SWEEP_HIGH': 'resistance',
         'LEVEL_BREAK_UP': 'resistance', 'LEVEL_REJECT_RESISTANCE': 'resistance', 'SPIKE': 'resistance',
         'LIQ_BURST_SHORT': 'resistance'}
WINDOW = pd.Timedelta(minutes=15)


def _holds(pf, condition):
    """Vectorised `rules.check` over a frame: True where the condition holds (missing inputs -> False)."""
    if isinstance(condition, dict):
        (kind, items), = condition.items()
        parts = [_holds(pf, item) for item in items]
        out = parts[0].copy()
        for part in parts[1:]:
            out = (out | part) if kind == 'any' else (out & part)
        return out
    left, op, right = condition
    side = lambda ref: pf[ref].astype(float) if isinstance(ref, str) and ref in pf else (
        pd.Series(np.nan, index=pf.index) if isinstance(ref, str) else pd.Series(float(ref), index=pf.index))
    a, b = side(left), side(right)
    if op not in rules.OPS:
        raise ValueError(f'unknown operator {op}')
    return pd.Series(rules.OPS[op](a.to_numpy(), b.to_numpy()), index=pf.index) & a.notna() & b.notna()


def _rows(pf, times, kind, spec, strength):
    rows = pf.loc[times]
    level_col = LEVEL.get(kind)
    return pd.DataFrame(dict(
        time=times, type=kind, direction=spec['direction'], strength=strength,
        start=times-WINDOW, support=rows.get('support', pd.Series(np.nan, index=times)).to_numpy(float),
        resistance=rows.get('resistance', pd.Series(np.nan, index=times)).to_numpy(float),
        atr=rows.get('atr_1h', pd.Series(np.nan, index=times)).to_numpy(float),
        level=rows[level_col].to_numpy(float) if level_col and level_col in rows else np.nan,
        open_interest=np.nan, range_high=np.nan, range_low=np.nan, range_mid=np.nan, confidence=np.nan, ref=None))


def detect(pf, book, macro_events=None, news_events=None):
    """Every trigger in the pattern frame `pf`, oldest first."""
    found = []
    at15 = pf.index == pf.index.floor('15min')
    for kind, spec in book['triggers'].items():
        if 'when' not in spec:
            continue
        holds = _holds(pf, spec['when'])
        if spec['at'] == '5m':
            fire = holds & ~holds.shift(1, fill_value=False)
        else:
            fire = holds & at15
        times = pf.index[fire.to_numpy()]
        if len(times):
            strength = pf['move_z_15m'].reindex(times).to_numpy(float) if 'move_z_15m' in pf else np.nan
            found.append(_rows(pf, times, kind, spec, strength))
    for event in macro_events or []:
        if not event.get('scheduled_at'):
            continue
        release = pd.Timestamp(event['scheduled_at'])
        t = (release+WINDOW).ceil('15min')
        if t not in pf.index:
            continue
        row = pf.loc[t]
        hi, lo = float(row.get('high_15m', np.nan)), float(row.get('low_15m', np.nan))
        frame = _rows(pf, pd.DatetimeIndex([t]), 'MACRO_RELEASE', book['triggers']['MACRO_RELEASE'], np.nan)
        frame[['range_high', 'range_low', 'range_mid']] = [hi, lo, (hi+lo)/2]
        frame['ref'] = event.get('id') or f"{event.get('kind')}:{release.isoformat()}"
        frame['start'] = release
        found.append(frame)
    for item in news_events or []:
        direction = {'bullish': 1, 'bearish': -1}.get(item.get('direction'))
        if item.get('impact') != 'high' or direction is None or not item.get('available_at'):
            continue
        t = pd.Timestamp(item['available_at']).ceil('5min')
        if t not in pf.index:
            continue
        kind = 'NEWS_SHOCK_UP' if direction == 1 else 'NEWS_SHOCK_DOWN'
        frame = _rows(pf, pd.DatetimeIndex([t]), kind, book['triggers'][kind], np.nan)
        frame['confidence'], frame['ref'] = float(item.get('confidence') or 0), item.get('id')
        found.append(frame)
    if not found:
        return pd.DataFrame(columns=COLUMNS)
    table = pd.concat(found, ignore_index=True).sort_values(['time', 'type']).reset_index(drop=True)
    if 'open_interest' in pf:
        oi = pf['open_interest'].astype(float)
        table['open_interest'] = oi.reindex(pd.DatetimeIndex(table.start), method='ffill').to_numpy()
    return table


class TriggerIndex:
    """Fast lookup of the most recent trigger of given types inside a playbook's watch window."""

    def __init__(self, table):
        self.table = table.reset_index(drop=True)
        self.by_type = {kind: (pd.DatetimeIndex(group.time).asi8, group.index.to_numpy())
                        for kind, group in self.table.groupby('type')}

    def active(self, t, spec):
        t = pd.Timestamp(t)
        best = None
        for kind in spec['any']:
            if kind not in self.by_type:
                continue
            times, rows = self.by_type[kind]
            k = np.searchsorted(times, t.value, side='right')-1
            while k >= 0:
                age = int((t.value-times[k])//WINDOW.value)
                if age > spec['within_bars']:
                    break
                if age >= spec['min_bars']:
                    if best is None or times[k] > best[0]:
                        best = (times[k], rows[k])
                    break
                k -= 1
        return None if best is None else self.table.loc[best[1]]


def episode(trigger, t, pf):
    """`trigger.*` features at decision time `t`: extremes and OI change since the move began."""
    t = pd.Timestamp(t)
    lo, hi = pf.index.searchsorted(trigger.start, side='right'), pf.index.searchsorted(t, side='right')
    low = float(pf['low'].to_numpy()[lo:hi].min()) if hi > lo else np.nan
    high = float(pf['high'].to_numpy()[lo:hi].max()) if hi > lo else np.nan
    oi_now = float(pf['open_interest'].to_numpy()[hi-1]) if 'open_interest' in pf and hi else np.nan
    oi_start = float(trigger.open_interest) if pd.notna(trigger.open_interest) else np.nan
    atr = float(trigger.atr) if pd.notna(trigger.atr) and trigger.atr > 0 else np.nan
    support, resistance = float(trigger.support), float(trigger.resistance)
    return {'trigger.type': trigger.type, 'trigger.direction': float(trigger.direction), 'trigger.low': low,
            'trigger.high': high, 'trigger.move': float(trigger.strength) if pd.notna(trigger.strength) else np.nan,
            'trigger.oi_change': oi_now/oi_start-1 if oi_start and oi_start == oi_start else np.nan,
            'trigger.low_to_support_atr': (low-support)/atr, 'trigger.high_to_resistance_atr': (high-resistance)/atr,
            'trigger.level': float(trigger.level), 'trigger.range_high': float(trigger.range_high),
            'trigger.range_low': float(trigger.range_low), 'trigger.range_mid': float(trigger.range_mid),
            'trigger.confidence': float(trigger.confidence), 'trigger.age_bars': int((t-trigger.time)//WINDOW),
            'trigger.time': trigger.time, 'trigger.ref': trigger.ref}
