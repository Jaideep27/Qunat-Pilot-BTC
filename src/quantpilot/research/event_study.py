"""Hourly 5%-move event study: what preceded large BTC moves and what followed them.

Every state column at a cutoff uses only observations available by that cutoff. Outcome
columns look forward and exist only to describe what happened next. Findings are
associations across a limited number of episodes, not causal attributions or a forecast.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select

from quantpilot.config import settings
from quantpilot.data.archives import AGES, VALUES, utc
from quantpilot.features.core import technical
from quantpilot.storage.database import database

THRESHOLD, COOLDOWN = .05, pd.Timedelta(hours=24)
HORIZONS = {'1h': 1, '4h': 4, '24h': 24}
OUTCOMES = ['fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_24h', 'max_drawdown_24h', 'max_rise_24h',
            'drop5_touch_24h', 'rally5_touch_24h']
STATE = ['ret_24h', 'rsi_14', 'ema_50_distance', 'adx', 'atr_fraction', 'realized_vol_24h', 'dist_7d_low',
         'dist_7d_high', 'volume_ratio_24h', 'taker_buy_share_24h', 'oi_change_24h', 'funding_rate',
         'global_ratio', 'global_ratio_change_24h', 'top_position_ratio', 'top_position_ratio_change_24h',
         'premium', 'fear_greed', 'hours_to_next_cpi', 'hours_to_next_jobs', 'hours_to_next_fomc']
PRECURSORS = ['ret_24h', 'rsi_14', 'realized_vol_24h', 'dist_7d_low', 'volume_ratio_24h', 'taker_buy_share_24h',
              'oi_change_24h', 'funding_rate', 'global_ratio', 'top_position_ratio', 'premium', 'fear_greed']
EXTERNAL = {'positioning': VALUES['positioning'], 'funding': ['funding_rate'], 'mark': ['mark_price'],
            'index': ['index_price'], 'sentiment': ['fear_greed']}


def load_market(engine=None, start=None, end=None):
    """Completed BTCUSDT perpetual candles and valid external observations from the durable store."""
    from quantpilot.storage.market import PERP, MarketCandle, MarketObservation
    engine = engine or database(settings.market_database_url)
    start, end = utc(start), utc(end)
    candle_columns = ['event_time', 'available_at', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_volume']
    candles, families = [], {family: [] for family in EXTERNAL}
    # Month-sized reads keep peak memory small: Python row objects never exist for the full history.
    bounds = list(pd.date_range(start.normalize().replace(day=1), end, freq='MS'))
    edges = [start-pd.Timedelta(days=2)]+[b for b in bounds if b > start]+[end]
    with engine.connect() as connection:
        for lo, hi in zip(edges[:-1], edges[1:]):
            rows = connection.execute(select(*[MarketCandle.__table__.c[c] for c in candle_columns]).where(
                MarketCandle.market == PERP, MarketCandle.interval == '5m', MarketCandle.complete.is_(True),
                MarketCandle.event_time >= max(lo, start).to_pydatetime(), MarketCandle.event_time < hi.to_pydatetime())
                .order_by(MarketCandle.event_time)).all()
            if rows:
                candles.append(_compact(pd.DataFrame(rows, columns=candle_columns)))
            for family, columns in EXTERNAL.items():
                rows = connection.execute(select(MarketObservation.event_time, MarketObservation.available_at,
                                                 MarketObservation.values).where(
                    MarketObservation.market == PERP, MarketObservation.valid.is_(True),
                    MarketObservation.family == family, MarketObservation.event_time >= lo.to_pydatetime(),
                    MarketObservation.event_time < hi.to_pydatetime())).all()
                if rows:
                    frame = pd.DataFrame({'event_time': [r[0] for r in rows], 'available_at': [r[1] for r in rows],
                                          **{c: [r[2].get(c) for r in rows] for c in columns}})
                    families[family].append(_compact(frame))
    frames = {'candles': pd.concat(candles, ignore_index=True) if candles else pd.DataFrame(columns=candle_columns)}
    for family, parts in families.items():
        if parts:
            frames[family] = pd.concat(parts, ignore_index=True).sort_values('available_at', ignore_index=True)
    return frames


def _compact(frame):
    frame = _times(frame)
    for column in frame.columns.difference(['event_time', 'available_at']):
        frame[column] = pd.to_numeric(frame[column], errors='coerce').astype('float32')
    return frame


def _times(frame):
    for column in ('event_time', 'available_at', 'received_at'):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def hourly_panel(frames, macro_events=None):
    """One row per completed hour, indexed by the instant it becomes known (the hour's close)."""
    candles = frames['candles'].sort_values('event_time').set_index('event_time')
    grouped = candles.resample('1h', closed='left', label='left')
    hours = grouped.agg(dict(open='first', high='max', low='min', close='last', volume='sum',
                             taker_buy_volume='sum'))
    hours = hours[grouped.close.count() == 12]  # incomplete hours never become observations
    hours = hours.reindex(pd.date_range(hours.index.min(), hours.index.max(), freq='h'))
    hours.index = hours.index+pd.Timedelta(hours=1)
    close, low, high, volume = hours.close, hours.low, hours.high, hours.volume
    panel = technical(hours)[['rsi_14', 'ema_50_distance', 'adx', 'atr_fraction']].copy()
    panel['close'] = close
    panel['high'], panel['low'], panel['volume'] = high, low, volume
    panel['ret_24h'] = close/close.shift(24)-1
    panel['realized_vol_24h'] = np.log(close).diff().rolling(24).std()*np.sqrt(24)
    panel['dist_7d_low'] = close/low.shift(1).rolling(168).min()-1
    panel['dist_7d_high'] = close/high.shift(1).rolling(168).max()-1
    panel['volume_ratio_24h'] = volume.rolling(24).sum()/(volume.shift(24).rolling(168).sum()/7)
    panel['taker_buy_share_24h'] = hours.taker_buy_volume.rolling(24).sum()/volume.rolling(24).sum()
    left = pd.DataFrame({'cutoff': panel.index})
    for family, columns in EXTERNAL.items():
        frame = frames.get(family)
        if frame is None or frame.empty:
            for column in columns:
                panel[column] = np.nan
            continue
        right = frame[['available_at', *columns]].dropna().sort_values('available_at')
        joined = pd.merge_asof(left, right, left_on='cutoff', right_on='available_at', direction='backward',
                               tolerance=pd.Timedelta(AGES[family]))
        for column in columns:
            panel[column] = joined[column].to_numpy()
    panel['oi_change_24h'] = panel.open_interest/panel.open_interest.shift(24)-1
    panel['global_ratio_change_24h'] = panel.global_ratio.diff(24)
    panel['top_position_ratio_change_24h'] = panel.top_position_ratio.diff(24)
    panel['premium'] = panel.mark_price/panel.index_price-1
    if macro_events is not None:
        from quantpilot.data.macro import macro_features
        panel = panel.join(macro_features(panel.index, macro_events))
    for column in STATE:
        if column not in panel:
            panel[column] = np.nan
    # Outcomes only: rows t+1..t+h after the cutoff.
    for label, h in HORIZONS.items():
        panel[f'fwd_ret_{label}'] = close.shift(-h)/close-1
    future_low = low.iloc[::-1].rolling(24, min_periods=24).min().iloc[::-1].shift(-1)
    future_high = high.iloc[::-1].rolling(24, min_periods=24).max().iloc[::-1].shift(-1)
    panel['max_drawdown_24h'] = future_low/close-1
    panel['max_rise_24h'] = future_high/close-1
    panel['drop5_touch_24h'] = (panel.max_drawdown_24h <= -THRESHOLD).astype(float).where(panel.max_drawdown_24h.notna())
    panel['rally5_touch_24h'] = (panel.max_rise_24h >= THRESHOLD).astype(float).where(panel.max_rise_24h.notna())
    return panel.replace([np.inf, -np.inf], np.nan)


def find_episodes(panel, threshold=THRESHOLD, cooldown=COOLDOWN):
    """First hour each 24h move crosses the threshold; repeats within the cooldown are one episode."""
    found, last = [], {'drop': None, 'rally': None}
    for t, value in panel.ret_24h.dropna().items():
        kind = 'drop' if value <= -threshold else 'rally' if value >= threshold else None
        if kind is None or (last[kind] is not None and t-last[kind] < cooldown):
            continue
        last[kind] = t
        found.append(dict(kind=kind, time=t, ret_24h=float(value)))
    return found


def match_controls(panel, episodes, per_episode=3, quiet=.02, distance=pd.Timedelta(hours=48)):
    """Ordinary hours at the same UTC hour, with a quiet 24h return, far from every episode."""
    eligible = panel.ret_24h.abs() < quiet
    if 'fwd_ret_24h' in panel:
        eligible &= panel.fwd_ret_24h.notna()
    candidates = panel.index[eligible.to_numpy()]
    if episodes:
        moments = np.sort(np.array([e['time'].value for e in episodes]))
        stamps = candidates.asi8
        position = np.clip(np.searchsorted(moments, stamps), 1, len(moments))
        nearest = np.minimum(np.abs(stamps-moments[position-1]), np.abs(moments[np.minimum(position, len(moments)-1)]-stamps))
        candidates = candidates[nearest >= distance.value]
    used, controls = set(), []
    for episode in episodes:
        same = [t for t in candidates[candidates.hour == episode['time'].hour] if t not in used]
        same.sort(key=lambda t: (abs(t-episode['time']), t))
        for t in same[:per_episode]:
            used.add(t)
            controls.append(dict(kind='control', time=t, matched_to=episode['time'], matched_kind=episode['kind']))
    return controls


def wilson(successes, n, z=1.96):
    if not n:
        return [None, None]
    p = successes/n
    centre, margin = p+z*z/(2*n), z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return [float((centre-margin)/(1+z*z/n)), float((centre+margin)/(1+z*z/n))]


def precursor_table(panel, column, bins=5, target='drop5_touch_24h'):
    """Descriptive frequency of a future large move by state bucket, over all eligible hours."""
    data = panel[[column, target]].dropna()
    if data.empty or data[column].nunique() < 2:
        return dict(column=column, target=target, base_rate=None, buckets=[])
    buckets = pd.qcut(data[column], bins, duplicates='drop')
    rows = []
    for interval, group in data.groupby(buckets, observed=True):
        hits = group[target] == 1
        rows.append(dict(low=float(interval.left), high=float(interval.right), hours=int(len(group)),
                         drop_rate=float(hits.mean()), ci95=wilson(int(hits.sum()), len(group)),
                         distinct_days=int(pd.Series(group.index[hits.to_numpy()].date).nunique())))
    return dict(column=column, target=target, base_rate=float((data[target] == 1).mean()), buckets=rows)


def bootstrap_difference(a, b, seed=0, draws=2000):
    """Difference in means (a - b) with a percentile bootstrap interval resampling each group."""
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if not len(a) or not len(b):
        return dict(difference=None, ci95=[None, None], n_a=len(a), n_b=len(b))
    rng = np.random.default_rng(seed)
    diffs = rng.choice(a, (draws, len(a))).mean(axis=1)-rng.choice(b, (draws, len(b))).mean(axis=1)
    return dict(difference=float(a.mean()-b.mean()), ci95=[float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
                n_a=int(len(a)), n_b=int(len(b)))


def _row(panel, t, columns):
    if t not in panel.index:
        return {c: None for c in columns}
    values = panel.loc[t, columns]
    return {c: (None if pd.isna(v) else float(v)) for c, v in values.items()}


def _timeline(t, macro_events, news_items):
    lo, hi = t-pd.Timedelta(hours=48), t+pd.Timedelta(hours=24)
    rows = [dict(type='macro', label=e['label'], at=e['scheduled_at'],
                 hours_from_trigger=round((pd.Timestamp(e['scheduled_at'])-t).total_seconds()/3600, 2),
                 first_release={k: v.get('change_pct', v.get('change')) for k, v in (e.get('values') or {}).items()})
            for e in macro_events or [] if lo <= pd.Timestamp(e['scheduled_at']) <= hi]
    rows += [dict(type='news', label=i['title'], at=i['available_at'], source=i['source'], url=i['url'],
                  hours_from_trigger=round((pd.Timestamp(i['available_at'])-t).total_seconds()/3600, 2))
             for i in news_items or [] if lo <= pd.Timestamp(i['available_at']) <= hi]
    return sorted(rows, key=lambda r: r['at'])


def _summary(values):
    values = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if not len(values):
        return dict(n=0, mean=None, median=None)
    return dict(n=int(len(values)), mean=float(values.mean()), median=float(np.median(values)))


def _after(panel, episodes, controls, kind):
    chosen = [e for e in episodes if e['kind'] == kind]
    matched = [c for c in controls if c['matched_kind'] == kind]
    def values(items, column):
        return [panel.loc[i['time'], column] for i in items if i['time'] in panel.index]
    sign = -1 if kind == 'drop' else 1
    result = dict(episodes=len(chosen), controls=len(matched), horizons={})
    for label in HORIZONS:
        column = f'fwd_ret_{label}'
        moves = np.array(values(chosen, column), dtype=float)
        moves = moves[np.isfinite(moves)]
        result['horizons'][label] = dict(
            episodes=_summary(moves), controls=_summary(values(matched, column)),
            continuation_share=float((sign*moves > 0).mean()) if len(moves) else None,
            difference_vs_controls=bootstrap_difference(moves, values(matched, column), seed=len(moves)))
    splits = {}
    for name, column, rule in (('funding_negative', 'funding_rate', lambda v: v < 0),
                               ('open_interest_falling', 'oi_change_24h', lambda v: v < 0),
                               ('volume_above_2x_weekly', 'volume_ratio_24h', lambda v: v > 2),
                               ('macro_release_within_24h_before', 'hours_since_cpi', None)):
        yes, no = [], []
        for e in chosen:
            if e['time'] not in panel.index:
                continue
            row = panel.loc[e['time']]
            if rule is None:
                since = [row.get(f'hours_since_{k}') for k in ('cpi', 'jobs', 'fomc')]
                flag = any(s is not None and np.isfinite(s) and s <= 24 for s in since)
            else:
                value = row.get(column)
                if value is None or not np.isfinite(value):
                    continue
                flag = bool(rule(value))
            (yes if flag else no).append(row.get('fwd_ret_24h'))
        splits[name] = dict(yes=_summary(yes), no=_summary(no))
    result['conditional_24h'] = splits
    return result


def study(panel, macro_events=None, news_items=None):
    episodes = find_episodes(panel)
    controls = match_controls(panel, episodes)
    def compare(offset):
        out = {}
        for kind in ('drop', 'rally'):
            group = [e['time']-offset for e in episodes if e['kind'] == kind]
            matched = [c['time']-offset for c in controls if c['matched_kind'] == kind]
            out[kind] = {c: dict(episodes=_summary([panel.loc[t, c] for t in group if t in panel.index]),
                                 controls=_summary([panel.loc[t, c] for t in matched if t in panel.index]))
                         for c in STATE}
        return out
    records = [dict(kind=e['kind'], time=e['time'].isoformat(), ret_24h=e['ret_24h'],
                    before=_row(panel, e['time']-pd.Timedelta(hours=24), STATE),
                    at_trigger=_row(panel, e['time'], STATE), outcomes=_row(panel, e['time'], OUTCOMES),
                    timeline=_timeline(e['time'], macro_events, news_items)) for e in episodes]
    coverage = {c: int(panel[c].notna().sum()) for c in STATE}
    return dict(
        status='descriptive_research', hours=int(panel.close.notna().sum()),
        start=panel.index.min().isoformat(), end=panel.index.max().isoformat(), coverage_hours=coverage,
        episodes=dict(drop=sum(e['kind'] == 'drop' for e in episodes), rally=sum(e['kind'] == 'rally' for e in episodes)),
        controls=len(controls),
        state_at_trigger=compare(pd.Timedelta(0)), state_24h_before=compare(pd.Timedelta(hours=24)),
        before_the_move={c: precursor_table(panel, c) for c in PRECURSORS},
        after_drop=_after(panel, episodes, controls, 'drop'), after_rally=_after(panel, episodes, controls, 'rally'),
        records=records,
        limitations=['Associations only: a nearby release or headline does not establish cause.',
                     'Hours overlap: precursor rates share future windows; distinct_days counts separate dates.',
                     'Episodes are few and cluster in volatile periods; intervals resample episodes as independent.',
                     'Positioning history starts October 2021 and invalid provider rows are excluded.',
                     'News is collected forward only, so historical episodes have no news timeline.',
                     'Macro values are first releases; no consensus forecast, so surprises are not measured.'])


def render(report):
    def pct(v, digits=2):
        return '—' if v is None else f'{v*100:+.{digits}f}%'
    lines = ['# BTC 5% move event study', '',
             f"Hourly BTCUSDT perpetual data, {report['start'][:10]} to {report['end'][:10]} "
             f"({report['hours']:,} complete hours). Drop episodes: **{report['episodes']['drop']}**, "
             f"rally episodes: **{report['episodes']['rally']}**, matched ordinary controls: {report['controls']}.", '',
             '> Descriptive research. Findings are associations, not causes, and not trading signals.', '',
             '## After a 5% drop: continuation or recovery?', '',
             '| Horizon | Median after drops | Median, controls | Share still falling | Mean difference (95% CI) |',
             '|---|---|---|---|---|']
    for label, h in report['after_drop']['horizons'].items():
        diff = h['difference_vs_controls']
        ci = diff['ci95']
        share = '—' if h['continuation_share'] is None else f"{h['continuation_share']*100:.0f}%"
        lines.append(f"| {label} | {pct(h['episodes']['median'])} | {pct(h['controls']['median'])} | {share} | "
                     f"{pct(diff['difference'])} ({pct(ci[0])} to {pct(ci[1])}) |")
    lines += ['', 'Conditional 24h outcome after drops (median, n):', '']
    for name, split in report['after_drop']['conditional_24h'].items():
        lines.append(f"- {name.replace('_', ' ')}: yes {pct(split['yes']['median'])} (n={split['yes']['n']}), "
                     f"no {pct(split['no']['median'])} (n={split['no']['n']})")
    lines += ['', '## State at the trigger: drops vs. ordinary hours (medians)', '',
              '| Measure | 24h before drop | At drop trigger | Controls |', '|---|---|---|---|']
    for column in STATE:
        before = report['state_24h_before']['drop'][column]['episodes']['median']
        at = report['state_at_trigger']['drop'][column]['episodes']['median']
        control = report['state_at_trigger']['drop'][column]['controls']['median']
        fmt = (lambda v: '—' if v is None else f'{v:.4g}')
        lines.append(f'| {column} | {fmt(before)} | {fmt(at)} | {fmt(control)} |')
    lines += ['', '## Before the move: how often a 5% drop follows within 24h, by state', '',
              'Base rate and quintile rates over all hours (touching -5% at any point in the next 24h).', '']
    for column, table in report['before_the_move'].items():
        if not table['buckets']:
            continue
        rates = ', '.join(f"{b['drop_rate']*100:.1f}% ({b['distinct_days']}d)" for b in table['buckets'])
        lines.append(f"- **{column}** base {table['base_rate']*100:.1f}% · lowest→highest quintile: {rates}")
    lines += ['', '## Recent drop episodes', '', '| Trigger (UTC) | 24h move | Next 24h | Macro within -48h/+24h |', '|---|---|---|---|']
    for record in [r for r in report['records'] if r['kind'] == 'drop'][-15:]:
        macro = '; '.join(f"{t['label']} ({t['hours_from_trigger']:+.0f}h)" for t in record['timeline'] if t['type'] == 'macro') or '—'
        lines.append(f"| {record['time'][:16]} | {pct(record['ret_24h'])} | {pct(record['outcomes']['fwd_ret_24h'])} | {macro} |")
    lines += ['', '## Limitations', ''] + [f'- {item}' for item in report['limitations']]
    return '\n'.join(lines)+'\n'


def run(output=None, years=None):
    from quantpilot.data.macro import events as macro_events
    from quantpilot.ai.news import snapshot
    end = pd.Timestamp.now(tz='UTC').floor('h')
    start = end-pd.DateOffset(years=years or settings.market_history_years)
    frames = load_market(None, start-pd.Timedelta(days=10), end)
    try:
        events = macro_events()
        news_items = snapshot(limit=500)['items']
    except Exception:
        events, news_items = [], []
    panel = hourly_panel(frames, events)
    panel = panel[panel.index >= start]
    report = study(panel, events, news_items)
    output = Path(output or settings.artifact_dir/'event-study')
    output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False, default=str), encoding='utf-8')
    (output/'report.md').write_text(render(report), encoding='utf-8')
    return report
