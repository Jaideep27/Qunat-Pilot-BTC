"""Extra research data and the hourly/daily panels used by the selective strategy research.

Historical families are public and cached incrementally as Parquet under data/research/:
Binance spot BTCUSDT klines (daily 2017-08+, hourly 2021-09+), Binance USD-M funding archives
(2020-01+), Coinbase BTC-USD candles, and Deribit's DVOL implied-volatility index (2021-03+).
Every bar is indexed by the instant it closes, so a row only uses information known by then.
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from quantpilot.config import settings

ARCHIVE = 'https://data.binance.vision/data'
SPOT_REST = 'https://api.binance.com/api/v3/klines'
COINBASE = 'https://api.exchange.coinbase.com/products/BTC-USD/candles'
DERIBIT = 'https://www.deribit.com/api/v2/public/get_volatility_index_data'
INTERVALS = {'1h': pd.Timedelta(hours=1), '1d': pd.Timedelta(days=1)}
DEFAULT_FUNDING = .0001  # Binance's default 8-hour rate, charged where no funding history exists
STARTS = {'spot_1d': '2017-08-01', 'spot_1h': '2021-09-01', 'funding_archive': '2020-01-01',
          'coinbase_1d': '2017-08-01', 'coinbase_1h': '2021-09-01', 'dvol_1h': '2021-03-24'}


def root_dir(root=None):
    return Path(root) if root else settings.data_dir/'research'


def parse_klines(content, interval):
    """Binance kline archive (zip of CSV, with or without header; ms or us timestamps) indexed by close time."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read(archive.namelist()[0]).decode()
    first = raw.split(',', 1)[0]
    frame = pd.read_csv(io.StringIO(raw), header=0 if not first.strip().isdigit() else None)
    frame = frame.iloc[:, [0, 1, 2, 3, 4, 5, 9]]
    frame.columns = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_volume']
    stamps = pd.to_numeric(frame.open_time)
    unit = 'us' if stamps.iloc[0] > 1e14 else 'ms'
    frame.index = pd.to_datetime(stamps, unit=unit, utc=True)+INTERVALS[interval]
    return frame.drop(columns='open_time').astype(float)


def _months(start, end):
    return pd.date_range(pd.Timestamp(start).replace(day=1), end, freq='MS', inclusive='left')


def binance_archive_klines(client, interval, start, end):
    parts = []
    for month in _months(start, end):
        url = f'{ARCHIVE}/spot/monthly/klines/BTCUSDT/{interval}/BTCUSDT-{interval}-{month:%Y-%m}.zip'
        response = client.get(url)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        parts.append(parse_klines(response.content, interval))
    return pd.concat(parts) if parts else pd.DataFrame()


def binance_rest_klines(client, interval, start, now=None):
    """Completed spot klines from `start` (used for the months the archives do not cover yet)."""
    now, rows, cursor = pd.Timestamp(now or pd.Timestamp.now(tz='UTC')), [], int(pd.Timestamp(start).timestamp()*1000)
    while True:
        batch = client.get(SPOT_REST, params=dict(symbol='BTCUSDT', interval=interval, startTime=cursor,
                                                  limit=1000)).raise_for_status().json()
        rows += batch
        if len(batch) < 1000:
            break
        cursor = batch[-1][0]+1
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([r[:6]+[r[9]] for r in rows],
                         columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_volume'])
    frame.index = pd.to_datetime(frame.open_time, unit='ms', utc=True)+INTERVALS[interval]
    frame = frame.drop(columns='open_time').astype(float)
    return frame[frame.index <= now]


def funding_archive(client, start, end):
    parts = []
    for month in _months(start, end):
        url = f'{ARCHIVE}/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{month:%Y-%m}.zip'
        response = client.get(url)
        if response.status_code == 404:
            continue
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            frame = pd.read_csv(archive.open(archive.namelist()[0]))
        parts.append(pd.DataFrame(dict(funding_rate=frame.last_funding_rate.astype(float).to_numpy()),
                                  index=pd.to_datetime(pd.to_numeric(frame.calc_time), unit='ms', utc=True)))
    return pd.concat(parts) if parts else pd.DataFrame()


def coinbase_candles(client, granularity, start, end, page=300, pause=.12):
    """Coinbase BTC-USD candles in [start, end), indexed by close time, oldest first."""
    step, rows, cursor = pd.Timedelta(seconds=granularity), [], pd.Timestamp(start)
    while cursor < end:
        stop = min(cursor+step*page, pd.Timestamp(end))
        batch = client.get(COINBASE, params=dict(granularity=granularity, start=cursor.isoformat(),
                                                 end=(stop-step).isoformat())).raise_for_status().json()
        rows += batch
        cursor = stop
        if pause:
            time.sleep(pause)
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=['time', 'low', 'high', 'open', 'close', 'volume'])
    frame.index = pd.to_datetime(frame.time, unit='s', utc=True)+step
    frame = frame.drop(columns='time').astype(float)
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    return frame[frame.index <= pd.Timestamp.now(tz='UTC')]


def deribit_dvol(client, start_ms, end_ms, resolution=3600):
    """Deribit BTC DVOL (30-day implied volatility index, annualised %) candles, oldest first."""
    rows, end = [], end_ms
    while True:
        result = client.get(DERIBIT, params=dict(currency='BTC', start_timestamp=start_ms, end_timestamp=end,
                                                 resolution=resolution)).raise_for_status().json()['result']
        rows += result['data']
        if not result.get('continuation'):
            break
        end = result['continuation']
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=['time', 'open', 'high', 'low', 'dvol'])
    frame.index = pd.to_datetime(frame.time, unit='ms', utc=True)+pd.Timedelta(seconds=resolution)
    frame = frame[['dvol']].astype(float)
    return frame[~frame.index.duplicated(keep='last')].sort_index()


def load_cache(root, name):
    path = root_dir(root)/f'{name}.parquet'
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def save_cache(root, name, frame):
    """Merge new rows into the cache; overlapping timestamps take the newer value."""
    path = root_dir(root)/f'{name}.parquet'
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = pd.concat([load_cache(root, name), frame])
    merged = merged[~merged.index.duplicated(keep='last')].sort_index()
    merged.to_parquet(path.with_suffix('.partial'))
    path.with_suffix('.partial').replace(path)
    return merged


def refresh(root=None, client=None, now=None):
    """Fetch every historical research family incrementally. Returns rows and range per family."""
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC'))
    month = now.normalize().replace(day=1)
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    out = {}
    for name, fetch in [
        ('spot_1d', lambda s: _klines(client, '1d', s, month, now)),
        ('spot_1h', lambda s: _klines(client, '1h', s, month, now)),
        ('funding_archive', lambda s: funding_archive(client, s, min(month, pd.Timestamp('2022-01-01', tz='UTC')))),
        ('coinbase_1d', lambda s: coinbase_candles(client, 86400, s, now.floor('D'))),
        ('coinbase_1h', lambda s: coinbase_candles(client, 3600, s, now.floor('h'))),
        ('dvol_1h', lambda s: deribit_dvol(client, int(s.timestamp()*1000), int(now.timestamp()*1000))),
    ]:
        cached = load_cache(root, name)
        start = pd.Timestamp(STARTS[name], tz='UTC') if cached.empty else cached.index.max()-pd.Timedelta(days=3)
        fresh = fetch(start)
        merged = save_cache(root, name, fresh) if len(fresh) else cached
        out[name] = dict(rows=len(merged), first=str(merged.index.min()), last=str(merged.index.max()))
    return out


def _klines(client, interval, start, month, now):
    archived = binance_archive_klines(client, interval, start, month)
    recent = binance_rest_klines(client, interval, month, now)
    return pd.concat([archived, recent]) if len(recent) else archived


def _settlement_bars(funding, freq):
    """Each funding settlement mapped to the bar whose close is at or after the (rounded) settlement time."""
    if funding is None or len(funding) == 0:
        return pd.Series(dtype=float)
    times = pd.DatetimeIndex(pd.to_datetime(funding.event_time if 'event_time' in funding else funding.index,
                                            utc=True))
    rates = pd.Series(np.asarray(funding.funding_rate, float), index=times.round('h'))
    return rates.groupby(rates.index.ceil(freq)).sum()


def hourly_extras(candles, funding):
    """Per hourly bar (indexed by close): taker buy share of that hour and funding settled at its close."""
    frame = candles.sort_values('event_time').set_index('event_time')
    grouped = frame.resample('1h', closed='left', label='left')
    hours = grouped.agg(dict(volume='sum', taker_buy_volume='sum'))
    hours = hours[grouped.close.count() == 12]
    hours.index = hours.index+pd.Timedelta(hours=1)
    out = pd.DataFrame(index=hours.index)
    out['taker_share_1h'] = hours.taker_buy_volume/hours.volume.replace(0, np.nan)
    out['funding_paid'] = _settlement_bars(funding, 'h').reindex(out.index).fillna(0.0)
    return out


def daily_funding(index, funding):
    """Funding a long pays per daily bar; the default rate x 3 settlements before funding history exists."""
    settled = _settlement_bars(funding, 'D')
    paid = settled.reindex(index).fillna(0.0)
    if len(settled):
        first = pd.to_datetime(funding.event_time if 'event_time' in funding else funding.index, utc=True).min()
        paid[index < first] = 3*DEFAULT_FUNDING
    else:
        paid[:] = 3*DEFAULT_FUNDING
    return paid


def valid_open_interest(series):
    """Archive rows occasionally carry 0 open interest next to valid ratios; treat <= 0 as missing."""
    return series.where(series > 0)


def build_panels(root=None, engine=None, now=None):
    """Hourly (2021-10+) and daily (2017-08+) research panels, cached under data/research/."""
    from quantpilot.research.event_study import hourly_panel, load_market
    from quantpilot.data.macro import events as macro_events
    now = pd.Timestamp(now or pd.Timestamp.now(tz='UTC')).floor('h')
    first = load_cache(root, 'spot_1h').index.min()
    start = max(pd.Timestamp('2021-10-01', tz='UTC'), first if pd.notna(first) else now-pd.DateOffset(years=5))
    frames = load_market(engine, start, now)
    hourly = hourly_panel(frames, macro_events()).join(hourly_extras(frames['candles'], frames.get('funding')))
    hourly['open_interest'] = valid_open_interest(hourly.open_interest)
    spot_1h, cb_1h, dvol = (load_cache(root, n) for n in ('spot_1h', 'coinbase_1h', 'dvol_1h'))
    if len(spot_1h) and len(cb_1h):
        hourly['coinbase_premium'] = (cb_1h.close/spot_1h.close-1).reindex(hourly.index)
    hourly['dvol'] = dvol.dvol.reindex(hourly.index) if len(dvol) else np.nan

    spot = load_cache(root, 'spot_1d')
    daily = spot[['open', 'high', 'low', 'close', 'volume']].copy()
    db_funding = frames.get('funding')
    history = load_cache(root, 'funding_archive')
    parts = [history.rename_axis('event_time').reset_index()] if len(history) else []
    if db_funding is not None and len(db_funding):
        parts.append(db_funding[['event_time', 'funding_rate']])
    funding = pd.concat(parts, ignore_index=True).drop_duplicates('event_time') if parts else None
    daily['funding_paid'] = daily_funding(daily.index, funding)
    if funding is not None:
        times = pd.to_datetime(funding.event_time, utc=True)
        rate = pd.Series(funding.funding_rate.to_numpy(float), index=times).sort_index()
        daily['funding_rate'] = rate.groupby(rate.index.round('h').ceil('D')).mean().reindex(daily.index)
    positioning = frames.get('positioning')
    if positioning is not None and len(positioning):
        oi = valid_open_interest(positioning.set_index('available_at').open_interest.astype(float)).dropna()
        daily['open_interest'] = oi.groupby(oi.index.ceil('D')).last().reindex(daily.index)
    cb_1d = load_cache(root, 'coinbase_1d')
    if len(cb_1d):
        daily['coinbase_premium'] = (cb_1d.close/daily.close-1).reindex(daily.index)
    if len(dvol):
        daily['dvol'] = dvol.dvol.groupby(dvol.index.ceil('D')).last().reindex(daily.index)
    for name, panel in (('hourly_panel', hourly), ('daily_panel', daily)):
        path = root_dir(root)/f'{name}.parquet'
        path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(path)
    return hourly, daily
