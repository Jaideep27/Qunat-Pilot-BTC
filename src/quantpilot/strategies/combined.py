"""X1 Combined Bot (A11): one account, one trade; every strategy is a member and a vote.

The market type (the rule engine's regime) picks which members may lead, the Trend Rider's daily vote vetoes
counter-trend trades, and a leader is taken only with a second opinion: another member from a different source
on the same side within the last hour, or price at a confirmed support (long) or resistance (short).
Idle money sits in the Funding Collector's hedge.
"""
import pandas as pd

from quantpilot.strategies import carry

CODE, NAME = 'X1', 'Combined Bot'
TREND = ('T1', 'PB02', 'PB04', 'PB07', 'PB08', 'PB12', 'PB13', 'B1')
LEADERS = dict(TREND_UP=TREND, TREND_DOWN=TREND, RANGE=('R1', 'PB03', 'PB09', 'PB10', 'PB11', 'B1'),
               HIGH_VOL=('PB01', 'PB05', 'PB06'), PANIC=('PB01', 'PB05', 'PB06'))
WINDOW = pd.Timedelta(hours=1)           # this close and the 4 closes before
LEVEL_ATR = .5
PARK_AFTER = pd.Timedelta(hours=24)
RULES = [
    'One account, one trade at a time. Every strategy is a member: Trend Rider, Range Trader, Breakout Hunter and the 20 rulebook setups.',
    'The market type decides who may lead: trend signals in a trend, range signals when sideways, crowd setups when it is wild.',
    'The daily trend has a veto: no shorts while BTC trends up, no longs while it trends down.',
    'A leader needs a second opinion: another strategy agreeing within the hour, or price sitting at support (to buy) or resistance (to sell).',
    "It uses the winning signal's own stop, target and time limit, and risks 1% of the account.",
    'When it has had no trade for a day and funding is positive, idle money earns funding in the hedge until the next trade.',
]


def family(source):
    return source[:4] if source.startswith('PB') else source


def leads(regime, source):
    return family(source) in LEADERS.get(regime, ())


def vetoed(side, vote):
    return (side == 'short' and vote == 1) or (side == 'long' and vote == -1)


def at_level(side, row):
    """Names of the confirmed levels within 0.5 x ATR(1h) on the trade's side."""
    close, atr = row.get('close'), row.get('atr_1h')
    if close is None or atr is None or pd.isna(close) or pd.isna(atr) or atr <= 0:
        return []
    out = []
    for tf in ('1h', '4h'):
        level = row.get(f'support_{tf}' if side == 'long' else f'resistance_{tf}')
        if level is None or pd.isna(level):
            continue
        gap = close-level if side == 'long' else level-close
        if 0 <= gap <= LEVEL_ATR*atr:
            out.append(f"{'support' if side == 'long' else 'resistance'}_{tf}")
    return out


def decide(regime, vote, current, recent, row):
    """The chosen candidate (with its agreements) and every candidate's status at this close."""
    order = LEADERS.get(regime, ())
    t = max((pd.Timestamp(c['time']) for c in current), default=None)
    considered, eligible = [], []
    for c in current:
        rec = dict(c, agreements=[])
        if not leads(regime, c['source']):
            rec['status'] = 'NOT_LEADER'
        elif vetoed(c['side'], vote):
            rec['status'] = 'VETOED'
        else:
            others = [o for o in list(current)+list(recent) if o is not c and o['side'] == c['side']
                      and family(o['source']) != family(c['source'])
                      and (t is None or t-WINDOW <= pd.Timestamp(o['time']) <= t)]
            rec['agreements'] = sorted({o['source'] for o in others})+at_level(c['side'], row)
            rec['status'] = 'NO_SECOND_OPINION' if not rec['agreements'] else 'CANDIDATE'
            if rec['agreements']:
                eligible.append(rec)
        considered.append(rec)
    eligible.sort(key=lambda r: (-len(r['agreements']), order.index(family(r['source'])), -(r.get('reward_risk') or 0)))
    chosen = eligible[0] if eligible else None
    for rec in considered:
        if rec['status'] == 'CANDIDATE':
            rec['status'] = 'CHOSEN' if rec is chosen else 'LOWER_RANK'
    return dict(chosen=chosen, considered=considered, regime=regime, vote=vote, leaders=list(order))


def park(flat_since, now, rates):
    """Open the idle hedge: flat for a day and funding positive over 7 days."""
    return flat_since is not None and pd.Timestamp(now)-pd.Timestamp(flat_since) >= PARK_AFTER \
        and carry.decide(rates, is_open=False) == 'open'


def unpark(rates):
    return carry.decide(rates, is_open=True) == 'close'


def active(regime, vote):
    label = {'TREND_UP': 'Trend up', 'TREND_DOWN': 'Trend down', 'RANGE': 'Sideways', 'HIGH_VOL': 'Wild market',
             'PANIC': 'Panic'}.get(regime)
    if label is None:
        return False, 'Off: warming up.'
    who = {'TREND_UP': 'follows trend signals', 'TREND_DOWN': 'follows trend signals',
           'RANGE': 'follows range signals (sideways market)', 'HIGH_VOL': 'only crowd-flush setups may lead',
           'PANIC': 'only crowd-flush setups may lead'}[regime]
    veto = {1: '; shorts are vetoed by the daily uptrend', -1: '; longs are vetoed by the daily downtrend'}.get(vote, '')
    return True, f'{label}: {who}{veto}. Every trade needs a second opinion.'
