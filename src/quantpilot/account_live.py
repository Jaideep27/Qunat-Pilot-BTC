"""A8 live fan-out: the four paper accounts act on the setups the reference rule engine confirmed.

Called from `rule_live.cycle` after the reference account. The shared pipeline (patterns, triggers, playbook
checklists, stop and target) runs once; each account then steps its own position through the new 1m bars,
numbers and journals every closed trade, pauses after `round_length` trades, and at a fresh 15m decision
scores each confirmed setup (frozen model), applies its cutoff, gates and sizing, and records one decision
row per setup. Accounts never change their own config and never flip. Nothing here places real orders.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import account_journal, account_review, account_score, accounts, arbiter, futures, journal, meta, model_health, rule_live
from .context import macro_state

CONFIRMABLE = {'REWARD_RISK_BELOW_MIN', 'BELOW_EXCHANGE_MINIMUM', 'INSUFFICIENT_MARGIN', 'STOP_BEYOND_LIQUIDATION'}
SNAPSHOT = ('relative_volume', 'global_ratio', 'top_position_ratio', 'funding_rate', 'oi_change_1h', 'taker_share_1h',
            'imbalance_15bps', 'spread_bps', 'liq_long_usd_15m', 'liq_short_usd_15m', 'fear_greed', 'atr_1h')
STOPPED = ('PAUSED_FOR_REVIEW', 'FLOOR_HIT')
_cache = {}


def default_scorer():
    """`(hit, features) -> {p, score, missing}` from the frozen model (reloaded when the file changes), or None."""
    path = Path(account_score.model_path())
    key = (str(path), path.stat().st_mtime if path.exists() else None)
    if _cache.get('scorer_key') != key:
        _cache.update(scorer_key=key, model=account_score.load_model(path))
    model = _cache['model']
    if model is None:
        return None

    challenger = model_health.load_challenger()

    def scorer(hit, features):
        row = account_score.live_row(hit, features)
        out = account_score.score(model, row).iloc[0]
        shadow = None
        if challenger is not None:     # recorded only; a challenger never decides
            c = account_score.score(challenger, row).iloc[0]
            shadow = dict(hash=challenger['hash'], p=float(c.p), score=float(c.score))
        return dict(p=float(out.p), score=float(out.score), missing=list(out.missing), inputs=_inputs(row), challenger=shadow)
    scorer.hash = model['hash']
    return scorer


def _inputs(row):
    """The setup's raw model row as plain values (stored with each decision for drift and the challenger)."""
    out = {}
    for k, v in row.iloc[0].items():
        out[k] = v if isinstance(v, str) or v is None else _num(v)
    return out


def check_inputs(sc, ref):
    """A9 data check: a list of failures (empty passes), or a note when it could not run."""
    inputs = (sc or {}).get('inputs')
    if inputs is None:
        return 'no inputs'
    if ref is None:
        return 'no reference'
    return model_health.data_check(meta.matrix(pd.DataFrame([inputs])).iloc[0].to_dict(), ref)


def new_state(cfg, account, now, last_bar):
    return dict(futures=futures.initial(cfg['balance'], bar_minutes=1, daily_loss=account['daily_brake'], max_drawdown=None,
                                        floor=cfg['floor']),
                name=account['name'], round=1, in_round=0, number=0, status='RUNNING', last_bar=last_bar,
                last_decision=None, flip_plan=None, extra={}, config_hash=cfg['hash'], created=pd.Timestamp(now).isoformat(),
                last=None)


def _num(value):
    try:
        v = float(value)
        return v if pd.notna(v) else None
    except (TypeError, ValueError):
        return None


def snapshot(pf, t, row, news_events, macro_events):
    """The market at decision time, as stored with every decision."""
    close = _num(row.get('close'))
    closes = pf['close'] if 'close' in pf else pd.Series(dtype=float)

    def ret(delta):
        past = closes[closes.index <= t-delta]
        return close/float(past.iloc[-1])-1 if close and len(past) and past.iloc[-1] else None
    news = (news_events or [None])[0]
    nxt = macro_state(macro_events, t)['next']
    return dict(time=pd.Timestamp(t).isoformat(), close=close, ret_1h=ret(pd.Timedelta(hours=1)),
                ret_4h=ret(pd.Timedelta(hours=4)), ret_24h=ret(pd.Timedelta(hours=24)),
                **{k: _num(row.get(k)) for k in SNAPSHOT},
                news_latest=news and {k: news.get(k) for k in ('title', 'direction', 'impact', 'category', 'available_at')},
                macro_next=nxt)


def _confirmed(book, hits):
    shadow = {pb['id'] for pb in book['playbooks'] if pb.get('shadow_only')}
    return [h for h in hits if h.get('plan') and h['status'] not in ('NOT_CONFIRMED', 'SHADOW')
            and h['playbook'] not in shadow and set(h.get('reasons') or []) <= CONFIRMABLE]


def _record_trades(engine, account_id, acct, before, round_length):
    for i, trade in enumerate(acct['futures']['trades'][before:], start=before):
        acct['number'] += 1
        acct['in_round'] += 1
        account_journal.add_trade(engine, account_id, acct['round'], acct['number'], trade, i)
    f = acct['futures']
    if f.get('floor_hit'):
        acct['status'] = 'FLOOR_HIT'
    elif acct['in_round'] >= round_length and acct['status'] != 'PAUSED_FOR_REVIEW':
        acct['status'], f['paused'] = 'PAUSED_FOR_REVIEW', True


def _decide(cfg, account, acct, book, pf, table, hits, features, scores, t15, macro_events, news_events, scorer, checks=None):
    state, iso = acct['futures'], pd.Timestamp(t15).isoformat()
    row = pf.loc[t15].to_dict() if t15 in pf.index else {}
    snap = snapshot(pf, t15, row, news_events, macro_events)
    minutes = None if not snap['macro_next'] else snap['macro_next']['hours']*60
    history = [dict(playbook=tr['plan'].get('playbook'), exit_time=tr['exit_time'], net=tr['net']) for tr in state['trades']]
    view = dict(halted=state['halted_day'] or state.get('floor_hit'), paused=state['paused'])
    blocked = arbiter.gates(book, t15, row, pf, history, view, macro_events) if row else {}
    busy = bool(state['position'] or state['pending'])
    rows, eligible = [], []
    for hit in hits:
        sc = scores.get(hit['id'])
        plan = hit['plan']
        sized = accounts.size(account, dict(plan, side=hit['side']), sc and sc['score'], state['equity'], state['cash'],
                              _num(features.get(hit['id'], {}).get('funding_rate'))) if sc else None
        if scorer is None:
            status = 'NO_SCORE_MODEL'
        elif acct['status'] in STOPPED or state['paused']:
            status = 'PAUSED'
        elif isinstance(check := (checks or {}).get(hit['id']), list) and check:
            status = 'DATA_CHECK_FAILED'
        elif not accounts.eligible(account, sc['score']):
            status = 'BELOW_CUTOFF'
        elif sized['rejections']:
            status = 'PLAN_REJECTED'
        elif arbiter.cooldown(book, hit['playbook'], t15, history):
            status = 'COOLDOWN'
        elif blocked:
            status = 'GATED'
        elif busy:
            status = 'POSITION_OPEN'
        else:
            status = None
        record = dict(account=account['id'], hit_id=hit['id'], time=t15, decision=status, score=sc and sc['score'],
                      confidence=sc and sc['p'], cutoff=account['cutoff_score'], trade_id=None, payload=dict(
                          playbook=hit['playbook'], side=hit['side'], regime=hit.get('regime'), trigger=hit.get('trigger'),
                          plan={k: plan.get(k) for k in ('entry', 'stop', 'target', 'reward_risk', 'anchor')},
                          ticks=hit.get('checks', []), missing_inputs=hit.get('missing', []),
                          watch=accounts.watch_items(hit['side'], row, snap['news_latest'], minutes),
                          sizing=sized, gates=sorted(blocked), snapshot=snap, missing_features=sc and sc['missing'],
                          inputs=sc and sc.get('inputs'), data_check=(checks or {}).get(hit['id']),
                          challenger=sc and sc.get('challenger'),
                          model=dict(score_model=getattr(scorer, 'hash', None), config=cfg['hash'], rulebook=book['hash'])))
        rows.append(record)
        if status is None:
            eligible.append(dict(record, plan=plan, sized=sized))
    chosen = accounts.choose(eligible)
    for record in rows:
        if record['decision'] is None:
            record['decision'] = 'TAKEN' if chosen and record['hit_id'] == chosen['hit_id'] else 'LOWER_SCORE'
    if chosen:
        hit_id, plan, sized = chosen['hit_id'], chosen['plan'], chosen['sized']
        stressed = bool(len(table) and ((table.type.isin(rule_live.STRESS)) & (table.time > t15-pd.Timedelta(hours=1))
                                        & (table.time <= t15)).any())
        entry = dict(side=chosen['payload']['side'], entry=plan['entry'], stop=plan['stop'], target=plan['target'],
                     anchor=plan.get('anchor'), reward_risk=plan.get('reward_risk'), cost_fraction=sized['cost_fraction'],
                     quantity=sized['quantity'], leverage=sized['leverage'], min_reward_risk=None,
                     playbook=chosen['payload']['playbook'], hit_id=hit_id, rules_version=book['hash'], context_cutoff=iso,
                     slippage=futures.SLIPPAGE*(book['defaults']['anomaly_slippage_multiplier'] if stressed else 1))
        acct['extra'][hit_id] = dict(anchor=plan.get('anchor'), open_interest=row.get('open_interest'), score=chosen['score'])
        futures.step(state, dict(kind='action', time=iso, action='enter', plan=entry), inplace=True)
        next(r for r in rows if r['hit_id'] == hit_id)['trade_id'] = f"{account['id']}:{hit_id}"
    if rows:
        top = next((r for r in rows if r['decision'] == 'TAKEN'), max(rows, key=lambda r: r['score'] or -1e9))
        ticks = top['payload']['ticks']
        acct['last'] = dict(time=iso, hit_id=top['hit_id'], playbook=top['payload']['playbook'], side=top['payload']['side'],
                            decision=top['decision'], score=top['score'], cutoff=top['cutoff'], confidence=top['confidence'],
                            passed=sum(bool(c.get('ok')) for c in ticks), total=len(ticks))
    return rows


def step_all(engine, book, pf, table, fast, decision, features, now, frames, macro_events, news_events, cfg=None,
             scorer=None):
    """One minute for every account. Returns {account id: summary}."""
    cfg = cfg or accounts.load()
    if scorer is None:
        scorer = default_scorer()
        if scorer is not None and getattr(scorer, 'hash', None) != cfg['model']['hash']:
            scorer = None    # the model on disk is not the one the config was resolved from
    t15 = pd.Timestamp(decision['time']) if decision else None
    hits = _confirmed(book, decision['hits']) if decision else []
    scores = {h['id']: scorer(h, features.get(h['id'], {})) for h in hits} if scorer else {}
    ref = None
    if getattr(scorer, 'hash', None):
        try:
            ref = model_health.load_reference(scorer.hash)
        except Exception:
            ref = None     # monitoring never stops trading
    checks = {h: check_inputs(sc, ref) for h, sc in scores.items()}
    out = {}
    for account in cfg['accounts']:
        acct = journal.load_account(engine, account['id'])
        if acct is None:
            acct = new_state(cfg, account, now, fast.available_at.max().isoformat() if len(fast) else now.floor('min').isoformat())
        new = fast[fast.available_at > pd.Timestamp(acct['last_bar'])]
        before = len(acct['futures']['trades'])
        rule_live._step_bars(book, acct, new, pf, frames.get('funding'))
        _record_trades(engine, account['id'], acct, before, cfg['round_length'])
        if acct['status'] not in STOPPED:
            acct['status'] = 'RUNNING' if scorer else 'NO_SCORE_MODEL'
        rows = []
        if t15 is not None and (acct['last_decision'] is None or t15 > pd.Timestamp(acct['last_decision'])):
            rows = _decide(cfg, account, acct, book, pf, table, hits, features, scores, t15, macro_events, news_events, scorer,
                           checks)
            account_journal.add_decisions(engine, rows)
            acct['last_decision'] = t15.isoformat()
        f = acct['futures']
        f['decisions'], f['rejections'] = f['decisions'][-500:], f['rejections'][-500:]
        journal.save_account(engine, account['id'], acct, now)
        out[account['id']] = dict(status=acct['status'], equity=f['equity'], trades=acct['number'], in_round=acct['in_round'],
                                  decided=len(rows), taken=sum(r['decision'] == 'TAKEN' for r in rows))
    out['diagnosed'] = account_review.diagnose_trades(engine, fast, now)   # explanations run in their own thread
    return out
