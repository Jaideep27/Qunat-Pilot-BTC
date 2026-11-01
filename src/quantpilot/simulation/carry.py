"""Paper ledger for the C1 hedge (A10): spot long plus an equal perpetual short, priced at one BTC price.

Opening spends 60% of equity on spot and posts 40% as perp margin. Fees: spot 10 bps, perp 5 bps, slippage 3 bps
per leg and side. Funding settles into cash. When the perp margin falls below 40% (or rises above 120%) of the perp
notional, both legs are resized to 60/40 of current equity. A closed open-to-close cycle is one trade, in the same
shape as a futures-v1 trade so the account journal and reviews take it unchanged. State is plain JSON.
"""
import pandas as pd

SPOT_FEE, PERP_FEE, SLIPPAGE = .0010, .0005, .0003
HEDGE, MARGIN = .60, .40
LOW, HIGH = .40, 1.20
VERSION = 'carry-v1'


def initial(balance=10_000.0):
    return dict(version=VERSION, balance=float(balance), cash=float(balance), equity=float(balance), open=False,
                spot_qty=0.0, perp_qty=0.0, perp_entry=None, margin=0.0, cycle=None, trades=[], funding=[],
                last_price=None, last_time=None, paused=False, halted_day=False, floor_hit=False, position=None,
                pending=None, decisions=[], rejections=[])


def _perp_pnl(s, price):
    return s['perp_qty']*(s['perp_entry']-price) if s['perp_qty'] else 0.0


def equity(s, price):
    return s['cash']+s['spot_qty']*price+s['margin']+_perp_pnl(s, price)


def _trade_legs(s, spot_delta, perp_delta, price):
    """Buy (+) or sell (-) spot and short more (+) or cover (-) perp at `price`; returns the fees paid."""
    fees = abs(spot_delta)*price*(SPOT_FEE+SLIPPAGE)+abs(perp_delta)*price*(PERP_FEE+SLIPPAGE)
    s['cash'] -= spot_delta*price+fees
    s['spot_qty'] += spot_delta
    s['perp_qty'] += perp_delta
    return fees


def _settle_perp(s, price):
    """Realise the perp's open result into its margin and re-mark its entry at `price`."""
    s['margin'] += _perp_pnl(s, price)
    s['perp_entry'] = price


def _resize(s, price):
    """Both legs to 60% of equity, margin to 40%."""
    if s['perp_qty']:
        _settle_perp(s, price)
    target = HEDGE*equity(s, price)/price
    fees = _trade_legs(s, target-s['spot_qty'], target-s['perp_qty'], price)
    s['perp_entry'] = price
    s['cash'] += s['margin']
    s['margin'] = MARGIN/HEDGE*target*price
    s['cash'] -= s['margin']
    return fees


def open_(s, price, time):
    s['cycle'] = dict(entry_time=pd.Timestamp(time).isoformat(), entry=price, equity_at_entry=equity(s, price), fees=0.0,
                      funding=0.0, rebalances=0)
    s['cycle']['fees'] += _resize(s, price)
    s['open'] = True
    s.update(equity=equity(s, price), last_price=price, last_time=pd.Timestamp(time).isoformat())
    s['position'] = dict(kind='hedge', entry_time=s['cycle']['entry_time'], quantity=s['spot_qty'], entry=price)


def close(s, price, time, reason):
    _settle_perp(s, price)
    fees = _trade_legs(s, -s['spot_qty'], -s['perp_qty'], price)
    s['cash'] += s['margin']
    s['margin'], s['perp_entry'] = 0.0, None
    c = s['cycle']
    c['fees'] += fees
    net = s['cash']-c['equity_at_entry']
    s['trades'].append(dict(side=1, quantity=0.0, entry=c['entry'], exit=price, entry_time=c['entry_time'],
                            exit_time=pd.Timestamp(time).isoformat(), reason=reason, pnl=net-c['funding']+c['fees'],
                            fees=c['fees'], funding=c['funding'], net=net, return_on_equity=net/c['equity_at_entry'],
                            rebalances=c['rebalances'], max_favorable=0.0, max_adverse=0.0, initial_stop=None,
                            initial_target=None, protection_changes=[], version=VERSION,
                            plan=dict(side='hedge', playbook='C1', style='C1', hit_id=f"C1:{c['entry_time']}")))
    s['open'], s['cycle'], s['position'] = False, None, None
    s.update(equity=s['cash'], last_price=price, last_time=pd.Timestamp(time).isoformat())


def fund(s, rate, price, time):
    if not s['open']:
        return 0.0
    amount = float(rate)*s['perp_qty']*price          # positive funding: longs pay shorts
    s['cash'] += amount
    s['cycle']['funding'] += amount
    s['funding'].append(dict(time=pd.Timestamp(time).isoformat(), rate=float(rate), amount=amount))
    s['funding'] = s['funding'][-500:]
    s['equity'] = equity(s, price)
    return amount


def margin_ratio(s, price):
    notional = s['perp_qty']*price
    return (s['margin']+_perp_pnl(s, price))/notional if notional else None


def maybe_rebalance(s, price, time):
    ratio = margin_ratio(s, price)
    if not s['open'] or ratio is None or LOW <= ratio <= HIGH:
        return False
    s['cycle']['fees'] += _resize(s, price)
    s['cycle']['rebalances'] += 1
    s['equity'] = equity(s, price)
    return True


def mark(s, price, time):
    s.update(equity=equity(s, price), last_price=price, last_time=pd.Timestamp(time).isoformat())
