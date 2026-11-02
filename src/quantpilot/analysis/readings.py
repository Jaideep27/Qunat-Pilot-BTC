"""Current indicator readings: value, zone, plain sentence and a sparkline, on completed bars only.

Timeframe indicators come from `features.core.technical` on completed 15m/1h/4h/1d bars (the same maths as the
chart studies). Market indicators come from the last row of `features.patterns.pattern_frame` (the rule engine's
causal frame). Nothing here looks past the latest completed bar.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quantpilot.analysis import catalogue
from quantpilot.features import patterns
from quantpilot.features.core import div, technical

RULES = {'15m': ('15min', 3), '1h': ('1h', 12), '4h': ('4h', 48), '1d': ('1D', 288)}
PERCENTILE_BARS = {'15m': 2880, '1h': 720, '4h': 180, '1d': 365}     # about 30 days (a year for daily)
SPARK = 48


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def timeframe_frame(candles, tf):
    """Completed bars of `tf` (indexed by close) and their indicator frame."""
    rule, count = RULES[tf]
    bars = patterns.resample(patterns._candles(dict(candles=candles)), rule, count)
    f = technical(bars)
    stack, _ = patterns.ema_stack(bars)
    f['ema_stack'] = stack
    f['taker_share'] = div(bars.taker_buy_volume, bars.volume)
    n = PERCENTILE_BARS[tf]
    f['atr_percentile'] = 100*f.atr.rolling(n, min_periods=min(n, 60)).rank(pct=True)
    f['squeeze'] = patterns.squeeze(bars).squeeze_on
    return bars, f


def market_values(row):
    """The 'market' indicators from one pattern-frame row (a dict)."""
    get = lambda k: _num(row.get(k))
    funding = get('funding_rate')
    return dict(range_pos_4h=get('range_pos_4h'), vwap_z=get('vwap_z'),
                funding_annual=None if funding is None else funding*3*365, funding_z=get('funding_z'),
                oi_change_24h=get('oi_change_24h'), global_ratio=get('global_ratio'), fear_greed=get('fear_greed'),
                premium=get('premium'), liq_z_15m=get('liq_z_15m'))


def reading(indicator, value, spark=None, detail=None):
    z = catalogue.zone(indicator, value)
    out = dict(id=indicator['id'], value=value, zone=None, label=None, tone=None, sentence=None,
               spark=spark or [], detail=detail)
    if z is None:
        return out | dict(label='No data', sentence='No reading available right now.')
    index, label, tone, sentence = z
    return out | dict(zone=index, label=label, tone=tone, sentence=sentence)


def timeframe_readings(candles, tf):
    """Every 'timeframe' indicator on the last completed `tf` bar, plus that bar's time and close."""
    bars, f = timeframe_frame(candles, tf)
    if not len(f):
        return dict(time=None, close=None, items=[])
    last = f.iloc[-1]
    items = []
    for ind in catalogue.INDICATORS:
        if ind['scope'] != 'timeframe':
            continue
        series = f[ind['id']].astype(float)
        spark = [round(v, 6) if np.isfinite(v) else None for v in series.iloc[-SPARK:].to_numpy(float)]
        detail = None
        if ind['id'] == 'atr_percentile' and _num(last.atr) is not None:
            detail = dict(atr=_num(last.atr), atr_pct=_num(last.atr)/float(bars.close.iloc[-1]))
        items.append(reading(ind, _num(last[ind['id']]), spark, detail))
    return dict(time=f.index[-1].isoformat(), close=float(bars.close.iloc[-1]), items=items)


def market_readings(pf):
    """The 'market' indicators from the latest pattern-frame row, with 48-bar (5m) sparklines."""
    if pf is None or not len(pf):
        return dict(time=None, items=[])
    row = pf.iloc[-1].to_dict()
    values = market_values(row)
    tail = pf.iloc[-SPARK*3::3]       # 15-minute steps over the last 12 hours
    items = []
    for ind in catalogue.INDICATORS:
        if ind['scope'] != 'market':
            continue
        series = [market_values(r).get(ind['id']) for r in tail.to_dict('records')]
        items.append(reading(ind, values.get(ind['id']), [None if v is None else round(v, 6) for v in series]))
    return dict(time=pf.index[-1].isoformat(), items=items)


def group_summary(items):
    """Per group: how many readings lean up, down or neither, and one plain line."""
    out = {}
    for group in catalogue.GROUPS:
        tones = [i['tone'] for i in items if i.get('tone') and catalogue.BY_ID[i['id']]['group'] == group]
        if not tones:
            continue
        up = sum(t in catalogue.UP for t in tones)
        down = sum(t in catalogue.DOWN for t in tones)
        lean = 'up' if up > down and up >= len(tones)/2 else 'down' if down > up and down >= len(tones)/2 else 'mixed'
        out[group] = dict(up=up, down=down, total=len(tones), lean=lean,
                          line=f'{group}: {up} of {len(tones)} lean up, {down} lean down')
    return out
