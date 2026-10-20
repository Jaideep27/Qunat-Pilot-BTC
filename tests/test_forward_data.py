"""Forward-only research data: per-minute liquidations and order-book depth snapshots."""
import json

import pandas as pd
import pytest
from sqlalchemy import select

from quantpilot import forward_data as fd
from quantpilot.market_store import MarketObservation


def event(side, qty, price, ms):
    return json.dumps(dict(e='forceOrder', E=ms, o=dict(s='BTCUSDT', S=side, q=str(qty), ap=str(price),
                                                      z=str(qty), X='FILLED', T=ms)))


T0 = int(pd.Timestamp('2026-10-20 12:00', tz='UTC').timestamp()*1000)


def test_liquidations_aggregate_per_minute_by_side():
    sells = [fd.parse_liquidation(event('SELL', .5, 100000, T0+1000)),
             fd.parse_liquidation(event('SELL', .25, 99000, T0+2000)),
             fd.parse_liquidation(event('BUY', 1, 101000, T0+61_000))]
    frame = fd.aggregate_liquidations(sells, received_at=pd.Timestamp('2026-10-20 12:05', tz='UTC'))
    first, second = frame.iloc[0], frame.iloc[1]
    assert first.event_time == pd.Timestamp('2026-10-20 12:00', tz='UTC')
    assert first.available_at == pd.Timestamp('2026-10-20 12:01', tz='UTC')
    assert first.long_liq_usd == pytest.approx(.5*100000+.25*99000) and first.short_liq_usd == 0
    assert first.liq_count == 2
    assert second.short_liq_usd == pytest.approx(101000) and second.long_liq_qty == 0


def test_non_btc_or_malformed_messages_are_ignored():
    assert fd.parse_liquidation('{"e":"forceOrder","o":{"s":"ETHUSDT"}}') is None
    assert fd.parse_liquidation('not json') is None


def test_depth_snapshot_features_measure_size_near_the_mid():
    book = dict(bids=[['10000.0', '2'], ['9996.0', '3'], ['9980.0', '9']],
                asks=[['10000.2', '1'], ['10004.0', '1'], ['10020.0', '4']], T=T0)
    row = fd.depth_features(book, received_at=pd.Timestamp('2026-10-20 12:00:01', tz='UTC'))
    assert row['bid_qty_5bps'] == 5 and row['ask_qty_5bps'] == 2          # within 0.05% of the mid
    assert row['bid_qty_15bps'] == 5 and row['ask_qty_15bps'] == 2
    assert row['imbalance_5bps'] == pytest.approx((5-2)/7)
    assert row['spread_bps'] == pytest.approx(.2/10000.1*1e4)
    assert row['event_time'] == pd.Timestamp('2026-10-20 12:00', tz='UTC')


def test_forward_rows_are_stored_as_market_observations(account):
    rows = fd.aggregate_liquidations([fd.parse_liquidation(event('SELL', 1, 100000, T0))],
                                     received_at=pd.Timestamp('2026-10-20 12:02', tz='UTC'))
    assert fd.save(account, 'liquidations', rows) == 1
    with account.connect() as connection:
        stored = connection.execute(select(MarketObservation.family, MarketObservation.values)).all()
    assert stored[0][0] == 'liquidations' and stored[0][1]['long_liq_usd'] == 100000
