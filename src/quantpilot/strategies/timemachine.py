"""Time Machine (A10; borrowed from github.com/Jakeschincariol/day-trader-skill): before a trade, replay its future
many times from BTC's own recent moves and record the honest odds. Recorded on the trade only, never a gate.

Each path draws, for every step ahead, the return of a random one of the last 60 days at the same time of day
(5-minute steps up to 24 hours, hourly steps beyond). Stops and targets are checked on the path's closes.
"""
import numpy as np
import pandas as pd

PATHS, DAYS = 2000, 60


def _matrix(closes, t, step, days):
    c = closes[(closes.index <= t) & (closes.index > t-pd.Timedelta(days=days+1))]
    if step != pd.Timedelta(minutes=5):
        c = c.resample(step, label='right', closed='right').last().dropna()
    r = np.log(c).diff().dropna()
    slots = int(pd.Timedelta(days=1)/step)
    slot = ((r.index-r.index.normalize())//step).astype(int) % slots
    day = (r.index-pd.Timedelta(microseconds=1)).normalize()
    table = pd.DataFrame(dict(r=r.to_numpy(), slot=slot, day=day)).pivot_table(index='day', columns='slot', values='r')
    table = table.reindex(columns=range(slots)).iloc[-days:]
    return table.to_numpy(float), slots


def odds(closes, t, side, entry, stop, target, hours, paths=PATHS, days=DAYS):
    t = pd.Timestamp(t)
    step = pd.Timedelta(minutes=5) if hours <= 24 else pd.Timedelta(hours=1)
    table, slots = _matrix(closes, t, step, days)
    if table.size == 0:
        return dict(paths=0, days=0, target_first=None, stop_first=None, neither=None, median_r=None)
    steps = int(pd.Timedelta(hours=hours)/step)
    rng = np.random.default_rng(int(t.timestamp()//60))
    start = int(((t-t.normalize())//step)) % slots
    cols = (start+np.arange(1, steps+1)) % slots
    rows = rng.integers(0, table.shape[0], size=(paths, steps))
    draws = np.nan_to_num(table[rows, cols[None, :]], nan=0.0)
    path = entry*np.exp(np.cumsum(draws, axis=1))
    sgn = 1 if side == 'long' else -1
    risk = abs(entry-stop)
    big = steps+1
    first_stop = np.where((sgn*(path-stop) <= 0).any(axis=1), (sgn*(path-stop) <= 0).argmax(axis=1), big)
    if target is None:
        first_target = np.full(paths, big)
    else:
        hit = sgn*(path-target) >= 0
        first_target = np.where(hit.any(axis=1), hit.argmax(axis=1), big)
    stopped = first_stop < first_target
    reached = first_target < first_stop
    final = sgn*(path[:, -1]-entry)/risk
    result = np.where(stopped, -1.0, np.where(reached, sgn*((target or entry)-entry)/risk, final))
    return dict(paths=int(paths), days=int(table.shape[0]), hours=float(hours), target_first=float(reached.mean()),
                stop_first=float(stopped.mean()), neither=float(1-reached.mean()-stopped.mean()),
                median_r=float(np.median(result)))
