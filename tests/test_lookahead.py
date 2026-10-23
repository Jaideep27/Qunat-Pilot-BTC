"""No look-ahead, end to end: scrambling every bar after a cut-off must not change anything decided before it.

The whole live chain runs twice on frames that still contain the future: patterns, triggers, playbook
checklists, plans, the reference account, the four A8 accounts' scores and decisions, and closed trades.
The second run sees an unrelated path after the cut-off. Everything up to the cut-off must be identical.
A planted leak (a feature read three bars ahead with truncation switched off) must be caught.
"""
import numpy as np
import pandas as pd
import pytest

from agent_fixtures import frames as five_minute
from quantpilot import account_journal, journal, patterns, rule_live, store
from test_account_live import T15, cfg
from test_rule_live import book, minute_bars

CUT = T15+pd.Timedelta(hours=3)
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
    other = scramble(data, CUT)
    return (data, minute_bars(data['candles'])), (other, minute_bars(other['candles']))


def feature_scorer(hit, features):
    """A score that depends on live features, so leaked features would change decisions."""
    rsi = features.get('rsi_15m')
    s = (float(rsi)-50)/50 if rsi is not None and np.isfinite(rsi) else 0.0
    return dict(p=.5, score=s+(.5 if hit['playbook'] == 'L1' else 0), missing=[])


def run_world(engine, world, b):
    data, fast = world
    rule_live._cache.clear()        # the pattern cache must not carry one world into the other
    for k in range(int((CUT-T15)/pd.Timedelta(minutes=15))+1):
        now = T15+pd.Timedelta(minutes=15*k, seconds=20)
        rule_live.cycle(engine, now, book=b, frames=data, fast=fast, macro_events=[], news_events=[],
                        accounts_cfg=cfg(), scorer=feature_scorer)
    rule_live._cache.clear()
    plan = lambda p: None if not p else tuple(round(float(p[k]), 6) for k in ('entry', 'stop', 'target'))
    hits = sorted((h['id'], h['status'], plan(h['payload'].get('plan'))) for h in journal.hits(engine, limit=10_000)
                  if pd.Timestamp(h['time']) <= CUT)
    decisions = sorted((d['id'], d['decision'], None if d['score'] is None else round(d['score'], 9))
                       for d in account_journal.decisions(engine, limit=10_000) if pd.Timestamp(d['time']) <= CUT)
    trades = sorted((t['id'], round(t['net'], 6)) for t in account_journal.trades(engine, limit=10_000)
                    if pd.Timestamp(t['exit_time']) <= CUT)
    return hits, decisions, trades


def second_engine(tmp_path):
    engine = store.database('sqlite:///'+str(tmp_path/'other.db'))
    store.initialize(engine)
    return engine


def test_pattern_rows_up_to_the_cut_ignore_a_scrambled_future(worlds):
    (data, _), (other, _) = worlds
    a, b = patterns.pattern_frame(data), patterns.pattern_frame(other)
    a, b = a[a.index <= CUT], b[b.index <= CUT]
    numeric = [c for c in a.columns if pd.api.types.is_numeric_dtype(a[c])]
    assert len(a) and a.index.equals(b.index)
    assert np.allclose(a[numeric].to_numpy(float), b[numeric].to_numpy(float), equal_nan=True)


def test_the_whole_live_chain_ignores_a_scrambled_future(account, worlds, tmp_path):
    original, scrambled = worlds
    b = book()
    first, second = run_world(account, original, b), run_world(second_engine(tmp_path), scrambled, b)
    hits, decisions, trades = first
    assert hits and decisions and trades           # the window really decides and trades
    assert first == second


def test_the_check_catches_a_planted_leak(account, worlds, tmp_path, monkeypatch):
    real = patterns.pattern_frame
    monkeypatch.setattr(rule_live, '_truncate', lambda frames, now: frames)       # stop hiding the future
    monkeypatch.setattr(rule_live.patterns, 'pattern_frame',
                        lambda frames, at=None: real(frames, at).assign(rsi_15m=lambda d: d.rsi_15m.shift(-3),
                                                                        close=lambda d: d.close.shift(-3)))
    original, scrambled = worlds
    b = book()
    assert run_world(account, original, b) != run_world(second_engine(tmp_path), scrambled, b)
