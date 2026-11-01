"""C1 Funding Collector (A10): a price-neutral hedge that earns funding, and its paper ledger."""
import pandas as pd
import pytest

from quantpilot.simulation import carry as ledger
from quantpilot.strategies import carry

T0 = pd.Timestamp('2026-03-02', tz='UTC')


def at(h):
    return (T0+pd.Timedelta(hours=h)).isoformat()


def test_it_opens_on_positive_week_funding_and_closes_on_negative_three_days():
    assert carry.decide([.0001]*20, is_open=False) is None                  # needs 21 settlements
    assert carry.decide([.0001]*21, is_open=False) == 'open'
    assert carry.decide([-.0001]*21, is_open=False) is None
    assert carry.decide([.0003]*12+[-.0001]*9, is_open=True) == 'close'
    assert carry.decide([.0003]*12+[.0001]*9, is_open=True) is None


def test_the_hedge_is_price_neutral_apart_from_costs():
    s = ledger.initial(10_000.0)
    ledger.open_(s, 100_000.0, at(0))
    spent = 10_000.0-ledger.equity(s, 100_000.0)
    assert spent == pytest.approx(.6*10_000*(ledger.SPOT_FEE+ledger.PERP_FEE+2*ledger.SLIPPAGE), rel=.05)
    for price in (120_000.0, 80_000.0):
        assert ledger.equity(s, price) == pytest.approx(10_000.0-spent, abs=1.0)
    ledger.close(s, 100_000.0, at(48), 'FUNDING_TURNED')
    assert s['open'] is False and len(s['trades']) == 1
    t = s['trades'][0]
    assert t['net'] == pytest.approx(s['cash']-10_000.0) and t['reason'] == 'FUNDING_TURNED'
    assert t['plan']['playbook'] == 'C1' and t['entry_time'] == at(0) and t['exit_time'] == at(48)


def test_funding_paid_to_shorts_is_income():
    s = ledger.initial(10_000.0)
    ledger.open_(s, 100_000.0, at(0))
    before = ledger.equity(s, 100_000.0)
    for k in range(9):
        ledger.fund(s, .0001, 100_000.0, at(8*(k+1)))
    assert ledger.equity(s, 100_000.0)-before == pytest.approx(9*.0001*s['perp_qty']*100_000.0)
    assert s['cycle']['funding'] == pytest.approx(9*.0001*s['perp_qty']*100_000.0)


def test_a_big_rise_rebalances_both_legs_back_to_sixty_forty():
    s = ledger.initial(10_000.0)
    ledger.open_(s, 100_000.0, at(0))
    assert ledger.maybe_rebalance(s, 110_000.0, at(1)) is False
    assert ledger.maybe_rebalance(s, 125_000.0, at(2)) is True
    e = ledger.equity(s, 125_000.0)
    assert s['spot_qty']*125_000.0 == pytest.approx(.6*e, rel=.01) and s['perp_qty'] == pytest.approx(s['spot_qty'])
    assert s['margin'] == pytest.approx(.4*e, rel=.02) and s['cycle']['rebalances'] == 1
    assert ledger.equity(s, 140_000.0) == pytest.approx(e, abs=1.0)        # still neutral after the rebalance
