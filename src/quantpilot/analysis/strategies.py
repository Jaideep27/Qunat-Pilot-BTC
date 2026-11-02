"""What every strategy says right now: a live checklist, a plain verdict and its history-test record.

Read-only: it reuses the strategies' own rules (`strategies/`, `rules/`) on completed bars and never places,
sizes or records a trade. Paper trading itself is switched off (A15).
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from quantpilot.config import settings
from quantpilot.features import patterns
from quantpilot.rules import rulebook as rules
from quantpilot.rules import triggers
from quantpilot.strategies import breakout, carry, combined, range_trader, trend

LABELS = {
    'volume_z_15m': 'Volume vs normal (15m, SD)', 'taker_share_15m': 'Aggressive buyers’ share (15m)',
    'range_pos_4h': 'Position in the 4h likely range', 'up_15m': 'Last 15m candle is green',
    'lower_wick_15m': 'Lower wick share of the 15m candle', 'upper_wick_15m': 'Upper wick share of the 15m candle',
    'body_15m': 'Body share of the 15m candle', 'sweep_low': 'Swept a support', 'sweep_high': 'Swept a resistance',
    'hold_support': 'Held support', 'reject_resistance': 'Rejected resistance', 'retest_from_below': 'Retest from below',
    'retest_from_above': 'Retest from above', 'room_up_atr': 'Room up to the 4h band top (ATR)',
    'room_down_atr': 'Room down to the 4h band bottom (ATR)', 'funding_z': 'Funding vs normal (SD)',
    'close': 'Price', 'vwap': 'VWAP', 'vwap_z': 'Price vs VWAP (SD)', 'oi_change_15m': 'Open interest change (15m)',
    'oi_change_1h': 'Open interest change (1h)', 'ema_stack_15m': 'EMA stack (15m)', 'rsi_15m': 'RSI (15m)',
    'adx_1h': 'ADX (1h)', 'close_15m': '15m close', 'trigger.oi_change': 'Open interest change during the trigger',
    'trigger.low_to_support_atr': 'Trigger low vs support (ATR)', 'trigger.high_to_resistance_atr': 'Trigger high vs resistance (ATR)',
    'trigger.range_high': 'First post-release candle high', 'trigger.range_low': 'First post-release candle low',
    'trigger.confidence': 'News confidence', 'trigger.low': 'Trigger low', 'trigger.high': 'Trigger high'}
OPS = {'>=': '≥', '<=': '≤', '>': '>', '<': '<', '==': '='}
TRIGGER_NAMES = {
    'DUMP': 'a sharp drop', 'SPIKE': 'a sharp jump', 'OI_SHOCK_DOWN': 'open interest falling fast',
    'OI_SHOCK_UP': 'open interest rising fast', 'LIQ_BURST_LONG': 'a burst of long liquidations',
    'LIQ_BURST_SHORT': 'a burst of short liquidations', 'FUNDING_EXTREME_HIGH': 'extreme positive funding',
    'FUNDING_EXTREME_LOW': 'extreme negative funding', 'LEVEL_BREAK_DOWN': 'a support break',
    'LEVEL_BREAK_UP': 'a resistance break', 'LEVEL_SWEEP_LOW': 'a sweep below support',
    'LEVEL_SWEEP_HIGH': 'a sweep above resistance', 'LEVEL_HOLD_SUPPORT': 'support holding',
    'LEVEL_REJECT_RESISTANCE': 'resistance rejecting', 'SQUEEZE_RELEASE': 'a squeeze releasing',
    'TREND_PULLBACK_UP': 'a pullback in an uptrend', 'TREND_PULLBACK_DOWN': 'a pullback in a downtrend',
    'RANGE_EDGE_UPPER': 'price at the top of a quiet range', 'RANGE_EDGE_LOWER': 'price at the bottom of a quiet range',
    'MACRO_RELEASE': 'a CPI, jobs or FOMC release', 'NEWS_SHOCK_UP': 'high-impact bullish news',
    'NEWS_SHOCK_DOWN': 'high-impact bearish news'}

STYLES = {
    'T1': dict(name='Trend Rider', what='Follows the long-term daily trend. It only buys when Bitcoin is up over 20, 60 '
               'and 120 days and above its 50-day average (sells short in the mirror case), and holds for days to weeks.'),
    'R1': dict(name='Range Trader', what='In sideways markets, buys a dip below the 4-hour likely range once price closes '
               'back inside it (sells the mirror at the top) and aims for the middle of the range.'),
    'B1': dict(name='Breakout Hunter', what='After a quiet spell, waits for a 15-minute close beyond the last 20 candles’ '
               'high or low on at least double the usual volume, then waits for a retest instead of chasing.'),
    'C1': dict(name='Funding Collector', what='Does not bet on direction: holds Bitcoin plus an equal short future and '
               'collects the funding that leveraged buyers pay sellers every 8 hours, while that funding is positive.'),
    'X2': dict(name='Master Bot (trend-led)', what='The last trading bot: the daily trend (Trend Rider) decides the '
               'direction, and Range Trader, Breakout Hunter, a rulebook setup or price at support/resistance decides the moment.'),
}
VERDICTS = {'T1': 'Positive in testing, not proven', 'C1': 'Positive in testing, not proven', 'R1': 'Failed in testing',
            'B1': 'Failed in testing', 'X2': 'Failed the pass rule (rules chosen after seeing results)'}
RUN_KEYS = {'T1': 'T1', 'R1': 'R1', 'B1': 'B1', 'C1': 'C1', 'X2': 'X2-nopark'}


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pct(x, digits=1):
    return '—' if x is None else f'{x*100:+.{digits}f}%'


def _money(x):
    return '—' if x is None else f'${x:,.0f}'


def item(label, ok, value):
    return dict(label=label, ok=None if ok is None else bool(ok), value=value)


# ---- history (static, from the 2026-11-01 style research run) -------------------------------------------------------

@lru_cache(maxsize=8)
def style_history(code):
    runs = sorted((Path(settings.artifact_dir)/'style-research').glob(f'run-*/runs/{RUN_KEYS[code]}.json'))
    if not runs:
        return None
    data = json.loads(runs[-1].read_text(encoding='utf-8'))
    acct, report = data['account'], data['verdict']['report']
    trades = [t for t in data.get('trades', []) if isinstance(t, dict) and t.get('exit_time')]
    balance, curve = 10_000.0, []
    for t in trades:
        balance += float(t.get('net') or 0)
        curve.append((t['exit_time'][:10], round(balance, 2)))
    step = max(1, len(curve)//120)
    curve = curve[::step]+([curve[-1]] if curve and (len(curve)-1) % step else [])
    return dict(verdict=VERDICTS[code], passed=bool(data['verdict']['passed']), trades=int(acct['trades']),
                win_rate=_num(acct.get('win_rate')), break_even=_num(report.get('break_even')),
                account_return=_num(acct.get('account_return')), max_drawdown=_num(acct.get('max_drawdown')),
                buy_and_hold=_num(acct.get('buy_and_hold')), profit_factor=_num(report.get('profit_factor')),
                start=trades[0]['entry_time'][:10] if trades and trades[0].get('entry_time') else None,
                end=trades[-1]['exit_time'][:10] if trades else None,
                curve=[dict(date=d, equity=e) for d, e in curve])


# ---- styles --------------------------------------------------------------------------------------------------------

def trend_rider(daily):
    f = trend.frame(daily)
    if len(f) < 130:
        return dict(status='off', tone='neutral', says='Not enough daily history yet.', checklist=[])
    last, c = f.iloc[-1], f.close
    vote = int(last.vote)
    checks = [item('20-day return', last.r20 > 0 if vote >= 0 else last.r20 < 0, _pct(_num(last.r20))),
              item('60-day return', last.r60 > 0 if vote >= 0 else last.r60 < 0, _pct(_num(last.r60))),
              item('120-day return', last.r120 > 0 if vote >= 0 else last.r120 < 0, _pct(_num(last.r120))),
              item('Price vs 50-day average', last.close > last.sma50 if vote >= 0 else last.close < last.sma50,
                   _pct(_num(last.close/last.sma50-1)))]
    need_up = float(max(c.iloc[-20], c.iloc[-60], c.iloc[-120], c.iloc[-49:].sum()/49))
    need_down = float(min(c.iloc[-20], c.iloc[-60], c.iloc[-120], c.iloc[-49:].sum()/49))
    says = {1: 'Every trend measure points up: it would only buy.', -1: 'Every trend measure points down: it would only sell short.',
            0: f'The trend measures disagree, so it stands aside. A daily close above about {_money(need_up)} would turn '
               f'it up; below about {_money(need_down)} would turn it down.'}[vote]
    return dict(status={1: 'bullish', -1: 'bearish', 0: 'standing aside'}[vote], tone={1: 'up', -1: 'down', 0: 'neutral'}[vote],
                says=says, checklist=checks, as_of=f.index[-1].isoformat(), vote=vote,
                levels=dict(turns_up_above=need_up, turns_down_below=need_down,
                            stop_distance=_num(3*last.atr20)))


def _plain(reason):
    """'On: the market ...' -> 'The market ...' (the analysis app shows status separately)."""
    text = re.sub(r'^(On|Off):\s*', '', reason or '')
    return text[:1].upper()+text[1:]


def _signal_now(module, sf):
    sig = module.signals(sf)
    if len(sig) and sig.index[-1] == sf.index[-1]:
        return sig.iloc[-1]
    return None


def range_rider(sf):
    t = sf.index[-1]
    on, reason = range_trader.active(sf, t)
    row = sf.iloc[-1]
    sig = _signal_now(range_trader, sf) if on else None
    checks = [item('Market type is RANGE (sideways)', row.regime == 'RANGE', str(row.regime).replace('_', ' ').title()),
              item('Dipped below the range and closed back inside (buy)', bool(row.low < row.band4_low < row.close),
                   f'range bottom {_money(_num(row.band4_low))}'),
              item('Rose above the range and closed back inside (sell)', bool(row.close < row.band4_high < row.high),
                   f'range top {_money(_num(row.band4_high))}')]
    if sig is not None:
        side = sig['side']
        return dict(status='bullish' if side == 'long' else 'bearish', tone='up' if side == 'long' else 'down', checklist=checks,
                    says=f"Signal now: {sig['reason'].lower()}; it would aim for the middle of the range ({_money(_num(row.band4_mid))}).")
    return dict(status='watching' if on else 'off', tone='neutral', says=_plain(reason), checklist=checks)


def breakout_hunter(sf):
    t = sf.index[-1]
    on, reason = breakout.active(sf, t)
    row = sf.iloc[-1]
    prior_high = _num(sf.high.shift(1).rolling(breakout.LOOKBACK).max().iloc[-1])
    prior_low = _num(sf.low.shift(1).rolling(breakout.LOOKBACK).min().iloc[-1])
    vol = _num(sf.volume.shift(1).rolling(breakout.LOOKBACK).mean().iloc[-1])
    quiet = _num(sf.atr_pct_1h.iloc[-breakout.SQUEEZE_BARS:].min())
    checks = [item('Quiet spell in the last 4 hours (1h volatility in its lowest 20%)', quiet is not None and quiet <= breakout.SQUEEZE_PCT,
                   '—' if quiet is None else f'lowest {quiet*100:.0f}%'),
              item('15m close beyond the last 20 candles', prior_high is not None and (row.close > prior_high or row.close < prior_low),
                   f'{_money(prior_low)} – {_money(prior_high)}'),
              item('Volume at least 2× usual', vol is not None and row.volume >= 2*vol, '—' if not vol else f'{row.volume/vol:.1f}×')]
    sig = _signal_now(breakout, sf)
    if sig is not None:
        side = sig['side']
        return dict(status='bullish' if side == 'long' else 'bearish', tone='up' if side == 'long' else 'down', checklist=checks,
                    says=f"Breakout signal: {sig['reason'].lower()} near {_money(_num(sig.get('limit')))}.")
    return dict(status='watching' if on else 'off', tone='neutral', says=_plain(reason), checklist=checks)


def funding_collector(funding):
    rates = [] if funding is None or not len(funding) else \
        funding.sort_values('event_time').funding_rate.astype(float).tolist()
    on, reason = carry.active(rates, False)
    week = sum(rates[-carry.OPEN_WINDOW:])/carry.OPEN_WINDOW if len(rates) >= carry.OPEN_WINDOW else None
    days3 = sum(rates[-carry.CLOSE_WINDOW:])/carry.CLOSE_WINDOW if len(rates) >= carry.CLOSE_WINDOW else None
    year = lambda r: None if r is None else r*3*365
    checks = [item('7-day average funding above zero', week is not None and week > 0, _pct(year(week))+' a year'),
              item('3-day average funding above zero', days3 is not None and days3 > 0, _pct(year(days3))+' a year')]
    if week is None:
        says = reason
    elif on:
        says = f'Funding is positive (about {year(week):+.1%} a year), so the hedge would be earning it now.'
    else:
        says = f'Funding is negative (about {year(week):+.1%} a year), so holding the hedge would cost money now.'
    return dict(status='collecting' if on else 'off', tone='up' if on else 'neutral', says=says, checklist=checks)


def master(trend_view, range_view, breakout_view, hits, sf):
    vote = trend_view.get('vote', 0)
    if not vote:
        return dict(status='standing aside', tone='neutral', checklist=[],
                    says='No clear daily trend, so the master would stay out whatever the other strategies say.')
    side = 'long' if vote == 1 else 'short'
    timers = [name for name, view in (('Range Trader', range_view), ('Breakout Hunter', breakout_view))
              if view['status'] == ('bullish' if vote == 1 else 'bearish')]
    timers += [f"rulebook setup {h['id']}" for h in hits if h['side'] == side]
    level = combined.at_level(side, sf.iloc[-1].to_dict())
    timers += [f"price at the {l.split('_')[1]} {l.split('_')[0]}" for l in level]
    word = 'buy' if vote == 1 else 'sell short'
    if timers:
        return dict(status='bullish' if vote == 1 else 'bearish', tone='up' if vote == 1 else 'down', checklist=[],
                    says=f"The trend allows it to {word}, and {', '.join(timers)} times the entry now.")
    return dict(status='watching', tone='neutral', checklist=[],
                says=f'The trend allows it to {word}; it waits for another strategy or '
                     f"{'support' if vote == 1 else 'resistance'} to time the entry.")


# ---- the rulebook --------------------------------------------------------------------------------------------------

def plain_condition(rec):
    left, op, right = rec['condition'].split(' ', 2)
    name = LABELS.get(left, left.replace('_', ' '))
    target = LABELS.get(right, right) if not re.fullmatch(r'-?[\d.]+', right) else right
    shown = '—' if rec['left'] is None else (f"{rec['left']:,.4g}" if abs(rec['left']) < 1000 else _money(rec['left']))
    return item(f'{name} {OPS.get(op, op)} {target}', rec['ok'], 'no data' if rec['missing'] else shown)


@lru_cache(maxsize=1)
def _setup_history():
    from quantpilot.research.setup_history import lookup
    book = rules.cached()
    return {pb['id']: lookup(pb['id']) for pb in book['playbooks']}


def rulebook_views(pf, macro_events=None, news_events=None):
    """Each of the 20 setup versions at the latest 15-minute close, grouped into the 13 playbooks."""
    book = rules.cached()
    closes = pf.index[pf.index == pf.index.floor('15min')]
    if not len(closes):
        return dict(time=None, playbooks=[], firing=[])
    t = closes[-1]
    table = triggers.detect(pf, book, macro_events, news_events)
    index = triggers.TriggerIndex(table) if len(table) else None
    row = pf.loc[t].to_dict()
    history = _setup_history()
    groups, firing = {}, []
    for pb in book['playbooks']:
        trig = index.active(t, pb['trigger']) if index is not None else None
        features = row | (triggers.episode(trig, t, pf) if trig is not None else {})
        regime = patterns.regime(features)
        ev = rules.evaluate(pb, features, regime)
        shadow = bool(pb.get('shadow_only')) or pb['id'].startswith('PB13')
        if trig is not None and ev['ok']:
            status, tone = 'firing', 'up' if pb['side'] == 'long' else 'down'
            firing.append(dict(id=pb['id'], side=pb['side'], name=pb['name']))
        elif trig is not None:
            status, tone = 'triggered, checklist not met', 'neutral'
        else:
            status, tone = 'waiting for its trigger', 'neutral'
        wakes = ' or '.join(TRIGGER_NAMES.get(k, k) for k in pb['trigger']['any'])
        checks = [item(f"Market type is {' / '.join(r.replace('_', ' ').title() for r in pb['regimes'])}",
                       ev['regime_ok'], regime.replace('_', ' ').title())]
        checks += [plain_condition(c) for c in ev['checks']]
        h = history.get(pb['id'])
        version = dict(id=pb['id'], side=pb['side'], status=status, tone=tone, shadow=shadow,
                       trigger=dict(wakes=wakes, seen=None if trig is None else pd.Timestamp(trig.time).isoformat()),
                       checklist=checks, passed=sum(c['ok'] for c in ev['checks']), total=len(ev['checks']),
                       stop_target=f"stop {pb['stop'].get('buffer_atr', 0)} ATR beyond {pb['stop']['anchor'].replace('trigger.', 'the trigger ').replace('_', ' ')}; "
                                   f"time limit {pb.get('time_stop_hours')} h",
                       history=None if h is None else dict(setups=h.get('setups'), win_rate=(h.get('plan') or {}).get('win_rate'),
                                                         mean_r=(h.get('plan') or {}).get('mean_r'),
                                                         went_their_way_4h=(h.get('went_their_way_4h') or {}).get('share')))
        base = re.sub(r'[LS]$', '', pb['id'])
        g = groups.setdefault(base, dict(id=base, name=re.sub(r'\s*\((long|short)\)$', '', pb['name']), family=pb.get('family'),
                                         what=pb.get('thesis'), versions=[]))
        g['versions'].append(version)
    return dict(time=t.isoformat(), regime=patterns.regime(row), playbooks=list(groups.values()), firing=firing,
                verdict='Your rulebook v2 lost 10.1% over 2021–2025 (180 trades, won 32% where 45% was needed). '
                        'It is shown here as analysis, not as a trading signal.')


def all_views(sf, daily, funding, pf, macro_events=None, news_events=None):
    """Every strategy's live view plus its history, in display order."""
    views = {}
    t_view = trend_rider(daily) if daily is not None and len(daily) else dict(status='off', tone='neutral', says='No daily data.', checklist=[])
    r_view, b_view = range_rider(sf), breakout_hunter(sf)
    rb = rulebook_views(pf, macro_events, news_events)
    views['T1'], views['R1'], views['B1'] = t_view, r_view, b_view
    views['C1'] = funding_collector(funding)
    views['X2'] = master(t_view, r_view, b_view, [f for f in rb['firing'] if not f['id'].startswith('PB13')], sf)
    out = []
    for code, meta in STYLES.items():
        v = views[code]
        out.append(dict(id=code, kind='style', name=meta['name'], what=meta['what'], history=style_history(code), **v))
    return dict(styles=out, rulebook=rb)
