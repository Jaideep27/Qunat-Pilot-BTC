"""Trading agent, step B: deterministic trade plans entered by pre-registered rules (amendment A4).

Hourly decisions while flat; plans from `tradeplan` (rules v1); fills, exits, funding and halts in the
`futures-v1` simulator; walk-forward selection and the section 7 pass rule from the strategy research.
Nothing here trades.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from quantpilot.simulation import futures
from quantpilot.research import selective
from quantpilot.simulation import tradeplan
from quantpilot.features.context import context_frame, snapshot_from_row
from quantpilot.research.strategies import DEV_END, blocks, walk_forward

RULES = ('B1', 'B2', 'B3')
CONTROL = ('B0',)
MIN_REWARD_RISK = (1.5, 2.0)
BALANCE = 10_000.0
DEV_START = pd.Timestamp('2021-10-20', tz='UTC')
PRIOR_REGISTRY = Path('artifacts/strategy-research/run-20261020/trials.jsonl')


def decide(ctx, rule, account, rng=None, min_reward_risk=1.5):
    """The rule's plan when it passes every plan rule, else None."""
    rules = dict(tradeplan.PLAN_RULES, min_reward_risk=min_reward_risk)
    tf1, tf4 = ctx.timeframes['1h'], ctx.timeframes['4h']
    if rule == 'B2':
        plans = [p for p in (tradeplan.plan(ctx, side, account, rules) for side in ('long', 'short')) if p.valid]
        return max(plans, key=lambda p: p.reward_risk) if plans else None
    if rule == 'B1':
        side = {1.0: 'long', -1.0: 'short'}.get(tf1.get('trend')) if tf1.get('trend') == tf4.get('trend') else None
    elif rule == 'B3':
        rsi, trend = tf1.get('rsi'), tf4.get('trend')
        side = 'long' if trend == 1 and rsi is not None and rsi < 40 else \
            'short' if trend == -1 and rsi is not None and rsi > 60 else None
    elif rule == 'B0':
        side = 'long' if rng.random() < .5 else 'short'
    else:
        raise ValueError(f'unknown rule {rule}')
    if side is None:
        return None
    plan = tradeplan.plan(ctx, side, account, rules)
    return plan if plan.valid else None


def decision_contexts(frames, start, end, warmup_days=60, chunk_days=365):
    """Context rows at completed 1h closes in [start, end), built in chunks with a causal warm-up."""
    parts, cursor = [], pd.Timestamp(start)
    while cursor < end:
        stop = min(cursor+pd.Timedelta(days=chunk_days), pd.Timestamp(end))
        lo = cursor-pd.Timedelta(days=warmup_days)
        chunk = {k: v[(pd.to_datetime(v.available_at, utc=True) > lo) & (pd.to_datetime(v.available_at, utc=True) <= stop)]
                 for k, v in frames.items() if v is not None and len(v)}
        hours = pd.date_range(cursor.ceil('h'), stop, freq='h', inclusive='left')
        parts.append(context_frame(chunk, at=hours))
        cursor = stop
    frame = pd.concat(parts)
    return frame[~frame.index.duplicated(keep='last')].sort_index()


def _plan_payload(plan, min_reward_risk):
    payload = plan.model_dump(mode='json', exclude={'evidence'})
    return payload | dict(min_reward_risk=min_reward_risk)


def run(frames, contexts, rule, min_reward_risk, macro_events, balance=BALANCE, seed=0):
    """Replay 5m bars through the simulator, deciding at each context row (hourly close) while flat."""
    candles = frames['candles'].copy()
    candles['available_at'] = pd.to_datetime(candles.available_at, utc=True)
    candles = candles.sort_values('available_at').drop_duplicates('available_at')
    candles = candles[(candles.available_at >= contexts.index.min()) & (candles.available_at <= contexts.index.max()+pd.Timedelta(days=1))]
    mark = frames.get('mark')
    marks = candles.close.to_numpy(float)
    if mark is not None and len(mark):
        joined = pd.Series(mark.mark_price.to_numpy(float), index=pd.to_datetime(mark.available_at, utc=True))
        joined = joined[~joined.index.duplicated(keep='last')].reindex(candles.available_at)
        marks = np.where(np.isfinite(joined.to_numpy()), joined.to_numpy(), marks)
    funding = frames.get('funding')
    settle = pd.DataFrame(columns=['time', 'rate'])
    if funding is not None and len(funding):
        settle = pd.DataFrame(dict(time=pd.to_datetime(funding.event_time, utc=True).dt.round('min'),
                                   rate=funding.funding_rate.astype(float))).sort_values('time')
    settle_times, settle_rates, k = pd.DatetimeIndex(settle.time).asi8, settle.rate.to_numpy(float), 0
    decision_times = set(contexts.index)
    rng = np.random.default_rng(seed)
    state, reviews, halt_day = futures.initial(balance), 0, None
    times = candles.available_at
    o, h, l, c = (candles[x].to_numpy(float) for x in ('open', 'high', 'low', 'close'))
    for i, t in enumerate(times):
        iso = t.isoformat()
        futures.step(state, dict(kind='bar', time=iso, open=o[i], high=h[i], low=l[i], close=c[i], mark=marks[i]),
                     inplace=True)
        while k < len(settle_times) and settle_times[k] <= t.value:
            if settle_times[k] > t.value-300_000_000_000:   # settlements inside this bar only
                futures.step(state, dict(kind='funding', time=iso, rate=settle_rates[k], mark=marks[i]), inplace=True)
            k += 1
        if state['drawdown_halt']:
            # Historical evaluation only (amendment A4): the halt is recorded and reviewed at the next UTC day.
            halt_day = halt_day or state['day']
            if state['day'] != halt_day:
                futures.step(state, dict(kind='control', time=iso, review=True), inplace=True)
                reviews, halt_day = reviews+1, None
        if t not in decision_times or state['position'] or state['pending']:
            continue
        account = dict(equity=state['equity'], cash=state['cash'], position=None, paused=state['paused'],
                       halted=state['halted_day'] or state['drawdown_halt'])
        plan = decide(snapshot_from_row(contexts.loc[t], macro_events=macro_events), rule, account, rng,
                      min_reward_risk)
        if plan is not None:
            futures.step(state, dict(kind='action', time=iso, action='enter',
                                     plan=_plan_payload(plan, min_reward_risk)), inplace=True)
    return dict(rule=rule, min_reward_risk=min_reward_risk, trades=state['trades'], state=state,
                drawdown_reviews=reviews, rejections=len(state['rejections']))


def trade_calls(trades):
    """Per-trade net return relative to entry notional (fees, slippage and funding included)."""
    rows = [dict(entry_time=pd.Timestamp(t['entry_time']), exit_time=pd.Timestamp(t['exit_time']), side=t['side'],
                 net=t['net']/(t['quantity']*t['entry']), reason=t['reason']) for t in trades]
    calls = pd.DataFrame(rows, columns=['entry_time', 'exit_time', 'side', 'net', 'reason'])
    calls['hit'] = (calls.net > 0).astype(int)
    return calls


def seed_registry(path, priors=(PRIOR_REGISTRY,)):
    """A run's trial registry starts with every earlier trial, so N counts all of them."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [line for prior in priors if Path(prior).exists()
                 for line in Path(prior).read_text(encoding='utf-8').splitlines() if line.strip()]
        path.write_text('\n'.join(lines)+('\n' if lines else ''), encoding='utf-8')
    return path


def account_summary(result):
    trades = result['trades']
    state = result['state']
    equity, peak, worst = BALANCE, BALANCE, 0.0
    for t in trades:
        equity += t['net']
        peak = max(peak, equity)
        worst = min(worst, equity/peak-1)
    reasons = pd.Series([t['reason'] for t in trades]).value_counts().to_dict() if trades else {}
    return dict(final_equity=state['cash'], account_return=state['cash']/BALANCE-1, max_drawdown_closed=worst,
                trades=len(trades), exit_reasons=reasons, drawdown_reviews=result['drawdown_reviews'],
                rejected_orders=result['rejections'])


def evaluate(results, registry, family, keys):
    """Walk-forward selection over the family's configurations and the section 7 pass rule."""
    calls_by_key = {key: trade_calls(results[key]['trades']) for key in keys}
    entries = [c.entry_time.min() for c in calls_by_key.values() if len(c)]
    block_list = blocks(min(entries)) if entries else []
    pooled, picks, pbo = walk_forward(calls_by_key, block_list)
    bars = sum(len(pd.date_range(s, e, freq='h', inclusive='left')) for s, e in block_list)
    rep = selective.report(pooled, bars)
    sharpes = selective.trial_sharpes(registry, 'intraday', min_calls=30)
    gate = selective.gate(rep, 'intraday', selective.trial_count(registry, 'intraday'),
                          float(np.var(sharpes)) if len(sharpes) > 1 else 0.0, pbo)
    return dict(family=family, configurations=len(keys), picks=picks, pbo=pbo, report=rep, gate=gate)


def key(rule, min_reward_risk):
    return f'{rule}:rr{min_reward_risk}'


def research(output, frames=None, macro_events=None):
    """Run every registered configuration on the development span (resumable per configuration)."""
    from quantpilot.research.event_study import load_market
    from quantpilot.data.macro import events as load_macro
    from quantpilot.storage.database import database
    from quantpilot.config import settings
    folder = Path(output)
    (folder/'runs').mkdir(parents=True, exist_ok=True)
    registry = seed_registry(folder/'trials.jsonl')
    frames = frames if frames is not None else load_market(database(settings.market_database_url),
                                                           DEV_START-pd.Timedelta(days=60), DEV_END)
    frames = {k: v[pd.to_datetime(v.available_at, utc=True) < DEV_END] for k, v in frames.items()}
    macro_events = macro_events if macro_events is not None else load_macro(database())
    contexts = decision_contexts(frames, DEV_START, DEV_END)
    results = {}
    for rule in RULES+CONTROL:
        for rr in MIN_REWARD_RISK:
            name = key(rule, rr)
            path = folder/'runs'/f'{name.replace(":", "_")}.json'
            if path.exists():
                results[name] = json.loads(path.read_text(encoding='utf-8'))
                continue
            result = run(frames, contexts, rule, rr, macro_events)
            result['state'] = {k: v for k, v in result['state'].items() if k not in ('decisions',)}
            path.write_text(json.dumps(result, default=str), encoding='utf-8')
            rep = selective.report(trade_calls(result['trades']), len(contexts))
            selective.register(registry, 'intraday', 'TP-control' if rule in CONTROL else 'TP',
                               dict(rule=rule, min_reward_risk=rr),
                               dict(calls=rep['calls'], mean_net=rep['mean_net'], hit_rate=rep['hit_rate'],
                                    sharpe=rep['sharpe'], scope='development, all trades'))
            results[name] = result
    report = dict(
        generated=pd.Timestamp.now(tz='UTC').isoformat(), development=[str(DEV_START), str(DEV_END)],
        decisions=len(contexts), trials=selective.trial_count(registry, 'intraday'),
        configurations={name: account_summary(r) | dict(selective.report(trade_calls(r['trades']), len(contexts)))
                        for name, r in results.items()},
        families=dict(TP=evaluate(results, registry, 'TP', [key(r, x) for r in RULES for x in MIN_REWARD_RISK]),
                      control=evaluate(results, registry, 'TP-control', [key(r, x) for r in CONTROL for x in MIN_REWARD_RISK])))
    (folder/'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report
