"""A8 account config (`rulebook/accounts-v1.yaml`): eligibility, sizing, choice, plain-English rules, watch items.

Sizing is fixed-fractional: the account risks `risk` of equity (half below its full-risk line) between entry
and stop plus costs, the notional is capped at equity x leverage, and the stop must sit inside the liquidation
price. Nothing here changes the config; a change is a new file version.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import yaml

from quantpilot.simulation import tradeplan
from quantpilot.simulation.futures import liquidation_price

CONFIG = Path(__file__).resolve().parents[3]/'rulebook'/'accounts-v1.yaml'
PERCENT = lambda x: f'{100*x:g}%'


def load(path=CONFIG):
    text = Path(path).read_text(encoding='utf-8')
    cfg = yaml.safe_load(text)
    cfg['hash'] = hashlib.sha256(text.replace('\r\n', '\n').encode()).hexdigest()[:12]
    return cfg


def eligible(account, score):
    return score is not None and score >= account['cutoff_score']


def full_risk(account, score):
    return score is not None and score >= account['full_risk_score']


def size(account, plan, score, equity, cash, funding_rate, rules=tradeplan.PLAN_RULES):
    """Quantity, notional, margin and liquidation for one plan in one account (with rejections)."""
    side, entry, stop = plan['side'], float(plan['entry']), float(plan['stop'])
    s = 1 if side == 'long' else -1
    cost = tradeplan.costs(side, funding_rate, rules)
    risk = account['risk'] if full_risk(account, score) else account['risk']/2
    leverage = float(account['leverage'])
    per_unit = s*(entry-stop)+entry*cost
    rejections, quantity = [], 0.0
    if s*(entry-stop) <= 0:
        rejections.append('INVALID_LEVELS')
    elif equity > 0:
        wanted = equity*risk/per_unit
        capped = min(wanted, equity*leverage/entry)
        quantity = round(math.floor(capped/rules['qty_step']+1e-9)*rules['qty_step'], 6)
    notional = quantity*entry
    margin = notional/leverage
    liquidation = liquidation_price(s, entry, leverage)
    if quantity < rules['min_qty'] or notional < rules['min_notional']-1e-9:
        rejections.append('BELOW_EXCHANGE_MINIMUM')
    if margin > cash+1e-9:
        rejections.append('INSUFFICIENT_MARGIN')
    if s*(stop-liquidation) <= 0:
        rejections.append('STOP_BEYOND_LIQUIDATION')
    risk_amount = quantity*per_unit if quantity else 0.0
    return dict(quantity=quantity, notional=notional, margin=margin, leverage=leverage, liquidation=liquidation,
                cost_fraction=cost, risk_fraction=risk, risk_amount=risk_amount,
                risk_fraction_used=risk_amount/equity if equity else 0.0, rejections=list(dict.fromkeys(rejections)))


def choose(candidates):
    return max(candidates, key=lambda c: c['score']) if candidates else None


def describe(account, round_length=30):
    top = 100-account['percentile']
    rules = [
        f"Takes a confirmed rulebook setup only when its score is at least {account['cutoff_score']:+.2f} R: "
        f"the top {top:g}% of setups over 2021–2025.",
        f"Risks {PERCENT(account['risk'])} of equity per trade, or {PERCENT(account['risk']/2)} when the score is below "
        f"{account['full_risk_score']:+.2f} R (the top {100-account['full_risk_percentile']:g}% get full risk).",
        f"Leverage {account['leverage']:g}× at most; the stop must sit inside the liquidation price.",
        'One trade at a time, never flips, and the stop and target are set before entry.',
        f"Stops for the day after losing {PERCENT(account['daily_brake'])}; stops early for review below $5,000.",
        f'Pauses after {round_length} trades for a review with the owner.',
    ]
    watches = [
        'Price action: 15m, 1h and 4h moves, volume, trend and range position (the rulebook checklist).',
        'Long/short ratios (all traders and top traders), funding rate and open interest.',
        'Market buying vs selling (taker share) and Fear & Greed.',
        'Order-book imbalance and spread (recorded, not scored).',
        'Liquidations in the last 15 minutes (recorded, not scored).',
        'Latest classified news and the next US macro release (recorded; releases also block entries).',
    ]
    return dict(rules=rules, watches=watches)


def _num(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def watch_items(side, features, news_latest, macro_next_minutes):
    """Live-only facts recorded with each decision: value, whether it agrees with the side (None = no data)."""
    s = 1 if side == 'long' else -1
    imbalance, spread = _num(features.get('imbalance_15bps')), _num(features.get('spread_bps'))
    against = _num(features.get('liq_long_usd_15m' if s == 1 else 'liq_short_usd_15m'))
    opposed = 'bearish' if s == 1 else 'bullish'
    news_ok = None if not news_latest else not (news_latest.get('direction') == opposed and news_latest.get('impact') in ('high', 'medium'))
    return [
        dict(name='book_imbalance', value=imbalance, ok=None if imbalance is None else s*imbalance > 0,
             note='buy minus sell orders within 0.15% of the price'),
        dict(name='spread', value=spread, ok=None if spread is None else spread < 1, note='bid-ask spread in bps'),
        dict(name='liquidations_against', value=against, ok=None if against is None else against < 1_000_000,
             note=f"USD of {side} positions liquidated in the last 15 minutes"),
        dict(name='news_latest', value=news_latest and dict(title=news_latest.get('title'), direction=news_latest.get('direction'),
                                                            impact=news_latest.get('impact')),
             ok=news_ok, note='latest classified headline does not oppose the trade'),
        dict(name='macro_next', value=macro_next_minutes, ok=macro_next_minutes is None or macro_next_minutes > 120,
             note='minutes to the next CPI, jobs or FOMC release'),
    ]
