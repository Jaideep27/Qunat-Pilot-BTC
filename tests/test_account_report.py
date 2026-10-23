"""Report card for each paper account: the standard measures and a comparison with simply holding BTC."""
import pandas as pd
import pytest
from sqlalchemy.orm import Session

from quantpilot import account_report
from quantpilot.market_store import PERP, MarketCandle

T = pd.Timestamp('2026-10-22 00:00', tz='UTC')


def trades(nets):
    return [dict(net=n, entry_time=(T+pd.Timedelta(hours=10*i)).isoformat(),
                 exit_time=(T+pd.Timedelta(hours=10*i+2)).isoformat()) for i, n in enumerate(nets)]


def test_report_card_numbers():
    card = account_report.report(trades([100, -50, -50, 200, -100]), 10_000.0, 10_100.0)
    assert card['trades'] == 5 and card['wins'] == 2 and card['win_rate'] == pytest.approx(.4)
    assert card['profit_factor'] == pytest.approx(1.5) and card['avg_win'] == pytest.approx(150)
    assert card['avg_loss'] == pytest.approx(-200/3) and card['payoff'] == pytest.approx(2.25)
    assert card['account_return'] == pytest.approx(.01) and card['expectancy'] == pytest.approx(20)
    assert card['max_drawdown'] == pytest.approx(10_000/10_100-1)
    assert card['longest_drawdown_hours'] == pytest.approx(30)      # from the peak after trade 1 to trade 4's exit
    assert card['longest_losing_streak'] == 2


def test_an_account_without_trades_has_a_safe_empty_card():
    card = account_report.report([], 10_000.0, 10_000.0)
    assert card['trades'] == 0 and card['win_rate'] is None and card['profit_factor'] is None
    assert card['max_drawdown'] == 0 and card['longest_drawdown_hours'] == 0


def test_no_losses_means_no_profit_factor_rather_than_infinity():
    assert account_report.report(trades([10, 20]), 10_000.0, 10_030.0)['profit_factor'] is None


def test_the_card_compares_with_holding_btc():
    card = account_report.report(trades([100]), 10_000.0, 10_100.0, hold=dict(start_price=80_000.0, end_price=84_000.0))
    assert card['hold_return'] == pytest.approx(.05) and card['vs_hold'] == pytest.approx(.01-.05)


def test_hold_reads_the_first_and_last_closes_from_stored_candles(account):
    with Session(account) as session, session.begin():
        for i, close in enumerate([80_000.0, 81_000.0, 79_000.0, 84_000.0]):
            t = T+pd.Timedelta(minutes=5*i)
            session.add(MarketCandle(market=PERP, interval='5m', event_time=t.to_pydatetime(),
                                     available_at=(t+pd.Timedelta(minutes=5)).to_pydatetime(),
                                     received_at=t.to_pydatetime(), source='test', open=close, high=close, low=close,
                                     close=close, volume=1.0, trade_count=1.0, taker_buy_volume=.5, complete=True))
    hold = account_report.hold(account, T+pd.Timedelta(minutes=3), T+pd.Timedelta(minutes=30))
    assert hold['start_price'] == 81_000.0 and hold['end_price'] == 84_000.0
    assert hold['return'] == pytest.approx(84/81-1)
    assert account_report.hold(account, T+pd.Timedelta(days=5)) is None
