"""Forward-only research data: liquidations and order-book depth cannot be downloaded later.

Liquidations come from Binance's public all-symbol `!forceOrder@arr` stream (BTCUSDT kept) and are stored per minute
(long liquidations are forced SELLs, short liquidations forced BUYs). Binance pushes at most one
liquidation per symbol per second on this stream, so totals are a lower bound. Depth is a
1000-level REST snapshot (about 17 bps each side) once a minute. Both are stored as
`market_observations` families.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from .config import settings
from .market_store import FORWARD_VALUES, save_frame

# All-symbol liquidation stream on Binance's market path, filtered to BTCUSDT. The legacy /ws/ path accepts
# connections but delivered no data at all on 2026-10-20, which recorded nothing silently.
STREAM = 'wss://fstream.binance.com/market/ws/!forceOrder@arr'
SILENCE_LIMIT = 900   # seconds without any message (any symbol) before the stream is treated as dead
DEPTH = 'https://fapi.binance.com/fapi/v1/depth'


def parse_liquidation(message):
    try:
        order = json.loads(message)['o']
        if order.get('s') != 'BTCUSDT':
            return None
        qty = float(order.get('z') or order['q'])
        return dict(time=pd.Timestamp(int(order['T']), unit='ms', tz='UTC'), side=order['S'], qty=qty,
                    usd=qty*float(order['ap']))
    except (ValueError, KeyError, TypeError):
        return None


def aggregate_liquidations(events, received_at):
    rows = {}
    for item in filter(None, events):
        minute = item['time'].floor('min')
        row = rows.setdefault(minute, dict(event_time=minute, available_at=minute+pd.Timedelta(minutes=1),
                                           long_liq_qty=0.0, long_liq_usd=0.0, short_liq_qty=0.0,
                                           short_liq_usd=0.0, liq_count=0.0))
        kind = 'long' if item['side'] == 'SELL' else 'short'
        row[f'{kind}_liq_qty'] += item['qty']
        row[f'{kind}_liq_usd'] += item['usd']
        row['liq_count'] += 1
    frame = pd.DataFrame(sorted(rows.values(), key=lambda r: r['event_time']))
    if len(frame):
        frame['received_at'], frame['source'] = received_at, STREAM
    return frame


def split_flush(events, now, grace=pd.Timedelta(minutes=2)):
    """Minutes older than `grace` are complete; later events may still arrive for the rest."""
    cutoff = (now-grace).floor('min')
    return [e for e in events if e['time'] < cutoff], [e for e in events if e['time'] >= cutoff]


def flush(events, now, saver, grace=pd.Timedelta(minutes=2)):
    """Save complete minutes once; on failure keep every event for the next attempt."""
    done, remaining = split_flush(events, now, grace)
    if not done:
        return remaining
    try:
        saver(aggregate_liquidations(done, now))
    except Exception as exc:
        print(json.dumps(dict(liquidations='save_failed', error_type=type(exc).__name__)), flush=True)
        return events
    return remaining


def depth_features(book, received_at):
    """Size resting within 5 and 15 bps of the mid on each side, their imbalance, and the spread."""
    bids = [(float(p), float(q)) for p, q in book['bids']]
    asks = [(float(p), float(q)) for p, q in book['asks']]
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid+best_ask)/2
    row = dict(event_time=pd.Timestamp(int(book['T']), unit='ms', tz='UTC').floor('min'),
               available_at=pd.Timestamp(received_at), received_at=pd.Timestamp(received_at), source=DEPTH,
               mid=mid, spread_bps=(best_ask-best_bid)/mid*1e4,
               bid_reach_bps=(mid-bids[-1][0])/mid*1e4, ask_reach_bps=(asks[-1][0]-mid)/mid*1e4)
    for bps in (5, 15):
        bid = sum(q for p, q in bids if p >= mid*(1-bps/1e4))
        ask = sum(q for p, q in asks if p <= mid*(1+bps/1e4))
        row |= {f'bid_qty_{bps}bps': bid, f'ask_qty_{bps}bps': ask,
                f'imbalance_{bps}bps': (bid-ask)/(bid+ask) if bid+ask else 0.0}
    return row


def stream_stale(last_message, now, limit=SILENCE_LIMIT):
    return now-last_message > limit


def save(engine, family, frame):
    if family not in FORWARD_VALUES:
        raise ValueError('unsupported forward family')
    return save_frame(engine, family, frame) if len(frame) else 0


async def _liquidations(engine, flush_seconds=60):
    import websockets
    pending, backoff = [], 1
    while True:
        try:
            async with websockets.connect(STREAM, ping_interval=60, close_timeout=5) as socket:
                backoff, last = 1, asyncio.get_running_loop().time()
                heard = last
                while True:
                    try:
                        message = await asyncio.wait_for(socket.recv(), timeout=flush_seconds)
                        heard = asyncio.get_running_loop().time()
                        item = parse_liquidation(message)
                        if item:
                            pending.append(item)
                    except asyncio.TimeoutError:
                        if stream_stale(heard, asyncio.get_running_loop().time()):
                            raise ConnectionError('liquidation stream silent')   # reconnect; logged below
                    if asyncio.get_running_loop().time()-last >= flush_seconds:
                        pending = await asyncio.to_thread(flush, pending, pd.Timestamp.now(tz='UTC'),
                                                          lambda frame: save(engine, 'liquidations', frame))
                        last = asyncio.get_running_loop().time()
        except Exception as exc:  # reconnect: the stream drops every 24 hours by design
            print(json.dumps(dict(liquidations='reconnecting', error_type=type(exc).__name__)), flush=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 300)


async def _depth(engine, every=60):
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            try:
                book = (await client.get(DEPTH, params=dict(symbol='BTCUSDT', limit=1000))).raise_for_status().json()
                row = depth_features(book, pd.Timestamp.now(tz='UTC'))
                await asyncio.to_thread(save, engine, 'depth', pd.DataFrame([row]))
            except Exception as exc:
                print(json.dumps(dict(depth='blocked', error_type=type(exc).__name__)), flush=True)
            await asyncio.sleep(every-pd.Timestamp.now(tz='UTC').second % every)


def run(engine=None):
    """Supervisor entry point: run both forward collectors forever."""
    from .store import database
    engine = engine or database(settings.market_database_url)

    async def main():
        await asyncio.gather(_liquidations(engine), _depth(engine))
    asyncio.run(main())
