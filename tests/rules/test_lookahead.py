"""No look-ahead: scrambling every bar after a cut-off must not change anything known before it.

The pattern frame (every feature the indicators, market type, levels and rulebook read) and the rulebook's triggers
and checklists run twice: once on real frames and once on frames with an unrelated path after the cut-off.
Everything up to the cut-off must be identical. A planted leak (a value read three bars ahead) must be caught.
"""
import numpy as np
import pandas as pd
import pytest

from agent_fixtures import frames as five_minute
from quantpilot.analysis import strategies
from quantpilot.features import patterns

PRICES = {'candles': ('open', 'high', 'low', 'close'), 'mark': ('mark_price',), 'index': ('index_price',)}


def scramble(data, cut, seed=99):
    """Same history up to `cut`, an unrelated random path after it (OHLC stays valid)."""
    rng = np.random.default_rng(seed)
    out = {}
    for name, frame in data.items():
        f = frame.copy()
        later = pd.to_datetime(f.available_at, utc=True) > cut
        n = int(later.sum())
        if name in PRICES:
            factor = np.exp(np.cumsum(rng.normal(0, .01, n)))*rng.choice([.9, 1.1])
            for column in PRICES[name]:
                f.loc[later, column] = f.loc[later, column].to_numpy()*factor
        for column in f.columns:
            if column in ('event_time', 'available_at') or column in PRICES.get(name, ()):
                continue
            if pd.api.types.is_numeric_dtype(f[column]):
                f.loc[later, column] = f.loc[later, column].to_numpy()*rng.uniform(.5, 1.5, n)
        out[name] = f
    return out


@pytest.fixture(scope='module')
def worlds():
    data = five_minute(days=42)
    cut = pd.to_datetime(data['candles'].available_at, utc=True).iloc[0].floor('15min')+pd.Timedelta(days=35)
    return data, scramble(data, cut), cut


def until(frame, cut):
    return frame[frame.index <= cut]


def same(a, b):
    numeric = [c for c in a.columns if pd.api.types.is_numeric_dtype(a[c])]
    return bool(len(a)) and a.index.equals(b.index) and np.allclose(a[numeric].to_numpy(float),
                                                                    b[numeric].to_numpy(float), equal_nan=True)


def test_pattern_rows_up_to_the_cut_ignore_a_scrambled_future(worlds):
    data, other, cut = worlds
    assert same(until(patterns.pattern_frame(data), cut), until(patterns.pattern_frame(other), cut))


def test_rulebook_checklists_up_to_the_cut_ignore_a_scrambled_future(worlds):
    data, other, cut = worlds
    views = [strategies.rulebook_views(until(patterns.pattern_frame(d), cut)) for d in (data, other)]
    flat = lambda v: [(x['id'], x['status'], [(c['label'], c['ok'], c['value']) for c in x['checklist']])
                      for p in v['playbooks'] for x in p['versions']]
    assert views[0]['time'] == views[1]['time'] and flat(views[0]) == flat(views[1])


def test_a_planted_leak_is_caught(worlds):
    data, other, cut = worlds
    leak = lambda d: patterns.pattern_frame(d).assign(leak=lambda f: f.close.shift(-3))
    assert not same(until(leak(data), cut), until(leak(other), cut))
