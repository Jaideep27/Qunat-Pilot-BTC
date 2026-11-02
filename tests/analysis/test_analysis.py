"""Analysis app (A15): indicator library, read-outs, strategy views, snapshot, Claude briefs/explanations, switch-off."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantpilot.analysis import ai, catalogue, readings, snapshot, strategies
from quantpilot.config import settings
from quantpilot.features import charting, patterns
from quantpilot.features.core import technical
from quantpilot.strategies import breakout, market, range_trader, trend
from style_fixtures import synthetic

TONES = {'up', 'down', 'hot', 'cold', 'neutral', 'quiet', 'busy', 'warn'}


def with_market(candles):
    """Frames for `pattern_frame`: the candles plus positioning, funding and sentiment that drift slowly."""
    meta = candles[['event_time', 'available_at']].assign(source='fixture', received_at=candles.available_at)
    n = len(meta)
    return dict(candles=candles.assign(source='fixture', received_at=candles.available_at, trade_count=1000),
                mark=meta.assign(mark_price=candles.close*1.0001), index=meta.assign(index_price=candles.close),
                positioning=meta.assign(open_interest=np.linspace(10000, 11000, n), global_ratio=1.6,
                                        top_account_ratio=1.2, top_position_ratio=1.05),
                funding=meta.iloc[::96].assign(funding_rate=np.linspace(.00005, .00015, len(meta.iloc[::96]))),
                sentiment=meta.iloc[::288].assign(fear_greed=40.0))


@pytest.fixture(scope='module')
def world():
    candles = synthetic(days=45, seed=7, start='2026-02-01')
    daily = synthetic(days=200, seed=8, start='2025-07-20')
    return with_market(candles), daily


@pytest.fixture(scope='module')
def snap(world):
    frames, daily = world
    return snapshot.build(None, now=frames['candles'].available_at.iloc[-1], frames=frames, daily_candles=daily,
                          side_data=False)


# ---- the catalogue ----------------------------------------------------------------------------------------------------

def test_every_indicator_is_complete_and_its_zones_cover_the_whole_line():
    assert len(catalogue.INDICATORS) == len(catalogue.BY_ID) >= 25
    for ind in catalogue.INDICATORS:
        assert ind['group'] in catalogue.GROUPS and ind['scope'] in ('timeframe', 'market')
        assert ind['what'] and ind['how'] and ind['settings'] and len(ind['scale']) == 2
        bounds = [z[0] for z in ind['zones']]
        assert bounds[-1] is None and all(b is not None for b in bounds[:-1]), ind['id']
        assert bounds[:-1] == sorted(bounds[:-1]) and len(set(bounds[:-1])) == len(bounds)-1, ind['id']
        assert all(z[1] and z[2] in TONES and z[3] for z in ind['zones']), ind['id']


def test_zone_boundaries_are_upper_exclusive():
    rsi = catalogue.BY_ID['rsi_14']
    assert [catalogue.zone(rsi, v)[1] for v in (10, 30, 44.9, 50, 55, 69.9, 70, 99)] == \
        ['Oversold', 'Weak', 'Weak', 'Neutral', 'Strong', 'Strong', 'Overbought', 'Overbought']
    assert catalogue.zone(rsi, None) is None


def test_show_on_chart_names_real_chart_studies(world):
    candles = world[0]['candles'].iloc[-600:]
    rows = candles.assign(event_time=candles.event_time, available_at=candles.available_at).to_dict('records')
    known = {s['id'] for s in charting.studies(rows)['catalogue']}
    for ind in catalogue.INDICATORS:
        assert set(ind['study'] or []) <= known, (ind['id'], set(ind['study'] or [])-known)


# ---- read-outs ----------------------------------------------------------------------------------------------------------

def test_timeframe_readings_equal_the_chart_maths_on_completed_bars(world):
    candles = world[0]['candles']
    out = readings.timeframe_readings(candles, '1h')
    bars = patterns.resample(patterns._candles(dict(candles=candles)), '1h', 12)
    expected = technical(bars).iloc[-1]
    got = {i['id']: i['value'] for i in out['items']}
    assert got['rsi_14'] == pytest.approx(expected.rsi_14) and got['adx'] == pytest.approx(expected.adx)
    assert out['time'] == bars.index[-1].isoformat() and bars.index[-1] <= candles.available_at.iloc[-1]
    assert {i['id'] for i in out['items']} == {i['id'] for i in catalogue.INDICATORS if i['scope'] == 'timeframe'}
    rsi = next(i for i in out['items'] if i['id'] == 'rsi_14')
    assert len(rsi['spark']) == readings.SPARK and rsi['label'] == catalogue.zone(catalogue.BY_ID['rsi_14'], rsi['value'])[1]


def test_readings_ignore_the_future():
    """Scrambling every candle after a cut-off must not change any timeframe reading known before it."""
    real = synthetic(days=40, seed=31)
    cut = real.available_at.iloc[int(len(real)*.8)]
    other = real.copy()
    later = other.available_at > cut
    other.loc[later, ['open', 'high', 'low', 'close']] *= 1.2
    other.loc[later, 'volume'] *= 3
    for tf in ('15m', '1h', '4h'):
        fa, fb = readings.timeframe_frame(real, tf)[1], readings.timeframe_frame(other, tf)[1]
        pd.testing.assert_frame_equal(fa.loc[:cut], fb.loc[:cut], check_dtype=False)
    with pytest.raises(AssertionError):     # a planted leak (a value three bars ahead) is caught by the same check
        fa, fb = (readings.timeframe_frame(c, '1h')[1].assign(leak=lambda f: f.rsi_14.shift(-3)) for c in (real, other))
        pd.testing.assert_frame_equal(fa.loc[:cut], fb.loc[:cut], check_dtype=False)


def test_market_readings_convert_funding_to_a_yearly_rate():
    pf = pd.DataFrame([dict(funding_rate=.0001, range_pos_4h=.5, vwap_z=0, funding_z=0, oi_change_24h=0,
                            global_ratio=1.5, fear_greed=50, premium=0, liq_z_15m=np.nan)],
                      index=pd.DatetimeIndex([pd.Timestamp('2026-01-01', tz='UTC')]))
    out = {i['id']: i for i in readings.market_readings(pf)['items']}
    assert out['funding_annual']['value'] == pytest.approx(.0001*3*365) and out['funding_annual']['label'] == 'Normal'
    assert out['liq_z_15m']['value'] is None and out['liq_z_15m']['label'] == 'No data'


def test_group_summary_counts_directional_tones_only():
    items = [dict(id='rsi_14', tone='up'), dict(id='stochastic', tone='hot'), dict(id='cci', tone='down'),
             dict(id='adx', tone='busy')]
    s = readings.group_summary(items)
    assert s['Momentum'] == dict(up=2, down=1, total=3, lean='up', line='Momentum: 2 of 3 lean up, 1 lean down')
    assert s['Trend']['lean'] == 'mixed' and s['Trend']['up'] == 0


# ---- strategy views -----------------------------------------------------------------------------------------------------

def test_trend_rider_view_matches_the_vote_and_its_turning_level_is_exact():
    daily = market.daily_bars(synthetic(days=200, seed=8, start='2025-07-20'))
    f = trend.frame(daily)
    view = strategies.trend_rider(daily)
    assert view['vote'] == int(f.vote.iloc[-1]) and len(view['checklist']) == 4
    level = view['levels']['turns_up_above']
    nxt = daily.index[-1]+pd.Timedelta(days=1)
    for close, up in ((level*1.001, True), (level*0.999, False)):
        row = pd.DataFrame(dict(open=close, high=close, low=close, close=close, volume=1.0), index=[nxt])
        last = trend.frame(pd.concat([daily, row])).iloc[-1]
        assert bool(last.r20 > 0 and last.r60 > 0 and last.r120 > 0 and last.close > last.sma50) is up


def test_intraday_views_agree_with_the_strategies_own_switches(world):
    sf = market.style_frame(world[0]['candles'])
    t = sf.index[-1]
    r, b = strategies.range_rider(sf), strategies.breakout_hunter(sf)
    assert (r['status'] != 'off') == range_trader.active(sf, t)[0]
    assert (b['status'] != 'off') == breakout.active(sf, t)[0]
    assert not r['says'].startswith(('On:', 'Off:')) and len(b['checklist']) == 3


def test_funding_collector_reads_the_7_and_3_day_averages():
    funding = pd.DataFrame(dict(event_time=pd.date_range('2026-01-01', periods=30, freq='8h', tz='UTC'),
                                funding_rate=[-.0001]*21+[.0001]*9))
    view = strategies.funding_collector(funding)
    assert view['status'] == 'off' and [c['ok'] for c in view['checklist']] == [False, True]


def test_the_master_stands_aside_without_a_daily_trend(world):
    sf = market.style_frame(world[0]['candles'])
    view = strategies.master(dict(vote=0), dict(status='bullish'), dict(status='off'), [], sf)
    assert view['status'] == 'standing aside'
    up = strategies.master(dict(vote=1), dict(status='bullish'), dict(status='off'),
                           [dict(id='PB09L', side='long'), dict(id='PB09S', side='short')], sf)
    assert up['status'] == 'bullish' and 'Range Trader' in up['says'] and 'PB09L' in up['says'] and 'PB09S' not in up['says']


def test_the_rulebook_view_shows_all_13_playbooks_and_20_versions(world):
    pf = patterns.pattern_frame(world[0])
    rb = strategies.rulebook_views(pf)
    versions = [v for p in rb['playbooks'] for v in p['versions']]
    assert len(rb['playbooks']) == 13 and len(versions) == 20
    assert all(v['status'] in ('firing', 'triggered, checklist not met', 'waiting for its trigger') for v in versions)
    assert all(v['checklist'][0]['label'].startswith('Market type is') for v in versions)
    assert all(p['versions'][0]['shadow'] for p in rb['playbooks'] if p['id'] == 'PB13')


# ---- the snapshot -------------------------------------------------------------------------------------------------------

def test_the_snapshot_has_every_section_and_is_plain_json(snap):
    assert set(snap) >= {'built_at', 'price', 'regime', 'timeframes', 'market', 'summary', 'levels', 'range_4h',
                         'strategies', 'calendar', 'headlines', 'note'}
    assert list(snap['timeframes']) == ['15m', '1h', '4h', '1d'] and snap['price']['last'] > 0
    assert [s['id'] for s in snap['strategies']['styles']] == ['T1', 'R1', 'B1', 'C1', 'X2']
    assert all(l['price'] > 0 for l in snap['levels'])
    json.dumps(snap, allow_nan=False)
    small = json.dumps(snapshot.compact(snap), default=str)
    assert len(small) < 30_000 and '"spark"' not in small and '"curve"' not in small


def test_snapshots_are_saved_and_the_latest_is_read_back(account, snap):
    first = dict(snap, built_at='2026-03-01T00:00:00+00:00')
    later = dict(snap, built_at='2026-03-01T00:05:00+00:00')
    snapshot.save(account, first)
    snapshot.save(account, later)
    assert snapshot.latest(account)['built_at'] == later['built_at']
    snapshot.save(account, dict(snap, built_at='2026-03-02T06:00:00+00:00'))     # older than 24h are pruned
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    with Session(account) as session:
        assert session.scalar(select(func.count()).select_from(snapshot.AnalysisSnapshot)) == 1


# ---- Claude: briefs and explanations ------------------------------------------------------------------------------------

BRIEF = dict(headline='Quiet, slightly weak', overall='mixed', summary='s', trend='t', momentum='m', volatility='v',
             volume_flow='f', positioning='p', strategies='x', news_calendar='n', levels_to_watch=['a'], risks=['r'])


def test_a_brief_uses_the_light_model_and_only_the_compact_snapshot(account, snap):
    calls = []
    def call(system, prompt, schema, model):
        calls.append((system, json.loads(prompt), model))
        return BRIEF
    record = ai.write_brief(account, snap, now=pd.Timestamp('2026-03-01 04:01', tz='UTC'), call=call)
    assert record['status'] == 'ok' and record['brief']['headline'] == BRIEF['headline']
    system, prompt, model = calls[0]
    assert model == settings.analysis_brief_model == 'claude-haiku-5-5'
    assert 'Never tell the reader to buy' in system and 'spark' not in json.dumps(prompt)
    assert ai.briefs(account, 1)[0]['id'] == record['id']


def test_a_failed_brief_is_recorded_and_the_schedule_does_not_retry_it(account, snap):
    class UsageLimitReached(Exception):
        pass
    def call(*_):
        raise UsageLimitReached('limit')
    now = pd.Timestamp('2026-03-01 08:02', tz='UTC')
    assert ai.due(account, snap, now) == 'scheduled'
    record = ai.write_brief(account, snap, now=now, call=call)
    assert record['status'] == 'limit' and 'brief' not in record
    assert ai.due(account, snap, now+pd.Timedelta(hours=1)) is None
    assert ai.due(account, snap, now+pd.Timedelta(hours=4)) == 'scheduled'


def test_a_fresh_macro_release_brings_a_brief_forward(account, snap):
    ai.write_brief(account, snap, now=pd.Timestamp('2026-03-01 12:01', tz='UTC'), call=lambda *_: BRIEF)
    released = dict(snap, calendar=dict(upcoming=[], recent=[dict(label='CPI', kind='cpi', at='2026-03-01T13:30:00+00:00')]))
    assert ai.due(account, released, pd.Timestamp('2026-03-01 13:10', tz='UTC')) is None
    assert ai.due(account, released, pd.Timestamp('2026-03-01 13:40', tz='UTC')) == 'macro'


def test_an_explanation_uses_the_deep_model_is_cached_per_snapshot_and_rate_limited(account, snap, monkeypatch):
    calls = []
    def call(system, prompt, schema, model):
        calls.append((json.loads(prompt), model))
        return dict(title='RSI', explanation='RSI is neutral.', what_to_watch=['a move above 55'])
    now = pd.Timestamp('2026-03-01 09:00', tz='UTC')
    first = ai.explain(account, snap, 'indicator', 'rsi_14', '1h', now=now, call=call)
    again = ai.explain(account, snap, 'indicator', 'rsi_14', '1h', now=now, call=call)
    assert not first['cached'] and again['cached'] and len(calls) == 1
    context, model = calls[0]
    assert model == settings.analysis_explain_model == 'claude-sonnet-5-5'
    assert context['indicator']['name'] == 'RSI 14' and set(context['readings']) == {'15m', '1h', '4h', '1d'}
    assert ai.cached(account, 'indicator', 'rsi_14', '1h')['explanation'] == 'RSI is neutral.'
    monkeypatch.setattr(settings, 'analysis_explain_per_hour', 1)
    with pytest.raises(ai.RateLimited):
        ai.explain(account, snap, 'strategy', 'T1', now=now, call=call)
    with pytest.raises(KeyError):
        ai._context(snap, 'indicator', 'no_such_indicator')


def test_every_kind_of_item_can_be_explained(snap):
    assert ai._context(snap, 'strategy', 'T1')['strategy']['name'] == 'Trend Rider'
    assert 'curve' not in json.dumps(ai._context(snap, 'strategy', 'X2'))
    assert ai._context(snap, 'strategy', 'PB09')['strategy']['id'] == 'PB09'
    chart = ai._context(snap, 'chart', '1h', '1h', ['rsi_14', 'EMA 21'])
    assert {r['name'] for r in chart['readings']} == {'RSI 14', 'Price vs EMA 21', 'EMA stack (9/21/50/200)'}
    news = dict(snap, headlines=dict(items=[dict(id='n1', title='T', source='s', available_at='x')],
                                     analyses=[dict(summary='a', sentiment='bullish', source_ids=['n1'])]))
    assert ai._context(news, 'news', 'n1')['ai_notes'][0]['summary'] == 'a'


# ---- switched off, published read-only ----------------------------------------------------------------------------------

def _supervisor():
    path = Path(__file__).resolve().parents[2]/'scripts'/'ops'/'data_supervisor.py'
    spec = importlib.util.spec_from_file_location('data_supervisor_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_supervisor_runs_no_trading_threads():
    sup = _supervisor()
    names = {j.__name__ for j in sup.JOBS}
    assert 'analysis' in names and sup.collect in sup.JOBS
    assert {'news_feeds', 'shadow_forecasts', 'forward_research', 'history'} <= names
    assert not names & {'paper', 'rule_engine', 'account_reviews', 'news_layer'}


def test_the_public_link_reads_analysis_but_cannot_call_claude():
    from quantpilot.api.public import READS
    assert {'analysis/snapshot', 'analysis/catalogue', 'analysis/briefs', 'analysis/explanations'} <= READS
    assert not {'analysis/explain', 'analysis/brief'} & READS


def test_explain_is_local_only_and_reports_a_missing_snapshot(account, monkeypatch):
    from fastapi.testclient import TestClient
    from quantpilot.api.app import app
    body = dict(kind='indicator', ref='rsi_14', timeframe='1h')
    with TestClient(app, client=('203.0.113.5', 5000)) as remote:
        assert remote.post('/api/v1/analysis/explain', json=body).status_code == 403
        assert remote.post('/api/v1/analysis/brief').status_code == 403
    with TestClient(app, client=('127.0.0.1', 5000)) as local:
        assert local.post('/api/v1/analysis/explain', json=body).status_code == 404     # no snapshot yet
        assert local.post('/api/v1/analysis/explain', json=dict(body, kind='other')).status_code == 422
        assert local.get('/api/v1/analysis/snapshot').json()['data'] is None
        assert local.get('/api/v1/analysis/catalogue').json()['data']['groups'][0] == 'Trend'


def test_the_archive_lists_every_past_account(account):
    from quantpilot.rules import journal
    now = pd.Timestamp('2026-11-02 10:00', tz='UTC')
    journal.save_account(account, 'acct-master', dict(status='RETIRED', retired_at=now.isoformat(), number=0,
                                                      futures=dict(equity=10_000.0, trades=[])), now)
    rows = journal.accounts(account)
    assert rows == [dict(id='acct-master', status='RETIRED', retired_at=now.isoformat(), equity=10_000.0, trades=0)]
