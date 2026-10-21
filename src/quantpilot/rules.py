"""The rulebook (rulebook/v1.yaml) and its evaluator: a fixed operator table over named features.

Nothing in the rulebook is executed as code. A condition is `[feature, op, value-or-feature]` or an
`any`/`all` group; a missing or non-finite input makes its leaf false and is reported. Stops and targets
are built from the playbook's anchors in 1h-ATR units. See the design spec, section 3.4.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import math
import operator
from pathlib import Path

import yaml

RULEBOOK = Path(__file__).resolve().parents[2]/'rulebook'/'v1.yaml'
OPS = {'<': operator.lt, '<=': operator.le, '>': operator.gt, '>=': operator.ge, '==': operator.eq,
       '!=': operator.ne}
SIDES = {'long': 1, 'short': -1}
TRIGGER_FIELDS = {'oi_change', 'low', 'high', 'low_to_support_atr', 'high_to_resistance_atr', 'range_high',
                  'range_low', 'range_mid', 'confidence', 'age_bars', 'direction', 'move', 'start_price', 'level'}
POSITION_FIELDS = {'oi_change', 'anchor', 'entry', 'stop', 'target', 'bars_held', 'r', 'side'}


def load(path=RULEBOOK):
    raw = Path(path).read_bytes().replace(b'\r\n', b'\n')
    book = yaml.safe_load(raw.decode('utf-8'))
    book['hash'] = hashlib.sha256(raw).hexdigest()[:12]
    book['path'] = str(path)
    return book


@lru_cache(maxsize=4)
def cached(path=str(RULEBOOK)):
    return load(path)


def playbook(book, playbook_id):
    return next(p for p in book['playbooks'] if p['id'] == playbook_id)


def _number(value):
    if value is None or isinstance(value, bool):
        return None if value is None else float(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def resolve(ref, features):
    """(value, missing name or None) for a feature name or a literal number."""
    if isinstance(ref, str):
        value = _number(features.get(ref))
        return value, (ref if value is None else None)
    return _number(ref), None


def check(condition, features):
    """(ok, leaf records). Groups evaluate every leaf so the record shows all of them."""
    if isinstance(condition, dict):
        (kind, items), = condition.items()
        if kind not in ('any', 'all'):
            raise ValueError(f'unknown group {kind}')
        results = [check(item, features) for item in items]
        oks = [ok for ok, _ in results]
        return (any(oks) if kind == 'any' else all(oks)), [r for _, rs in results for r in rs]
    left, op, right = condition
    if op not in OPS:
        raise ValueError(f'unknown operator {op}')
    a, miss_a = resolve(left, features)
    b, miss_b = resolve(right, features)
    missing = [m for m in (miss_a, miss_b) if m]
    ok = not missing and bool(OPS[op](a, b))
    return ok, [dict(condition=f'{left} {op} {right}', left=a, right=b, ok=ok, missing=missing)]


def evaluate(pb, features, regime):
    """Does the playbook's regime list and every condition hold? Records every leaf and missing input."""
    checks, ok = [], regime in pb['regimes']
    for condition in pb.get('conditions', []):
        passed, records = check(condition, features)
        ok = ok and passed
        checks.extend(records)
    missing = sorted({m for c in checks for m in c['missing']})
    return dict(id=pb['id'], side=pb['side'], ok=ok, regime=regime, regime_ok=regime in pb['regimes'],
                checks=checks, missing=missing)


def levels(pb, features, defaults):
    """Stop from the anchor (min/max distance enforced) and the nearest target candidate, capped by the band."""
    s = SIDES[pb['side']]
    close, atr = _number(features.get('close')), _number(features.get('atr_1h'))
    out = dict(stop=None, target=None, anchor=None, risk=None, reason=None)
    if close is None or atr is None or atr <= 0:
        return out | dict(reason='MISSING_LEVEL_INPUT')
    spec = pb['stop']
    anchor, _ = resolve(spec['anchor'], features)
    if anchor is None:
        return out | dict(reason='MISSING_ANCHOR')
    stop = anchor-s*spec.get('buffer_atr', 0)*atr
    if s*(close-stop) <= 0:
        stop = close-s*defaults['min_stop_atr']*atr     # anchor on the wrong side: the minimum stop
    distance = s*(close-stop)
    if distance < defaults['min_stop_atr']*atr:
        stop, distance = close-s*defaults['min_stop_atr']*atr, defaults['min_stop_atr']*atr
    if distance > defaults['max_stop_atr']*atr:
        return out | dict(anchor=anchor, reason='STOP_TOO_WIDE')
    target_spec = pb['target']
    cap, _ = resolve(target_spec.get('cap'), features) if target_spec.get('cap') else (None, None)
    candidates = [v for v in (resolve(c, features)[0] for c in target_spec.get('candidates', []))
                  if v is not None and s*(v-close) >= defaults['min_target_atr']*atr
                  and (cap is None or s*(v-cap) <= 0)]
    if candidates:
        target = min(candidates, key=lambda v: s*(v-close))
    else:
        target = close+s*target_spec.get('fallback_r', 2.0)*distance
    if cap is not None and s*(target-cap) > 0:
        target = cap
    return dict(stop=stop, target=target, anchor=anchor, risk=distance, reason=None)


def confluence(features, side):
    """Owner's Tab 3: one +1/0/-1 vote per factor toward `side` (volume counts for either side)."""
    s = SIDES[side]
    get = lambda k: _number(features.get(k))
    votes = {}
    sign = lambda x: 0 if x is None or x == 0 else (1 if x > 0 else -1)
    stack = get('ema_stack_1h')
    votes['ema_stack_1h'] = s*sign(stack)
    adx = get('adx_1h')
    votes['adx_1h'] = s*sign(stack) if adx is not None and adx >= 25 else 0
    rsi = get('rsi_15m')
    votes['rsi_15m'] = 0 if rsi is None else s*(1 if rsi > 55 else -1 if rsi < 45 else 0)
    volume = get('volume_z_15m')
    votes['volume_z_15m'] = 1 if volume is not None and volume >= 1.5 else 0
    taker = get('taker_share_15m')
    votes['taker_share_15m'] = 0 if taker is None else s*(1 if taker >= .55 else -1 if taker <= .45 else 0)
    votes['vwap_z'] = s*sign(get('vwap_z'))
    state = get('oi_price_state')   # 1 longs opening, 2 shorts covering, 3 shorts opening, 4 longs closing
    toward = {1: {1: 1, 2: 0, 3: -1, 4: -1}, -1: {1: -1, 2: -1, 3: 1, 4: 0}}[s]
    votes['oi_price_state'] = 0 if state is None else toward.get(int(state), 0)
    funding = get('funding_z')
    votes['funding_z'] = 0 if funding is None else s*(1 if funding <= -1.5 else -1 if funding >= 1.5 else 0)
    pos = get('range_pos_4h')
    votes['range_pos_4h'] = 0 if pos is None else s*(1 if pos <= .3 else -1 if pos >= .7 else 0)
    return dict(score=int(sum(votes.values())), factors=votes)


def _features_in(condition):
    if isinstance(condition, dict):
        (_, items), = condition.items()
        return [f for item in items for f in _features_in(item)]
    left, _, right = condition
    return [x for x in (left, right) if isinstance(x, str)]


def referenced(pb):
    names = [f for c in pb.get('conditions', []) for f in _features_in(c)]
    names += [f for c in pb.get('abort', []) for f in _features_in(c)]
    names += [pb['stop']['anchor'], *pb['target'].get('candidates', [])]
    names += [pb['target']['cap']] if pb['target'].get('cap') else []
    return names


def validate(book, columns=None):
    """(playbook or trigger id, feature) pairs that no source provides; [] when the rulebook is consistent."""
    if columns is None:
        from .patterns import columns as pattern_columns
        columns = pattern_columns()
    known = set(columns)
    unknown = []
    def ok(name):
        prefix, _, field = name.partition('.')
        if field:
            return (prefix == 'trigger' and field in TRIGGER_FIELDS) or (prefix == 'position' and field in POSITION_FIELDS)
        return name in known
    for pb in book['playbooks']:
        unknown += [(pb['id'], f) for f in dict.fromkeys(referenced(pb)) if not ok(f)]
        unknown += [(pb['id'], t) for t in pb['trigger']['any'] if t not in book['triggers']]
    for name, spec in book['triggers'].items():
        if 'when' in spec:
            unknown += [(name, f) for f in dict.fromkeys(_features_in(spec['when'])) if f not in known]
    return unknown
