"""Research data: archive/REST parsers, incremental caches and the daily/hourly research panels."""
import io
import json
import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest

from quantpilot import research_data as rd


def zipped(text, name='x.csv'):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


def test_kline_archives_accept_millisecond_and_microsecond_timestamps():
    ms = '1503100800000,4000,4186,3960,4157,10,1503187199999,0,1,0,0,0\n'
    us = 'open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,ignore\n' \
         '1735689600000000,93576,95151,92888,94591,10373,1735775999999999,0,1,0,0,0\n'
    a, b = rd.parse_klines(zipped(ms), '1d'), rd.parse_klines(zipped(us), '1d')
    assert a.index[0] == pd.Timestamp('2017-08-20', tz='UTC')       # indexed by close time
    assert b.index[0] == pd.Timestamp('2025-01-02', tz='UTC') and b.close.iloc[0] == 94591


def test_coinbase_pages_are_ordered_and_deduplicated():
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        start = pd.Timestamp(request.url.params['start'])
        rows = [[int((start+pd.Timedelta(days=d)).timestamp()), 1, 3, 2, 2.5+d, 9] for d in (1, 0)]
        return httpx.Response(200, json=rows)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = rd.coinbase_candles(client, 86400, pd.Timestamp('2024-01-01', tz='UTC'),
                                pd.Timestamp('2024-01-03', tz='UTC'), page=2)
    assert list(frame.close) == [2.5, 3.5] and frame.index[0] == pd.Timestamp('2024-01-02', tz='UTC')
    assert frame.index.is_monotonic_increasing and frame.index.is_unique


def test_deribit_dvol_follows_continuation():
    def handler(request):
        end = int(request.url.params['end_timestamp'])
        if end > 2_000:
            return httpx.Response(200, json=dict(result=dict(data=[[2_000, 1, 1, 1, 50.0], [3_000, 1, 1, 1, 51.0]],
                                                             continuation=1_999)))
        return httpx.Response(200, json=dict(result=dict(data=[[1_000, 1, 1, 1, 49.0]], continuation=None)))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    frame = rd.deribit_dvol(client, 0, 10_000)
    assert list(frame.dvol) == [49.0, 50.0, 51.0]


def candles(hours=72, start='2024-01-01'):
    index = pd.date_range(start, periods=hours*12, freq='5min', tz='UTC')
    close = np.linspace(100, 110, len(index))
    return pd.DataFrame(dict(event_time=index, available_at=index+pd.Timedelta(minutes=5), open=close,
                             high=close+.5, low=close-.5, close=close, volume=10.0,
                             taker_buy_volume=np.where(index.hour < 12, 7.0, 3.0)))


def test_hourly_taker_share_and_settlements_align_to_bar_close():
    funding = pd.DataFrame(dict(event_time=pd.to_datetime(['2024-01-01 08:00:00.004', '2024-01-01 16:00:00.010'],
                                                           utc=True), funding_rate=[.0001, .0003]))
    extra = rd.hourly_extras(candles(), funding)
    assert extra.loc['2024-01-01 06:00', 'taker_share_1h'] == pytest.approx(.7)
    assert extra.loc['2024-01-01 13:00', 'taker_share_1h'] == pytest.approx(.3)
    assert extra.loc['2024-01-01 08:00', 'funding_paid'] == pytest.approx(.0001)
    assert extra.funding_paid.sum() == pytest.approx(.0004)


def test_daily_funding_sums_settlements_and_defaults_before_history():
    index = pd.date_range('2019-12-30', periods=4, freq='D', tz='UTC')+pd.Timedelta(days=1)
    funding = pd.DataFrame(dict(event_time=pd.to_datetime(['2020-01-01 08:00', '2020-01-01 16:00',
                                                           '2020-01-02 00:00'], utc=True),
                                funding_rate=[.0001, .0002, .0004]))
    paid = rd.daily_funding(index, funding)
    assert paid.iloc[0] == pytest.approx(3*rd.DEFAULT_FUNDING)      # day of 2019-12-30, before history
    assert paid.loc['2020-01-02'] == pytest.approx(.0001+.0002+.0004)
    assert paid.loc['2020-01-03'] == 0.0                              # history exists, nothing recorded


def test_cache_appends_only_new_rows(tmp_path):
    first = pd.DataFrame(dict(close=[1.0, 2.0]), index=pd.date_range('2024-01-01', periods=2, freq='D', tz='UTC'))
    second = pd.DataFrame(dict(close=[2.5, 3.0]), index=pd.date_range('2024-01-02', periods=2, freq='D', tz='UTC'))
    rd.save_cache(tmp_path, 'demo', first)
    rd.save_cache(tmp_path, 'demo', second)
    cached = rd.load_cache(tmp_path, 'demo')
    assert list(cached.close) == [1.0, 2.5, 3.0]


def test_non_positive_open_interest_is_treated_as_missing():
    oi = pd.Series([100.0, 0.0, -1.0, 101.0])
    assert rd.valid_open_interest(oi).isna().tolist() == [False, True, True, False]
