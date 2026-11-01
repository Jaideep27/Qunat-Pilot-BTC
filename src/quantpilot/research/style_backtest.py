"""A10 history test of the four account styles on the development span (holdout untouched).

Each style is one fixed configuration, replayed with the live code (style frame, intents, futures-v1 or the carry
ledger) and judged by the A10 pass rule. Resumable: each style's result is written to its own file, so a reaped
run restarts where it stopped. Run detached: `quantpilot research-styles --output artifacts/style-research/<dir>`.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantpilot.research import selective
from quantpilot.simulation import carry as ledger
from quantpilot.simulation import futures
from quantpilot.strategies import base, breakout, carry, market, range_trader, trend

DEV_START, DEV_END = pd.Timestamp('2021-10-20', tz='UTC'), pd.Timestamp('2025-10-20', tz='UTC')
TREND_START, CARRY_START = pd.Timestamp('2017-08-18', tz='UTC'), pd.Timestamp('2020-01-01', tz='UTC')
WARMUP = pd.Timedelta(days=60)
BALANCE = 10_000.0
MIN_TRADES = dict(T1=30, R1=100, B1=100, C1=10)
TRACK = dict(T1='swing', R1='intraday', B1='intraday', C1='swing')
SIGNALS = dict(R1=range_trader.signals, B1=breakout.signals)
REGISTRIES = ('artifacts/strategy-research/run-20261020/trials.jsonl', 'artifacts/agent-research/run-20261020/trials.jsonl',
              'artifacts/rule-engine/research-20261021/trials.jsonl', 'artifacts/rule-engine/research-v2-20261021/trials.jsonl')


def _state():
    return futures.initial(BALANCE, bar_minutes=5, daily_loss=.03, max_drawdown=None, floor=5_000.0)


def _account(state_equity, curve, trades, hold):
    e = pd.Series(curve, dtype=float)
    drawdown = float((e/e.cummax()-1).min()) if len(e) else 0.0
    nets = [t['net'] for t in trades]
    return dict(final_equity=float(state_equity), account_return=float(state_equity/BALANCE-1), max_drawdown=drawdown,
                trades=len(trades), win_rate=float(np.mean([n > 0 for n in nets])) if nets else None,
                buy_and_hold=hold)


def run_intraday(style, candles, funding, start, end, macro_events=()):
    """R1 or B1 on 5m bars: decisions at 15m closes in [start, end), fills and protection on every 5m bar."""
    sf = market.style_frame(candles)
    sig = SIGNALS[style](sf)
    sig = sig[(sig.index >= start) & (sig.index < end)]
    todo = {t: row for t, row in zip(sig.index, sig.to_dict('records'))}
    c = market.candles_of(candles)
    c = c[(c.available_at >= start) & (c.available_at < end)]
    rates = pd.Series(dtype=float) if funding is None or not len(funding) else \
        funding.assign(t=pd.to_datetime(funding.event_time, utc=True)).set_index('t').funding_rate.astype(float).sort_index()
    s, curve, previous = _state(), [], None
    for bar in c.itertuples():
        t = bar.available_at
        futures.step(s, dict(kind='bar', time=t.isoformat(), open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                             mark=bar.close), inplace=True)
        if previous is not None and len(rates):
            for rate in rates[(rates.index > previous) & (rates.index <= t)]:
                futures.step(s, dict(kind='funding', time=t.isoformat(), rate=float(rate), mark=bar.close), inplace=True)
        previous = t
        row = todo.get(t)
        if row is not None and s['position'] is None and s['pending'] is None and not base.in_blackout(t, macro_events):
            intent = base.to_intent(style, t, row)
            futures.step(s, dict(kind='action', time=t.isoformat(), action='enter', plan=base.plan(intent, s['equity'])),
                         inplace=True)
        if t.minute == 0 and t.hour == 0:
            curve.append(s['equity'])
    hold = float(c.close.iloc[-1]/c.open.iloc[0]-1) if len(c) else None
    return dict(style=style, trades=s['trades'], rejections=len(s['rejections']), signals=len(sig),
                account=_account(s['equity'], curve, s['trades'], hold))


def run_trend(daily, funding_daily, start, end):
    """T1 on daily bars (index = each day's close): decide at the close, fill at the next open."""
    f = trend.frame(daily)
    s = futures.initial(BALANCE, bar_minutes=1440, daily_loss=.03, max_drawdown=None, floor=5_000.0)
    blocked, curve, seen = None, [], 0
    for t, row in f.iterrows():
        if t < start or t >= end:
            continue
        futures.step(s, dict(kind='bar', time=t.isoformat(), open=row.open, high=row.high, low=row.low, close=row.close,
                             mark=row.close), inplace=True)
        rate = funding_daily.get(t-pd.Timedelta(days=1)) if funding_daily is not None else None
        if rate is not None and not pd.isna(rate):
            futures.step(s, dict(kind='funding', time=t.isoformat(), rate=float(rate), mark=row.close), inplace=True)
        for tr in s['trades'][seen:]:
            if tr['reason'] == 'STOP':
                blocked = 'long' if tr['side'] == 1 else 'short'
        seen = len(s['trades'])
        vote = int(row.vote)
        if blocked and vote != (1 if blocked == 'long' else -1):
            blocked = None          # one close without that side's agreement re-arms it
        p = s['position']
        if p is not None and s['pending'] is None and not trend.holds(f, t, 'long' if p['side'] == 1 else 'short'):
            futures.step(s, dict(kind='action', time=t.isoformat(), action='exit'), inplace=True)
        elif p is None and s['pending'] is None:
            it = trend.intent(f, t)
            if it is not None and it.side != blocked:
                futures.step(s, dict(kind='action', time=t.isoformat(), action='enter', plan=base.plan(it, s['equity'])),
                             inplace=True)
        curve.append(s['equity'])
    span = f[(f.index >= start) & (f.index < end)]
    hold = float(span.close.iloc[-1]/span.open.iloc[0]-1) if len(span) else None
    return dict(style='T1', trades=s['trades'], rejections=len(s['rejections']),
                account=_account(s['equity'], curve, s['trades'], hold))


def run_carry(rates, price, start, end):
    """C1 at each funding settlement: settle, rebalance if needed, then decide."""
    s = ledger.initial(BALANCE)
    history, curve = [], []
    for t, rate in rates.sort_index().items():
        if t >= end:
            break
        p = float(price.asof(t))
        if s['open']:
            ledger.fund(s, rate, p, t)
            ledger.maybe_rebalance(s, p, t)
        history.append(float(rate))
        if t < start:
            continue
        action = carry.decide(history, s['open'])
        if action == 'open':
            ledger.open_(s, p, t)
        elif action == 'close':
            ledger.close(s, p, t, 'FUNDING_TURNED')
        ledger.mark(s, p, t)
        curve.append(s['equity'])
    funding = sum(t['funding'] for t in s['trades'])+((s['cycle'] or {}).get('funding') or 0.0)
    span = price[(price.index >= start) & (price.index < end)]
    hold = float(span.iloc[-1]/span.iloc[0]-1) if len(span) else None
    years = max((min(end, rates.index.max())-start)/pd.Timedelta(days=365.25), 1e-9)
    acct = _account(s['equity'], curve, s['trades'], hold)
    acct.update(funding=float(funding), annual_return=float((s['equity']/BALANCE)**(1/years)-1), open_at_end=s['open'])
    return dict(style='C1', trades=s['trades'], account=acct)


def judge(style, result, registries=REGISTRIES):
    """The A10 pass rule on the style's closed trades (per-trade result = net / equity at entry)."""
    trades = result['trades']
    calls = pd.DataFrame(dict(net=[t['return_on_equity'] for t in trades],
                              entry_time=pd.to_datetime([t['entry_time'] for t in trades], utc=True, format='ISO8601')))
    rep = selective.report(calls, max(len(trades), 1))
    track = TRACK[style]
    trials = selective.trial_count(list(registries), track)+4
    sharpes = selective.trial_sharpes(list(registries), track, min_calls=30)
    variance = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    gate = selective.gate(rep, track, trials, variance, pbo=0.0)
    luck = selective.binomial_luck([t['net'] > 0 for t in trades])
    criteria = {k: v for k, v in gate['criteria'].items() if k not in ('low_overfitting', 'enough_calls')}
    criteria['enough_trades'] = len(trades) >= MIN_TRADES[style]
    if style == 'C1':     # no win/lose call: judged on the result and its years only (A10)
        criteria = dict(enough_trades=criteria['enough_trades'], positive_mean=criteria['positive_mean'],
                        deflated_sharpe=criteria['deflated_sharpe'], most_years_positive=criteria['most_years_positive'],
                        beats_cash=result['account']['final_equity'] > BALANCE)
    else:
        criteria['not_luck'] = luck['p_value'] < .01
    return dict(report=rep, criteria=criteria, passed=all(criteria.values()), deflated_sharpe=gate['deflated_sharpe'],
                trials=trials, luck=luck)


def _plain(x):
    return json.loads(json.dumps(x, default=str))


def research(output, engine=None):
    """Run every style once (resumable per style) and write report.json."""
    from quantpilot.config import settings
    from quantpilot.data.macro import events as load_macro
    from quantpilot.research.event_study import load_market
    from quantpilot.storage.database import database
    folder = Path(output)
    (folder/'runs').mkdir(parents=True, exist_ok=True)
    results = {}
    for style in ('C1', 'T1', 'R1', 'B1'):
        path = folder/'runs'/f'{style}.json'
        if path.exists():
            results[style] = json.loads(path.read_text(encoding='utf-8'))
            continue
        if style == 'T1':
            panel = pd.read_parquet(Path(settings.data_dir)/'research'/'daily_panel.parquet')
            daily = panel[['open', 'high', 'low', 'close', 'volume']].copy()
            daily.index = daily.index+pd.Timedelta(days=1)          # indexed by the day's close
            result = run_trend(daily, panel.funding_paid, TREND_START+pd.Timedelta(days=1), DEV_END)
        elif style == 'C1':
            rates, price = _carry_inputs(settings, engine)
            result = run_carry(rates, price, CARRY_START, DEV_END)
        else:
            engine = engine or database(settings.market_database_url)
            frames = load_market(engine, DEV_START-WARMUP, DEV_END, families=('funding',))
            frames = {k: v[pd.to_datetime(v.available_at, utc=True) < DEV_END] for k, v in frames.items()}
            result = run_intraday(style, frames['candles'], frames.get('funding'), DEV_START, DEV_END, load_macro(database()))
            del frames
        result['verdict'] = judge(style, result)
        path.write_text(json.dumps(_plain(result)), encoding='utf-8')
        results[style] = _plain(result)
    report = dict(created=pd.Timestamp.now(tz='UTC').isoformat(), amendment='A10',
                  span=dict(development_end=DEV_END.isoformat(), holdout='2025-10-20 to 2026-10-19, untouched'),
                  styles={k: dict(verdict=v['verdict'], account=v['account'], trades=len(v['trades'])) for k, v in results.items()})
    (folder/'report.json').write_text(json.dumps(_plain(report), indent=2), encoding='utf-8')
    return report


def _carry_inputs(settings, engine=None):
    """8-hourly funding (Binance archive 2020-2021, then the PostgreSQL store) and the BTC price at each settlement."""
    from quantpilot.research.event_study import load_market
    from quantpilot.storage.database import database
    research = Path(settings.data_dir)/'research'
    archive = pd.read_parquet(research/'funding_archive.parquet').funding_rate.astype(float)
    engine = engine or database(settings.market_database_url)
    parts = [archive]
    for year in range(2022, DEV_END.year+1):
        lo, hi = pd.Timestamp(f'{year}-01-01', tz='UTC'), min(pd.Timestamp(f'{year+1}-01-01', tz='UTC'), DEV_END)
        f = load_market(engine, lo, hi, families=('funding',)).get('funding')
        if f is not None and len(f):
            parts.append(f.assign(t=pd.to_datetime(f.event_time, utc=True).dt.round('h')).groupby('t').funding_rate.last()
                         .astype(float))
    rates = pd.concat(parts).sort_index()
    rates = rates[~rates.index.duplicated(keep='last')]
    spot = pd.read_parquet(research/'spot_1d.parquet').close.astype(float)
    spot.index = spot.index+pd.Timedelta(days=1)
    hourly = pd.read_parquet(research/'spot_1h.parquet').close.astype(float)
    hourly.index = hourly.index+pd.Timedelta(hours=1)
    price = pd.concat([spot[spot.index < hourly.index.min()], hourly]).sort_index()
    return rates, price
