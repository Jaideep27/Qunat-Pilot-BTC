"""Deterministic trade plans from a MarketContext: structural stop and target, net reward:risk, size.

These are registered starting rules (docs/design/specs/2026-10-20-trading-agent-step-a-design.md),
not a claim of edge. A plan never decides whether to trade; a policy does. The 24h range bounds targets
but is never presented as a probability of reaching them.
"""
from __future__ import annotations

from datetime import datetime
import math

import pandas as pd
from pydantic import BaseModel, ConfigDict

from quantpilot.config import RiskConfig

_RISK = RiskConfig()
ROUND_TRIP = 2*(_RISK.fee_bps+_RISK.slippage_bps)/1e4
PLAN_RULES = dict(version=1, structure_timeframes=('1h', '4h'), stop_buffer_atr=.25, min_stop_atr=1.5, max_stop_atr=4.0,
                  min_reward_risk=1.5, funding_settlements=3, risk_fraction=_RISK.risk_budget, max_exposure=1.0,
                  leverage=2.0, maintenance=_RISK.maintenance_margin, qty_step=.001, min_qty=.001, min_notional=100.0,
                  range_z=1.2816, forecast_max_age_minutes=30)
SIDES = {'long': 1, 'short': -1}


class TradePlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    rules_version: int
    context_cutoff: datetime
    expires_at: datetime
    side: str
    entry: float
    stop: float | None
    target: float | None
    quantity: float
    notional: float
    margin: float
    leverage: float
    liquidation: float
    risk_amount: float
    reward_risk: float | None
    cost_fraction: float
    range: dict
    evidence: list[dict]
    rejections: list[str]

    @property
    def valid(self):
        return not self.rejections


def _levels(ctx, kind, rules):
    found = [ctx.levels[tf][kind] for tf in rules['structure_timeframes']]
    found += [ctx.levels['prior_day']['low' if kind == 'support' else 'high'],
              ctx.levels['prior_week']['low' if kind == 'support' else 'high']]
    return [x for x in found if x is not None]


def day_range(ctx, rules=PLAN_RULES):
    """24h 80% range: a fresh shadow forecast when one exists, otherwise volatility-scaled around the price."""
    forecast = ctx.forecasts.get('24h') or {}
    fresh = forecast.get('age_minutes') is not None and forecast['age_minutes'] <= rules['forecast_max_age_minutes']
    if fresh and forecast.get('return_q10') is not None and forecast.get('return_q90') is not None:
        return dict(low=ctx.price*(1+forecast['return_q10']), high=ctx.price*(1+forecast['return_q90']),
                    source='shadow_forecast')
    if ctx.volatility_24h:
        z = rules['range_z']*ctx.volatility_24h
        return dict(low=ctx.price*math.exp(-z), high=ctx.price*math.exp(z), source='volatility')
    return dict(low=None, high=None, source='unavailable')


def costs(side, funding_rate, rules=PLAN_RULES):
    """Round-trip fee and slippage plus funding the side would pay over the expected settlements."""
    paid = max(0.0, SIDES[side]*(funding_rate or 0.0))*rules['funding_settlements']
    return ROUND_TRIP+paid


def reward_risk(side, entry, stop, target, cost):
    s = SIDES[side]
    if stop is None or target is None or not (s*(target-entry) > 0 > s*(stop-entry)):
        return None
    return (s*(target-entry)/entry-cost)/(s*(entry-stop)/entry+cost)


def _evidence(ctx, side):
    s, rows = SIDES[side], []
    def add(factor, value, stance, note):
        rows.append(dict(factor=factor, value=value, stance=stance, note=note))
    for tf in ('1h', '4h'):
        trend = ctx.timeframes[tf].get('trend')
        if trend:
            add(f'trend_{tf}', trend, 'supports' if trend == s else 'opposes', f'{tf} close vs EMA50')
    rsi = ctx.timeframes['1h'].get('rsi')
    if rsi is not None:
        extreme = 1 if rsi > 70 else -1 if rsi < 30 else 0
        add('rsi_1h', rsi, 'neutral' if not extreme else ('opposes' if extreme == s else 'supports'),
            'overbought' if extreme > 0 else 'oversold' if extreme < 0 else 'mid-range')
    if ctx.funding_rate is not None:
        crowded = 1 if ctx.funding_rate > .0003 else -1 if ctx.funding_rate < -.0001 else 0
        add('funding', ctx.funding_rate, 'opposes' if crowded == s else 'neutral',
            'crowded longs pay' if crowded > 0 else 'crowded shorts pay' if crowded < 0 else 'normal')
    if ctx.global_ratio is not None:
        crowded = 1 if ctx.global_ratio > 2 else -1 if ctx.global_ratio < .8 else 0
        add('long_short_ratio', ctx.global_ratio, 'opposes' if crowded == s else 'neutral', 'global accounts')
    if ctx.taker_share_1h is not None:
        flow = 1 if ctx.taker_share_1h > .55 else -1 if ctx.taker_share_1h < .45 else 0
        add('taker_flow_1h', ctx.taker_share_1h, 'neutral' if not flow else ('supports' if flow == s else 'opposes'),
            'aggressive buying' if flow > 0 else 'aggressive selling' if flow < 0 else 'balanced')
    for horizon in ('4h', '24h'):
        f = ctx.forecasts.get(horizon)
        if f and f.get('probability_up') is not None and f.get('base_rate') is not None:
            if f.get('status') == 'no_validated_skill':
                add(f'forecast_{horizon}', f['probability_up'], 'neutral', 'no validated direction skill')
            else:
                edge = s*(f['probability_up']-f['base_rate'])
                add(f'forecast_{horizon}', f['probability_up'], 'supports' if edge > .02 else
                    'opposes' if edge < -.02 else 'neutral', 'validated forecast vs base rate')
    upcoming = (ctx.macro or {}).get('next')
    if upcoming and upcoming.get('hours') is not None and upcoming['hours'] <= 24:
        add('macro', upcoming['hours'], 'neutral', f"{upcoming.get('label')} in {upcoming['hours']}h")
    if (ctx.news or {}).get('count_24h'):
        add('news', ctx.news['count_24h'], 'neutral', 'news items in the last 24h (context only)')
    return rows


def plan(ctx, side, account, rules=PLAN_RULES):
    """A complete plan for `side` ('long'/'short'); `rejections` lists every rule it fails."""
    s, entry, rejections = SIDES[side], ctx.price, []
    atr = ctx.timeframes['1h'].get('atr')
    if ctx.blocking or not atr:
        rejections.append('MISSING_CRITICAL_INPUT')
    if (ctx.macro or {}).get('blackout'):
        rejections.append('MACRO_BLACKOUT')
    if account.get('position'):
        rejections.append('POSITION_OPEN')
    if account.get('halted') or account.get('paused'):
        rejections.append('ACCOUNT_HALTED')
    atr = atr or 0.0
    protective = [x for x in _levels(ctx, 'support' if s == 1 else 'resistance', rules) if s*(entry-x) > 0]
    opposing = [x for x in _levels(ctx, 'resistance' if s == 1 else 'support', rules) if s*(x-entry) > 0]
    stop = None
    if atr:
        nearest = max(protective) if s == 1 and protective else min(protective) if protective else None
        stop = entry-s*rules['min_stop_atr']*atr if nearest is None else nearest-s*rules['stop_buffer_atr']*atr
        if s*(entry-stop) < rules['min_stop_atr']*atr:
            stop = entry-s*rules['min_stop_atr']*atr
        if s*(entry-stop) > rules['max_stop_atr']*atr:
            rejections.append('STOP_TOO_WIDE')
    window = day_range(ctx, rules)
    boundary = window['high'] if s == 1 else window['low']
    target = (min(opposing) if s == 1 else max(opposing)) if opposing else boundary
    if target is not None and boundary is not None and s*(target-boundary) > 1e-9*entry:
        rejections.append('TARGET_OUTSIDE_RANGE')
    sized = size(side, entry, stop, target, account, ctx.funding_rate, rules)
    cutoff = pd.Timestamp(ctx.cutoff)
    return TradePlan(
        rules_version=rules['version'], context_cutoff=cutoff.to_pydatetime(),
        expires_at=(cutoff+pd.Timedelta(minutes=5)).to_pydatetime(), side=side, entry=entry, stop=stop,
        target=target, quantity=sized['quantity'], notional=sized['notional'], margin=sized['margin'],
        leverage=sized['leverage'], liquidation=sized['liquidation'], risk_amount=sized['risk_amount'],
        reward_risk=sized['reward_risk'], cost_fraction=sized['cost_fraction'], range=window,
        evidence=_evidence(ctx, side), rejections=list(dict.fromkeys(rejections+sized['rejections'])))


def size(side, entry, stop, target, account, funding_rate, rules=PLAN_RULES):
    """Net reward:risk, risk-budget sizing, exchange minimums, margin and liquidation for given levels."""
    s, rejections = SIDES[side], []
    cost = costs(side, funding_rate, rules)
    ratio = reward_risk(side, entry, stop, target, cost)
    if ratio is None:
        rejections.append('INVALID_LEVELS')
    elif ratio < rules['min_reward_risk']:
        rejections.append('REWARD_RISK_BELOW_MIN')
    quantity = 0.0
    equity, step = float(account.get('equity') or 0.0), rules['qty_step']
    if stop is not None and equity > 0:
        per_unit = s*(entry-stop)+entry*cost
        if per_unit > 0:
            sized = equity*rules['risk_fraction']/per_unit
            capped = min(sized, equity*rules['max_exposure']/entry)
            quantity = math.floor(capped/step+1e-9)*step
    quantity = round(quantity, 6)
    notional = quantity*entry
    margin = notional/rules['leverage']
    if quantity < rules['min_qty'] or notional < rules['min_notional']-1e-9:
        rejections.append('BELOW_EXCHANGE_MINIMUM')
    if margin > float(account.get('cash', equity))+1e-9:
        rejections.append('INSUFFICIENT_MARGIN')
    leverage, maintenance = rules['leverage'], rules['maintenance']
    liquidation = entry*(1-1/leverage)/(1-maintenance) if s == 1 else entry*(1+1/leverage)/(1+maintenance)
    if stop is not None and s*(stop-liquidation) <= 0:
        rejections.append('STOP_BEYOND_LIQUIDATION')
    return dict(quantity=quantity, notional=notional, margin=margin, leverage=leverage, liquidation=liquidation,
                risk_amount=quantity*(s*(entry-stop)+entry*cost) if stop is not None else 0.0, reward_risk=ratio,
                cost_fraction=cost, rejections=rejections)


def revalidate(plan, fill_price, rules=PLAN_RULES):
    """Re-apply level ordering and the reward:risk minimum at the actual fill price."""
    ratio = reward_risk(plan.side, fill_price, plan.stop, plan.target, plan.cost_fraction)
    return [] if ratio is not None and ratio >= rules['min_reward_risk'] else ['INVALIDATED_AT_FILL']
