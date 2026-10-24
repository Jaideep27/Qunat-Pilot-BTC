"""The arbiter: from active triggers to at most one action per decision (design spec, section 3.5).

For every playbook whose trigger window is open it evaluates the conditions, builds the plan (rulebook
stop/target, `tradeplan.size` for costs and sizing), asks each layer for a verdict and records the hit
with its status. Layers act only in `live` mode for that playbook; in `shadow` mode they are recorded.
Global gates block all new entries. Selection order: priority, then confluence, then reward:risk.
`manage` handles an open position: time stop, abort conditions and the break-even stop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quantpilot.features import patterns
from quantpilot.rules import rulebook as rules
from quantpilot.simulation import tradeplan
from quantpilot.rules import triggers
from quantpilot.features.context import macro_state

ROUND_TRIP = tradeplan.ROUND_TRIP
LAYERS = ('ml', 'news', 'learner')


def plan_rules(defaults):
    return dict(tradeplan.PLAN_RULES, min_reward_risk=defaults['min_reward_risk'])


def gates(book, t, row, pf, history, account, macro_events=None):
    """Global gates that block every new entry at `t` (name -> detail)."""
    g, t, out = book['gates'], pd.Timestamp(t), {}
    halt = g['cascade_halt']
    lo = pf.index.searchsorted(t-pd.Timedelta(minutes=halt['minutes']), side='right')
    hi = pf.index.searchsorted(t, side='right')
    window = lambda col: pf[col].to_numpy(float)[lo:hi] if col in pf and hi > lo else np.array([np.nan])
    move, liq = np.nanmax(np.abs(window('move_z_15m')), initial=-np.inf), np.nanmax(window('liq_z_15m'), initial=-np.inf)
    if move >= halt['move_z_15m_abs'] or liq >= halt['liq_z_15m']:
        out['cascade_halt'] = dict(move_z=float(move), liq_z=float(liq) if np.isfinite(liq) else None)
    if macro_events and macro_state(macro_events, t)['blackout']:
        out['macro_blackout'] = True
    spread, depth = rules._number(row.get('spread_bps')), rules._number(row.get('depth_15bps_pct'))
    if (spread is not None and spread >= g['thin_book']['spread_bps']) or (depth is not None and depth <= g['thin_book']['depth_pct']):
        out['thin_book'] = dict(spread_bps=spread, depth_pct=depth)
    since = (t-t.floor('8h')).total_seconds()     # settlements at 00/08/16 UTC
    if min(since, 8*3600-since) <= g['funding_window_minutes']*60:
        out['funding_window'] = True
    if account.get('halted') or account.get('paused'):
        out['account_halted'] = True
    return out


def cooldown(book, playbook_id, t, history):
    rule = book['gates']['cooldown']
    trades = sorted((h for h in history if h['playbook'] == playbook_id), key=lambda h: pd.Timestamp(h['exit_time']))
    last = trades[-rule['losses']:]
    return (len(last) == rule['losses'] and all(h['net'] <= 0 for h in last)
            and pd.Timestamp(t)-pd.Timestamp(last[-1]['exit_time']) < pd.Timedelta(hours=rule['hours']))


def _plan(book, pb, features, levels, account):
    close, s = features['close'], rules.SIDES[pb['side']]
    sized = tradeplan.size(pb['side'], close, levels['stop'], levels['target'], account,
                           rules._number(features.get('funding_rate')), plan_rules(book['defaults']))
    plan = dict(side=pb['side'], entry=close, stop=levels['stop'], target=levels['target'], anchor=levels['anchor'],
                quantity=sized['quantity'], reward_risk=sized['reward_risk'], cost_fraction=sized['cost_fraction'],
                leverage=sized['leverage'], min_reward_risk=book['defaults']['min_reward_risk'],
                risk_fraction=s*(close-levels['stop'])/close, playbook=pb['id'], rules_version=book['hash'])
    return plan, sized['rejections']


def widen(book, pb, rec, features, account):
    """The learner's `take_wide`: stop distance and target distance both x `wide_stop_multiplier`."""
    k, s, plan = book['defaults']['wide_stop_multiplier'], rules.SIDES[pb['side']], rec['plan']
    entry = plan['entry']
    target = entry+s*abs(plan['target']-entry)*k
    cap = rules.resolve(pb['target']['cap'], features)[0] if pb['target'].get('cap') else None
    if cap is not None and s*(target-cap) > 0:
        target = cap
    levels = dict(stop=entry-s*abs(entry-plan['stop'])*k, target=target, anchor=plan['anchor'])
    return _plan(book, pb, features, levels, account)


def candidate(book, pb, t, pf, row, trig, account):
    """One playbook at `t`: conditions, levels and plan. Returns (hit record, features)."""
    facts = triggers.episode(trig, t, pf)
    features = row | facts
    regime = patterns.regime(features)
    ev = rules.evaluate(pb, features, regime)
    t = pd.Timestamp(t)
    rec = dict(id=f"{pb['id']}:{t.isoformat()}", playbook=pb['id'], family=pb.get('family'), side=pb['side'],
               time=t.isoformat(), regime=regime, priority=pb['priority'],
               trigger=dict(type=trig.type, time=pd.Timestamp(trig.time).isoformat(), ref=trig.ref,
                            facts={k[8:]: v for k, v in facts.items() if isinstance(v, (int, float)) and k != 'trigger.time'}),
               checks=ev['checks'], missing=ev['missing'], confluence=rules.confluence(features, pb['side']),
               plan=None, layers={}, status=None, reasons=[])
    if not ev['ok']:
        rec['status'] = 'NOT_CONFIRMED'
        return rec, features
    levels = rules.levels(pb, features, book['defaults'])
    if levels['stop'] is None:
        rec.update(status='PLAN_REJECTED', reasons=[levels['reason']])
        return rec, features
    plan, rejections = _plan(book, pb, features, levels, account)
    plan['hit_id'] = rec['id']
    rec['plan'] = plan
    if rejections:
        rec.update(status='PLAN_REJECTED', reasons=rejections)
    return rec, features


def decide(book, t, pf, index, account, history=(), layers=None, modes=None, macro_events=None):
    """All hits at decision time `t` and the single action taken: None, 'enter' or 'flip'."""
    t, layers, modes = pd.Timestamp(t), layers or {}, modes or {}
    active = [(pb, trig) for pb in book['playbooks'] if (trig := index.active(t, pb['trigger'])) is not None]
    if not active:
        return dict(time=t.isoformat(), action=None, chosen=None, hits=[], gates={}, features={})
    row = pf.loc[t].to_dict()
    blocked = gates(book, t, row, pf, history, account, macro_events)
    hits, eligible, inputs = [], [], {}
    for pb, trig in active:
        rec, features = candidate(book, pb, t, pf, row, trig, account)
        hits.append(rec)
        inputs[rec['id']] = features
        if rec['status']:
            continue
        for name, fn in layers.items():
            verdict = fn(rec, features)
            mode = modes.get((name, pb['id']), book['layers'].get(name, 'shadow'))
            rec['layers'][name] = dict(verdict, mode=mode)
            if mode != 'live' or rec['status']:
                continue
            if verdict.get('verdict') in ('skip', 'veto'):
                rec['status'] = f'VETO_{name.upper()}'
            elif name == 'learner' and verdict.get('verdict') == 'take_wide':
                plan, rejections = widen(book, pb, rec, features, account)
                rec['plan'], rec['action'] = plan | dict(hit_id=rec['id']), 'take_wide'
                if rejections:
                    rec.update(status='PLAN_REJECTED', reasons=rejections)
        if rec['status']:
            continue
        if pb.get('shadow_only'):
            rec['status'] = 'SHADOW'
        elif cooldown(book, pb['id'], t, history):
            rec['status'] = 'COOLDOWN'
        else:
            rec.setdefault('action', 'take')
            eligible.append(rec)
    position = account.get('position')
    action, chosen = None, None
    key = lambda r: (r['priority'], -r['confluence']['score'], -(r['plan']['reward_risk'] or 0))
    if blocked:
        for rec in eligible:
            rec.update(status='GATED', reasons=sorted(blocked))
    elif position:
        flips = [r for r in eligible if rules.playbook(book, r['playbook']).get('may_flip')
                 and rules.SIDES[r['side']] != position['side']]
        chosen = min(flips, key=key) if flips else None
        for rec in eligible:
            rec['status'] = 'TAKEN' if rec is chosen else 'POSITION_OPEN'
        action = 'flip' if chosen else None
    elif eligible:
        chosen = min(eligible, key=key)
        for rec in eligible:
            rec['status'] = 'TAKEN' if rec is chosen else 'SKIPPED_LOWER_PRIORITY'
        action = 'enter'
    return dict(time=t.isoformat(), action=action, chosen=chosen, hits=hits, gates=blocked, features=inputs)


def manage(pb, position, row, t, defaults):
    """Exit or tighten an open position at a 15m close: time stop, abort conditions, break-even stop."""
    s, t = position['side'], pd.Timestamp(t)
    if t-pd.Timestamp(position['entry_time']) >= pd.Timedelta(hours=pb['time_stop_hours']):
        return dict(action='exit', reason='TIME_STOP')
    oi_now, oi_entry = rules._number(row.get('open_interest')), rules._number(position.get('open_interest'))
    features = dict(row) | {'position.anchor': position.get('anchor'), 'position.entry': position['entry'],
                            'position.stop': position['stop'], 'position.side': s,
                            'position.oi_change': oi_now/oi_entry-1 if oi_now and oi_entry else None,
                            'position.bars_held': (t-pd.Timestamp(position['entry_time']))/pd.Timedelta(minutes=15)}
    for condition in pb.get('abort', []):
        ok, _ = rules.check(condition, features)
        if ok:
            return dict(action='exit', reason='ABORT', condition=condition)
    risk = abs(position['entry']-position['initial_stop'])/position['entry']
    level = position['entry']*(1+s*ROUND_TRIP)
    if (pb.get('breakeven_at_r') is not None and position.get('max_favorable', 0) >= pb['breakeven_at_r']*risk
            and s*(level-position['stop']) > 0):
        return dict(action='tighten_stop', price=level, reason='BREAKEVEN')
    return None
