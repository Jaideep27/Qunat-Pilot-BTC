"""What a style asks for (an Intent) and how every account turns it into a futures-v1 plan (A10 shared rules)."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quantpilot.simulation import futures

RISK, LEVERAGE = .01, 2.0
MACRO_BLACKOUT = (pd.Timedelta(minutes=30), pd.Timedelta(minutes=15))


@dataclass
class Intent:
    style: str
    side: str                    # 'long' or 'short'
    time: pd.Timestamp           # the decision (bar close) it was made at
    ref: float                   # the decision close, or the limit price for resting entries
    stop: float
    target: float | None
    max_hold_hours: float
    reason: str
    risk_scale: float = 1.0
    limit: float | None = None
    valid_minutes: int | None = None

    @property
    def id(self):
        return f'{self.style}:{pd.Timestamp(self.time).isoformat()}'


def quantity(intent, equity, risk=RISK, leverage=LEVERAGE):
    """1% of equity lost at the stop (times the style's risk scale), never more than `leverage` x equity in notional."""
    per_unit = abs(intent.ref-intent.stop)
    if per_unit <= 0 or equity <= 0:
        return 0.0
    return min(risk*intent.risk_scale*equity/per_unit, leverage*equity/intent.ref)


def plan(intent, equity, hit_id=None, risk=RISK, leverage=LEVERAGE):
    p = dict(side=intent.side, quantity=quantity(intent, equity, risk, leverage), stop=float(intent.stop),
             target=None if intent.target is None else float(intent.target), leverage=leverage,
             cost_fraction=futures.ROUND_TRIP, min_reward_risk=None, max_hold_hours=intent.max_hold_hours,
             style=intent.style, playbook=intent.style, hit_id=hit_id or intent.id, context_cutoff=pd.Timestamp(intent.time).isoformat())
    if intent.limit is not None:
        p.update(limit=float(intent.limit), expires=(pd.Timestamp(intent.time)+pd.Timedelta(minutes=intent.valid_minutes)).isoformat())
    return p


def in_blackout(t, macro_events):
    """Entries wait from 30 minutes before to 15 minutes after a scheduled macro release."""
    t = pd.Timestamp(t)
    for e in macro_events or []:
        at = e.get('scheduled_at')
        if at is not None and pd.Timestamp(at)-MACRO_BLACKOUT[0] <= t <= pd.Timestamp(at)+MACRO_BLACKOUT[1]:
            return True
    return False


def rows(frame, side, stop, target, max_hold, reason, limit=None, valid=None, scale=None):
    """Signals frame: one row per decision time that fires."""
    out = pd.DataFrame(dict(side=side, stop=stop, target=target, max_hold_hours=max_hold, reason=reason), index=frame.index)
    out['ref'] = frame.close if limit is None else limit
    out['limit'] = limit
    out['valid_minutes'] = valid
    out['risk_scale'] = 1.0 if scale is None else scale
    return out


def to_intent(style, t, row):
    get = lambda k: None if pd.isna(row.get(k)) else row.get(k)
    return Intent(style=style, side=row['side'], time=pd.Timestamp(t), ref=float(row['ref']), stop=float(row['stop']),
                  target=get('target'), max_hold_hours=float(row['max_hold_hours']), reason=row['reason'],
                  risk_scale=float(row.get('risk_scale', 1.0)), limit=get('limit'),
                  valid_minutes=None if get('valid_minutes') is None else int(row['valid_minutes']))
