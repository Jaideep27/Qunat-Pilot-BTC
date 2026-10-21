"""Rule engine research, exactly as pre-registered in amendment A6 (docs/research/preregistration.md).

Per yearly chunk: pattern frame, triggers and the arbiter at every completed 15m close on the development
span; every confirmed hit with a plan is simulated under its normal and its wide stop (the counterfactual
record for the ML filter and the learner). Then each playbook is scored one position at a time with the
section 7 pass rule, and the combined arbiter account is replayed in `futures-v1`. Resumable per chunk.
Nothing here trades.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import agent_research, arbiter, futures, outcomes, patterns, rules, selective, tradeplan, triggers
from .strategy_research import DEV_END

DEV_START = pd.Timestamp('2021-10-20', tz='UTC')
WARMUP, TAIL = pd.Timedelta(days=60), pd.Timedelta(days=2)
BALANCE = 10_000.0
TRADEABLE = ('TAKEN', 'SKIPPED_LOWER_PRIORITY')
STRESS_TRIGGERS = ('DUMP', 'SPIKE')
PRIORS = (Path('artifacts/agent-research/run-20261020/trials.jsonl'),)
ML_FEATURES = [
    'move_z_15m', 'move_z_1h', 'volume_z_15m', 'relative_volume', 'taker_share_15m', 'taker_share_1h', 'oi_change_15m',
    'oi_change_1h', 'oi_change_24h', 'oi_z_1h', 'oi_price_state', 'funding_z', 'funding_rate', 'premium', 'global_ratio',
    'top_position_ratio', 'fear_greed', 'vwap_z', 'ema_stack_15m', 'ema_stack_1h', 'ema21_dist_15m_atr', 'ema200_slope_1h',
    'squeeze_on', 'atr_pct_1h', 'vol_forecast_1h', 'volatility_24h', 'range_pos_1h', 'range_pos_4h', 'room_up_atr',
    'room_down_atr', 'rsi_5m', 'rsi_15m', 'rsi_1h', 'rsi_4h', 'adx_15m', 'adx_1h', 'adx_4h', 'macd_hist_15m',
    'macd_hist_1h', 'body_15m', 'upper_wick_15m', 'lower_wick_15m', 'close_loc_15m', 'support_1h_atr', 'resistance_1h_atr',
    'support_4h_atr', 'resistance_4h_atr', 'support_confluence', 'resistance_confluence', 'rsi_div_bull', 'rsi_div_bear',
    'ret_4h', 'ret_24h']
TRIGGER_FACTS = ['oi_change', 'low_to_support_atr', 'high_to_resistance_atr', 'age_bars', 'move']
OUTCOME = ['valid', 'reason', 'entry_time', 'exit_time', 'entry', 'exit', 'net', 'r', 'mae_r', 'mfe_r', 'bars_held',
           'risk_fraction', 'reward_risk', 'target_after_exit', 'breakeven_moved', 'funding', 'post_high', 'post_low',
           'stop', 'target']


def _slice(frames, lo, hi):
    return {k: v[(pd.to_datetime(v.available_at, utc=True) > lo) & (pd.to_datetime(v.available_at, utc=True) <= hi)]
            for k, v in frames.items() if v is not None and len(v)}


def _funding(frames):
    f = frames.get('funding')
    if f is None or not len(f):
        return pd.DatetimeIndex([], tz='UTC'), np.array([])
    f = f.sort_values('event_time')
    return pd.DatetimeIndex(pd.to_datetime(f.event_time, utc=True)), f.funding_rate.to_numpy(float)


def abort_columns(book):
    names = {f for pb in book['playbooks'] for c in pb.get('abort', []) for f in rules._features_in(c)
             if not f.startswith('position.')}
    return sorted(names | {'open_interest', 'funding_rate'})


def wide_levels(book, pb, plan, row):
    k, s = book['defaults']['wide_stop_multiplier'], rules.SIDES[pb['side']]
    entry = plan['entry']
    target = entry+s*abs(plan['target']-entry)*k
    cap = rules._number(row.get(pb['target']['cap'])) if pb['target'].get('cap') else None
    if cap is not None and s*(target-cap) > 0:
        target = cap
    return entry-s*abs(entry-plan['stop'])*k, target


def abort_mask(pb, plan, cols, at15, i, max_bars, row):
    """True at 15m closes after the decision where one of the playbook's abort conditions holds.
    `cols` maps every abort column to its array over the chunk; `at15` marks the 15m closes."""
    mask = np.zeros(len(at15), bool)
    if not pb.get('abort'):
        return mask
    s = rules.SIDES[pb['side']]
    oi_entry = rules._number(row.get('open_interest'))
    end = min(len(at15), i+max_bars+1)
    for j in np.flatnonzero(at15[i+1:end])+i+1:
        oi = cols['open_interest'][j]
        features = {c: v[j] for c, v in cols.items()} | {
            'position.anchor': plan['anchor'], 'position.entry': plan['entry'], 'position.stop': plan['stop'],
            'position.side': s, 'position.oi_change': oi/oi_entry-1 if oi_entry and oi == oi else None}
        if any(rules.check(c, features)[0] for c in pb['abort']):
            mask[j] = True
    return mask


def _outcome(prefix, result):
    result = result or dict(valid=False)
    return {f'{prefix}_{k}': (str(result.get(k)) if k in ('entry_time', 'exit_time') and result.get(k) is not None
                              else result.get(k)) for k in OUTCOME}


def chunk_hits(book, frames, start, end, macro_events, hard_end):
    """Every confirmed hit with plan and both simulated outcomes, plus the chunk's bars and abort rows."""
    data_end = min(end+TAIL, hard_end)
    chunk = _slice(frames, start-WARMUP, data_end)
    pf = patterns.pattern_frame(chunk)
    pf = pf[pf.index <= data_end]
    table = triggers.detect(pf, book, macro_events)
    index = triggers.TriggerIndex(table)
    stress = pd.DatetimeIndex(table[table.type.isin(STRESS_TRIGGERS)].time).sort_values().asi8
    candles = chunk['candles'].assign(available_at=lambda d: pd.to_datetime(d.available_at, utc=True))
    opens = candles.drop_duplicates('available_at').set_index('available_at').open.astype(float)
    bars = outcomes.Bars(t=pf.index, o=opens.reindex(pf.index).to_numpy(float), h=pf.high.to_numpy(float),
                         l=pf.low.to_numpy(float), c=pf.close.to_numpy(float))
    funding = _funding(chunk)
    playbooks = {pb['id']: pb for pb in book['playbooks']}
    decisions = pf.index[(pf.index >= start) & (pf.index < end) & (pf.index == pf.index.floor('15min'))]
    account, rows = dict(equity=BALANCE, cash=BALANCE), []
    position = {t: i for i, t in enumerate(pf.index)}
    at15 = pf.index == pf.index.floor('15min')
    cols = {c: pf[c].to_numpy(float) if c in pf else np.full(len(pf), np.nan) for c in abort_columns(book)}
    for t in decisions:
        out = arbiter.decide(book, t, pf, index, account, macro_events=macro_events)
        if not out['hits']:
            continue
        i = position[t]
        row = pf.iloc[i].to_dict()
        k = np.searchsorted(stress, t.value, side='right')
        stressed = bool(k and t.value-stress[k-1] <= pd.Timedelta(hours=1).value)
        slippage = futures.SLIPPAGE*(book['defaults']['anomaly_slippage_multiplier'] if stressed else 1)
        for rec in out['hits']:
            if rec['status'] == 'NOT_CONFIRMED':
                continue
            pb, plan = playbooks[rec['playbook']], rec['plan']
            base = dict(id=rec['id'], playbook=rec['playbook'], family=rec['family'], side=rec['side'], time=t,
                        regime=rec['regime'], status=rec['status'], reasons=';'.join(rec['reasons']),
                        tradeable=rec['status'] in TRADEABLE, trigger_type=rec['trigger']['type'],
                        trigger_time=rec['trigger']['time'], confluence=rec['confluence']['score'], stressed=stressed,
                        priority=rec['priority'])
            base |= {f'f_{c}': rules._number(row.get(c)) for c in ML_FEATURES}
            base |= {f'tf_{c}': rules._number(rec['trigger']['facts'].get(c)) for c in TRIGGER_FACTS}
            if plan:
                base |= {f'plan_{k}': plan.get(k) for k in ('entry', 'stop', 'target', 'anchor', 'reward_risk',
                                                            'risk_fraction', 'quantity', 'cost_fraction')}
                max_bars = int(min(pb['time_stop_hours'], book['defaults']['max_hold_hours'])*12)
                mask = abort_mask(pb, plan, cols, at15, i, max_bars, row)
                normal = outcomes.simulate(bars, i, pb['side'], plan['stop'], plan['target'], max_bars,
                                           pb.get('breakeven_at_r'), mask, funding, slippage)
                stop_w, target_w = wide_levels(book, pb, plan, row)
                wide = outcomes.simulate(bars, i, pb['side'], stop_w, target_w, max_bars, pb.get('breakeven_at_r'),
                                         mask, funding, slippage)
                base |= _outcome('o', normal) | _outcome('w', wide)
            rows.append(base)
    hits = pd.DataFrame(rows)
    keep = (pf.index > start-pd.Timedelta(minutes=5)) & (pf.index < end)
    frame = pd.DataFrame(dict(open=bars.o, high=bars.h, low=bars.l, close=bars.c,
                              mark=pf['mark'].fillna(pf.close).to_numpy(float)), index=pf.index)[keep]
    aborts = pf.loc[keep & (pf.index == pf.index.floor('15min')), [c for c in abort_columns(book) if c in pf]]
    return hits, frame, aborts, pd.DataFrame(dict(time=funding[0], rate=funding[1]))


def load_hits(output):
    parts = [pd.read_parquet(p) for p in sorted((Path(output)/'hits').glob('*.parquet'))]
    if not parts:
        return pd.DataFrame()
    hits = pd.concat(parts, ignore_index=True)
    hits['time'] = pd.to_datetime(hits.time, utc=True)
    return hits.sort_values(['time', 'priority']).reset_index(drop=True)


def _load(output, name):
    parts = [pd.read_parquet(p) for p in sorted((Path(output)/name).glob('*.parquet'))]
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


def playbook_trades(book, hits, playbook_id):
    """The playbook alone: tradeable hits taken one position at a time, with the 2-loss cooldown."""
    if 'o_valid' not in hits:
        return []
    h = hits[(hits.playbook == playbook_id) & hits.tradeable & (hits.o_valid == True)]  # noqa: E712
    trades, history, last_exit = [], [], None
    for row in h.itertuples():
        if last_exit is not None and row.time < last_exit:
            continue
        if arbiter.cooldown(book, playbook_id, row.time, history):
            continue
        exit_time = pd.Timestamp(row.o_exit_time)
        trades.append(dict(hit_id=row.id, entry_time=row.o_entry_time, exit_time=row.o_exit_time, net=row.o_net,
                           r=row.o_r, reason=row.o_reason, side=row.side, regime=row.regime, stressed=row.stressed))
        history.append(dict(playbook=playbook_id, exit_time=exit_time, net=row.o_net))
        last_exit = exit_time
    return trades


def _calls(trades):
    calls = pd.DataFrame(trades, columns=['entry_time', 'exit_time', 'net'] if not trades else None)
    if len(calls):
        calls['entry_time'], calls['exit_time'] = (pd.to_datetime(calls[c], utc=True) for c in ('entry_time', 'exit_time'))
    calls['hit'] = (calls.net > 0).astype(int) if len(calls) else []
    return calls


def _register_once(registry, family, config, rep):
    key = json.dumps(config, sort_keys=True)
    if any(r['family'] == family and r['key'] == key for r in selective._trials(registry, 'intraday').values()):
        return
    selective.register(registry, 'intraday', family, config,
                       dict(calls=rep['calls'], mean_net=rep['mean_net'], hit_rate=rep['hit_rate'], sharpe=rep['sharpe'],
                            scope='development, all trades'))


def combined(book, hits, bars, aborts, funding):
    """The arbiter over every playbook in one futures-v1 account (amendment A6)."""
    playbooks = {pb['id']: pb for pb in book['playbooks']}
    defaults = book['defaults']
    tradeable = hits[hits.tradeable & hits.plan_stop.notna()] if len(hits) else hits
    fields = ['id', 'playbook', 'side', 'priority', 'confluence', 'stressed', 'plan_entry', 'plan_stop', 'plan_target',
              'plan_anchor', 'plan_reward_risk']
    by_time = {}
    for rec in (tradeable[['time', *fields]].to_dict('records') if len(tradeable) else []):
        by_time.setdefault(rec['time'], []).append(rec)
    abort_rows = aborts.to_dict('index') if len(aborts) else {}
    settle_t, settle_r = pd.DatetimeIndex(funding.time).asi8 if len(funding) else np.array([]), funding.rate.to_numpy(float) if len(funding) else np.array([])
    k = 0
    state, reviews, halt_day, flip_plan, extra = futures.initial(BALANCE), 0, None, None, {}
    at15s = bars.index == bars.index.floor('15min')
    for t, bar, at15 in zip(bars.index, bars.itertuples(), at15s):
        iso = t.isoformat()
        futures.step(state, dict(kind='bar', time=iso, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                                 mark=bar.mark), inplace=True)
        while k < len(settle_t) and settle_t[k] <= t.value:
            if settle_t[k] > t.value-300_000_000_000:
                futures.step(state, dict(kind='funding', time=iso, rate=settle_r[k], mark=bar.mark), inplace=True)
            k += 1
        if state['drawdown_halt']:
            halt_day = halt_day or state['day']
            if state['day'] != halt_day:
                futures.step(state, dict(kind='control', time=iso, review=True), inplace=True)
                reviews, halt_day = reviews+1, None
        if state['pending']:
            continue
        p = state['position']
        if p is None and flip_plan is not None:
            futures.step(state, dict(kind='action', time=iso, action='enter', plan=flip_plan), inplace=True)
            flip_plan = None
            continue
        flip_plan = None
        if p is not None:
            pid = p['plan'].get('playbook')
            info = extra.get(p['plan'].get('hit_id'), {})
            position = dict(side=p['side'], entry=p['entry'], stop=p['stop'], initial_stop=p['initial_stop'],
                            max_favorable=p['max_favorable'], entry_time=p['entry_time'], anchor=info.get('anchor'),
                            open_interest=info.get('open_interest'))
            act = None
            if at15 and pid in playbooks:
                act = arbiter.manage(playbooks[pid], position, abort_rows.get(t, {}), t, defaults)
            elif pid in playbooks:
                risk = abs(p['entry']-p['initial_stop'])/p['entry']
                level = p['entry']*(1+p['side']*arbiter.ROUND_TRIP)
                be = playbooks[pid].get('breakeven_at_r')
                if be is not None and p['max_favorable'] >= be*risk and p['side']*(level-p['stop']) > 0:
                    act = dict(action='tighten_stop', price=level)
            if act and act['action'] == 'exit':
                futures.step(state, dict(kind='action', time=iso, action='exit'), inplace=True)
                continue
            if act and act['action'] == 'tighten_stop':
                futures.step(state, dict(kind='action', time=iso, action='tighten_stop', price=act['price']), inplace=True)
                continue
        if not at15 or t not in by_time or state['halted_day'] or state['drawdown_halt']:
            continue
        history = [dict(playbook=tr['plan'].get('playbook'), exit_time=tr['exit_time'], net=tr['net'])
                   for tr in state['trades']]
        options = [r for r in by_time[t] if not arbiter.cooldown(book, r['playbook'], t, history)]
        if p is not None:
            options = [r for r in options if playbooks[r['playbook']].get('may_flip') and rules.SIDES[r['side']] != p['side']]
        if not options:
            continue
        best = min(options, key=lambda r: (r['priority'], -r['confluence'], -(r['plan_reward_risk'] or 0)))
        account = dict(equity=state['equity'], cash=state['cash'])
        sized = tradeplan.size(best['side'], best['plan_entry'], best['plan_stop'], best['plan_target'], account,
                               rules._number(abort_rows.get(t, {}).get('funding_rate')),
                               arbiter.plan_rules(defaults))
        if sized['quantity'] <= 0 or sized['rejections']:
            continue
        slippage = futures.SLIPPAGE*(defaults['anomaly_slippage_multiplier'] if best['stressed'] else 1)
        plan = dict(side=best['side'], quantity=sized['quantity'], stop=best['plan_stop'], target=best['plan_target'],
                    leverage=sized['leverage'], cost_fraction=sized['cost_fraction'], reward_risk=sized['reward_risk'],
                    min_reward_risk=defaults['min_reward_risk'], rules_version=book['hash'], context_cutoff=iso,
                    slippage=slippage, playbook=best['playbook'], hit_id=best['id'])
        extra[best['id']] = dict(anchor=best['plan_anchor'], open_interest=abort_rows.get(t, {}).get('open_interest'))
        if p is not None:
            futures.step(state, dict(kind='action', time=iso, action='exit'), inplace=True)
            flip_plan = plan
        else:
            futures.step(state, dict(kind='action', time=iso, action='enter', plan=plan), inplace=True)
    return dict(trades=state['trades'], state=state, drawdown_reviews=reviews, rejections=len(state['rejections']))


def research(output, frames=None, macro_events=None, book=None, start=DEV_START, end=DEV_END, chunk_days=365,
             priors=PRIORS):
    """Run amendment A6 (resumable per chunk) and write `report.json`."""
    from .event_study import load_market
    from .macro import events as load_macro
    from .store import database
    from .config import settings
    folder = Path(output)
    for name in ('hits', 'bars', 'aborts', 'funding'):
        (folder/name).mkdir(parents=True, exist_ok=True)
    book = book or rules.load()
    hard_end = min(pd.Timestamp(end), DEV_END)
    registry = agent_research.seed_registry(folder/'trials.jsonl', priors=priors)
    macro_events = macro_events if macro_events is not None else load_macro(database())
    cursor = pd.Timestamp(start)
    while cursor < hard_end:
        stop = min(cursor+pd.Timedelta(days=chunk_days), hard_end)
        name = f'{cursor:%Y%m%d}.parquet'
        if not (folder/'hits'/name).exists():
            chunk = frames if frames is not None else load_market(database(settings.market_database_url),
                                                                  cursor-WARMUP, min(stop+TAIL, hard_end))
            chunk = {k: v[pd.to_datetime(v.available_at, utc=True) < hard_end] for k, v in chunk.items()}
            hits, bars, aborts, funding = chunk_hits(book, chunk, cursor, stop, macro_events, hard_end)
            bars.to_parquet(folder/'bars'/name)
            aborts.to_parquet(folder/'aborts'/name)
            funding.to_parquet(folder/'funding'/name)
            hits.to_parquet(folder/'hits'/name)   # written last: its presence marks the chunk complete
            del chunk
        cursor = stop
    hits = load_hits(folder)
    decisions = len(pd.date_range(pd.Timestamp(start).ceil('15min'), hard_end, freq='15min', inclusive='left'))
    results = {}
    executable = [pb for pb in book['playbooks'] if not pb.get('shadow_only')]
    for pb in executable:
        trades = playbook_trades(book, hits, pb['id']) if len(hits) else []
        rep = selective.report(_calls(trades), decisions)
        _register_once(registry, 'RE', dict(playbook=pb['id'], rulebook=book['hash']), rep)
        results[pb['id']] = dict(name=pb['name'], family=pb.get('family'), side=pb['side'], report=rep, trades=trades,
                                 hits=int((hits.playbook == pb['id']).sum()) if len(hits) else 0,
                                 tradeable=int(((hits.playbook == pb['id']) & hits.tradeable).sum()) if len(hits) else 0)
    bars, aborts = _load(folder, 'bars'), _load(folder, 'aborts')
    funding = pd.concat([pd.read_parquet(p) for p in sorted((folder/'funding').glob('*.parquet'))]).drop_duplicates('time')
    account = combined(book, hits, bars, aborts, funding)
    trades = account['trades']
    calls = agent_research.trade_calls(trades)
    rep = selective.report(calls, decisions)
    _register_once(registry, 'RE-combined', dict(playbook='RE-combined', rulebook=book['hash']), rep)
    sharpes = selective.trial_sharpes(registry, 'intraday', min_calls=30)
    variance, trials = float(np.var(sharpes)) if len(sharpes) > 1 else 0.0, selective.trial_count(registry, 'intraday')
    for result in results.values():
        result['gate'] = selective.gate(result['report'], 'intraday', trials, variance, 0.0)
    summary = agent_research.account_summary(account)
    report = dict(generated=pd.Timestamp.now(tz='UTC').isoformat(), rulebook=book['hash'], development=[str(start), str(hard_end)],
                  decisions=decisions, trials=trials, hits=len(hits), playbooks=results,
                  combined=dict(report=rep, gate=selective.gate(rep, 'intraday', trials, variance, 0.0), account=summary,
                                trades=trades),
                  passing=[pid for pid, r in results.items() if r['gate']['passed']])
    (folder/'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    return report
