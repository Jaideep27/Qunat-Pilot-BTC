"""Outlook engine: validated answers to canonical market questions. Never trades."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.isotonic import IsotonicRegression

from . import analogs
from .config import settings

STANDARD = (1, 4, 12, 24, 72, 168)
THRESHOLDS = (.01, .02, .03, .05, .07, .10)
EVIDENCE = ['ret_24h', 'realized_vol_24h', 'rsi_14', 'ema_50_distance', 'volume_ratio_24h', 'taker_buy_share_24h',
            'oi_change_24h', 'funding_rate', 'global_ratio', 'top_position_ratio', 'premium', 'fear_greed']


def report_path():
    return settings.artifact_dir/'outlook'/'validation.json'


def panel_path():
    return settings.artifact_dir/'outlook'/'panel.parquet'


@dataclass
class Context:
    panel: pd.DataFrame
    ranks: pd.DataFrame
    report: dict
    macro_events: list
    news_items: list
    forecasts: list
    price: float | None
    price_time: pd.Timestamp | None
    now: pd.Timestamp
    extra: dict = field(default_factory=dict)


def touch_probability(price, level, sigma):
    """Reflection-principle probability that a driftless log price touches `level` within the horizon."""
    distance = abs(math.log(level/price))
    if distance == 0:
        return 1.0
    if sigma <= 0:
        return 0.0
    return float(min(1.0, 2*(1-norm.cdf(distance/sigma))))


def horizon_sigma(panel, t, h):
    returns = np.log(panel.close).diff()
    variance = returns.pow(2).ewm(span=48, adjust=False).mean()
    value = variance.loc[:t].iloc[-1]
    return float(math.sqrt(value*h)) if np.isfinite(value) else float('nan')


def empirical_touch(panel, t, h, threshold, side):
    """Historical frequency that an h-hour window touched +/- threshold from its starting close."""
    past = panel.loc[:pd.Timestamp(t)-pd.Timedelta(hours=h)]
    if len(past) < h+2:
        return None
    highs = panel.high.iloc[::-1].rolling(h, min_periods=h).max().iloc[::-1].shift(-1).reindex(past.index)
    lows = panel.low.iloc[::-1].rolling(h, min_periods=h).min().iloc[::-1].shift(-1).reindex(past.index)
    up, down = (highs/past.close-1 >= threshold), (lows/past.close-1 <= -threshold)
    hit = {'up': up, 'down': down, 'either': up | down}[side]
    valid = highs.notna() & lows.notna()
    return float(hit[valid].mean()) if valid.any() else None


def standardized_quantiles(panel, t, h, coverage):
    past = panel.loc[:pd.Timestamp(t)-pd.Timedelta(hours=h)]
    returns = np.log(panel.close).diff().pow(2).ewm(span=48, adjust=False).mean()
    sigma = np.sqrt(returns.reindex(past.index)*h)
    forward = (panel.close.shift(-h)/panel.close-1).reindex(past.index)
    z = (forward/sigma).replace([np.inf, -np.inf], np.nan).dropna()
    tail = (1-coverage)/2
    return float(z.quantile(tail)), float(z.quantile(1-tail))


def _nearest(h):
    return min(STANDARD, key=lambda s: (abs(math.log(h/s)), s))


def _confidence(gate):
    if not gate or not gate.get('promoted'):
        return 'no_edge'
    return 'weak' if gate.get('relative_improvement', 0) < .01 or gate.get('windows', 0) < 300 else 'validated'


def _evidence(ctx, t, keys=EVIDENCE):
    row = ctx.panel.loc[t]
    out = {}
    for key in keys:
        value = row.get(key)
        series = ctx.panel[key].loc[:t].dropna() if key in ctx.panel else pd.Series(dtype=float)
        pct = float((series <= value).mean()*100) if len(series) and pd.notna(value) else None
        out[key] = dict(value=None if pd.isna(value) else float(value), percentile=pct, label=key.replace('_', ' '))
    return out


def _events(ctx, h):
    lo, hi = ctx.now-pd.Timedelta(hours=24), ctx.now+pd.Timedelta(hours=h)
    rows = []
    for e in ctx.macro_events:
        at = pd.Timestamp(e['scheduled_at'])
        if lo <= at <= hi:
            rows.append(dict(label=e['label'], at=e['scheduled_at'],
                             hours_from_now=round((at-ctx.now).total_seconds()/3600, 1),
                             first_release={k: v.get('change_pct', v.get('change')) for k, v in (e.get('values') or {}).items()}))
    return sorted(rows, key=lambda r: r['at'])


def _news(ctx, hours=24):
    cutoff = ctx.now-pd.Timedelta(hours=hours)
    return [dict(title=i['title'], url=i['url'], source=i['source'], at=i['available_at'])
            for i in ctx.news_items if pd.Timestamp(i['available_at']) >= cutoff][:8]


def answer(question, ctx):
    """Answer a canonical question with the best validated method available."""
    q = question
    warnings = []
    base = dict(question=q.model_dump(), asked_at=ctx.now.isoformat(), price=ctx.price, data_time=None, stale=False,
                warnings=warnings, range=None, context={}, evidence={}, events=_events(ctx, q.horizon_hours),
                news=_news(ctx), track_record=dict(validation=None))
    if ctx.panel is None or ctx.panel.empty or ctx.price is None:
        warnings.append('Outlook data is warming up; try again after the hourly refresh.')
        base['headline'] = dict(kind='explain' if q.type == 'explain' else 'probability', probability=None,
                                base_rate=None, method='unavailable', confidence='no_edge', ci=None)
        return base
    t = ctx.panel.index[ctx.panel.index <= ctx.now].max()
    base['data_time'] = t.isoformat()
    if ctx.now-t > pd.Timedelta(hours=2):
        base['stale'] = True
        warnings.append(f'Market data is stale: last completed hour {t.isoformat()}.')
    if ctx.price_time is not None and ctx.now-pd.Timestamp(ctx.price_time) > pd.Timedelta(minutes=10):
        base['stale'] = True
        age = int((ctx.now-pd.Timestamp(ctx.price_time)).total_seconds()//60)
        warnings.append(f'Latest price is {age} minutes old; the live collector may be stopped.')
    if q.type == 'touch' and not .2 <= q.level/ctx.price <= 5:
        raise ValueError(f'Touch level ${q.level:,.0f} is implausible against the current price ${ctx.price:,.0f}; '
                         'use a level within 0.2x-5x of the price (for example 120k for $120,000).')
    if not ctx.report:
        warnings.append('No validation report yet; every method is treated as unvalidated.')
    h = q.horizon_hours
    base['evidence'] = _evidence(ctx, t)
    if q.type == 'direction':
        outcome = analogs.forward_up(ctx.panel, h)
        estimate = analogs.estimate(ctx.ranks, outcome, t, h)
        if estimate.get('missing'):
            warnings.append('Similar-situations estimate skipped: missing ' + ', '.join(estimate['missing']) + '.')
        gate = (ctx.report.get('analogs') or {}).get(f'{_nearest(h)}h')
        confidence = _confidence(gate)
        # Stale or incomplete state never feeds a promoted method: the base rate needs no current state.
        use = confidence != 'no_edge' and estimate['probability'] is not None and not base['stale']
        probability, base_rate = estimate['probability'] if use else estimate['base_rate'], estimate['base_rate']
        ci = estimate['ci'] if use else None
        if q.side == 'down':   # the question asks for P(fall): report the complement, with its interval mirrored
            probability, base_rate = _complement(probability), _complement(base_rate)
            ci = [1-ci[1], 1-ci[0]] if ci else None
        base['question']['side'] = q.side
        base['headline'] = dict(kind='probability', probability=probability, base_rate=base_rate,
                                method='analogs' if use else 'base_rate', confidence=confidence if use else 'no_edge',
                                ci=ci)
        base['context']['analog'] = estimate
        base['track_record']['validation'] = gate
    elif q.type in ('touch', 'big_move'):
        sigma = horizon_sigma(ctx.panel, t, h)
        if q.type == 'touch':
            side = 'up' if q.level >= ctx.price else 'down'
            base['question']['side'] = q.side or ('above' if side == 'up' else 'below')
            threshold = abs(q.level/ctx.price-1)
            raw = touch_probability(ctx.price, q.level, sigma)
        else:
            side, threshold = q.side, q.threshold_pct/100
            up = touch_probability(ctx.price, ctx.price*(1+threshold), sigma)
            down = touch_probability(ctx.price, ctx.price*(1-threshold), sigma)
            raw = {'up': up, 'down': down, 'either': min(1.0, up+down)}[side]
        empirical = empirical_touch(ctx.panel, t, h, threshold, side)
        touch = ctx.report.get('touch') or {}
        calibrated = float(np.interp(raw, touch['calibration_x'], touch['calibration_y'])) \
            if touch.get('use_calibration') else None
        method, probability = ('calibrated_volatility', calibrated) if calibrated is not None and touch.get('promoted') \
            else (('volatility', raw) if touch.get('promoted') else ('historical_frequency', empirical))
        confidence = _confidence(touch)
        if base['stale']:   # stale state never feeds the validated volatility method
            method, probability, confidence = 'historical_frequency', empirical, 'no_edge'
        base['headline'] = dict(kind='probability', probability=probability, base_rate=empirical, method=method,
                                confidence=confidence, ci=None)
        base['context'].update(sigma=sigma, raw_probability=raw, calibrated_probability=calibrated,
                               historical_frequency=empirical, threshold=threshold, side=side)
        base['track_record']['validation'] = touch or None
    elif q.type == 'range':
        latest = next((f for f in ctx.forecasts if f['horizon'] == f'{h}h' and f.get('range_status') == 'model'
                       and ctx.now-pd.Timestamp(f['cutoff']) <= pd.Timedelta(minutes=30)), None)
        if latest and abs(q.coverage-.8) < 1e-9:
            low, high, method = ctx.price*(1+latest['return_q10']), ctx.price*(1+latest['return_q90']), 'range_model'
        else:
            z_low, z_high = standardized_quantiles(ctx.panel, t, h, q.coverage)
            sigma = horizon_sigma(ctx.panel, t, h)
            low, high, method = ctx.price*(1+z_low*sigma), ctx.price*(1+z_high*sigma), 'volatility_scaled_history'
        coverage = (ctx.report.get('ranges') or {}).get(f'{_nearest(h)}h')
        base['range'] = dict(low=float(low), high=float(high), coverage=q.coverage, method=method)
        base['headline'] = dict(kind='range', probability=None, base_rate=None, method=method,
                                confidence='validated' if coverage and coverage.get('promoted') and not base['stale']
                                else 'weak', ci=None)
        base['track_record']['validation'] = coverage
    else:
        lookback = ctx.panel.loc[:t].iloc[-(h+1):]
        change = float(lookback.close.iloc[-1]/lookback.close.iloc[0]-1) if len(lookback) > 1 else None
        base['evidence']['return_window'] = dict(value=change, percentile=None, label=f'return over last {h}h')
        base['headline'] = dict(kind='explain', probability=None, base_rate=None, method='evidence',
                                confidence='no_edge', ci=None)
    return base


def _complement(p):
    return None if p is None else 1-p


def refresh_panel(now=None, full=False):
    """Rebuild the cached hourly panel: all five years when missing, otherwise the last 45 days."""
    from .event_study import hourly_panel, load_market
    from .macro import events as macro_events
    now = pd.Timestamp(now or datetime.now(timezone.utc)).floor('h')
    path = panel_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cached = pd.read_parquet(path) if path.exists() and not full else None
    start = now-pd.DateOffset(years=settings.market_history_years)-pd.Timedelta(days=10) if cached is None \
        else now-pd.Timedelta(days=45)
    fresh = hourly_panel(load_market(None, start, now), macro_events())
    if cached is not None:
        keep_from = start+pd.Timedelta(days=14)   # warm-up rows of the partial rebuild are discarded
        fresh = pd.concat([cached[cached.index < keep_from], fresh[fresh.index >= keep_from]])
    fresh.to_parquet(path.with_suffix('.partial'))
    path.with_suffix('.partial').replace(path)
    return dict(rows=len(fresh), first=fresh.index.min().isoformat(), last=fresh.index.max().isoformat())


def load_context(now=None, engine=None):
    from sqlalchemy import func, select
    from .forecaster import forecasts
    from .macro import events as macro_events
    from .market_store import PERP, MarketCandle
    from .news import snapshot
    from .store import database
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    path = panel_path()
    panel = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    report = json.loads(report_path().read_text(encoding='utf-8')) if report_path().exists() else {}
    with database(settings.market_database_url).connect() as connection:
        # Completed candles known by `now` only: never a forming bar or a timestamp after the cutoff.
        latest = connection.execute(select(MarketCandle.close, MarketCandle.available_at).where(
            MarketCandle.market == PERP, MarketCandle.interval == '5m', MarketCandle.complete.is_(True),
            MarketCandle.available_at <= now.to_pydatetime())
            .order_by(MarketCandle.event_time.desc()).limit(1)).first()
    price, price_time = (float(latest[0]), pd.Timestamp(latest[1])) if latest else (None, None)
    engine = engine or database()
    return Context(panel=panel, ranks=analogs.state_ranks(panel) if not panel.empty else pd.DataFrame(),
                   report=report, macro_events=macro_events(engine), news_items=snapshot(engine, 50)['items'],
                   forecasts=forecasts(engine, 20), price=price, price_time=price_time, now=now)


def validate_outlook(panel=None, step_hours=12, burn_in_days=365, confirm_days=365):
    """Walk-forward checks for analogs (direction), touch calibration and range coverage; writes the report."""
    panel = panel if panel is not None else pd.read_parquet(panel_path())
    report = dict(analogs=analogs.validate(panel, STANDARD, step_hours, burn_in_days, confirm_days))
    rows = []
    start = panel.index.min()+pd.Timedelta(days=burn_in_days)
    for t in panel.index[panel.index >= start][::step_hours]:
        for h in STANDARD:
            end = t+pd.Timedelta(hours=h)
            if end > panel.index.max():
                continue
            window = panel.loc[t+pd.Timedelta(hours=1):end]
            sigma = horizon_sigma(panel, t, h)
            price = panel.close.loc[t]
            for threshold in THRESHOLDS:
                for side, level in (('up', price*(1+threshold)), ('down', price*(1-threshold))):
                    hit = window.high.max() >= level if side == 'up' else window.low.min() <= level
                    rows.append((t, h, threshold, side, touch_probability(price, level, sigma), float(hit)))
    frame = pd.DataFrame(rows, columns=['t', 'h', 'threshold', 'side', 'p', 'y']).set_index('t')
    split = frame.index.max()-pd.Timedelta(days=confirm_days)
    val, conf = frame[frame.index <= split], frame[frame.index > split]
    climate = val.groupby(['h', 'threshold', 'side']).y.mean().rename('base')
    conf = conf.join(climate, on=['h', 'threshold', 'side'])
    iso = IsotonicRegression(out_of_bounds='clip', y_min=0, y_max=1).fit(val.p, val.y)
    brier = lambda p, y: float(np.mean((np.asarray(p)-np.asarray(y))**2))
    raw, calibrated, base = brier(conf.p, conf.y), brier(iso.predict(conf.p), conf.y), brier(conf.base, conf.y)
    best = min(raw, calibrated)
    grid = np.linspace(0, 1, 101)
    report['touch'] = dict(windows=int(len(frame)), brier_raw=raw, brier_calibrated=calibrated, brier_base=base,
                           use_calibration=calibrated < raw, promoted=best < base,
                           relative_improvement=float((base-best)/base) if base else 0.0,
                           calibration_x=grid.tolist(), calibration_y=iso.predict(grid).tolist())
    ranges = {}
    for h in STANDARD:
        hits = []
        for t in conf.index.unique()[::2]:
            z_low, z_high = standardized_quantiles(panel, t, h, .8)
            sigma, price = horizon_sigma(panel, t, h), panel.close.loc[t]
            end = t+pd.Timedelta(hours=h)
            if end in panel.index:
                hits.append(price*(1+z_low*sigma) <= panel.close.loc[end] <= price*(1+z_high*sigma))
        coverage = float(np.mean(hits)) if hits else None
        ranges[f'{h}h'] = dict(coverage_80=coverage, windows=len(hits),
                               promoted=bool(coverage is not None and abs(coverage-.8) <= .05))
    report['ranges'] = ranges
    report['generated_at'] = datetime.now(timezone.utc).isoformat()
    report_path().parent.mkdir(parents=True, exist_ok=True)
    report_path().write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report
