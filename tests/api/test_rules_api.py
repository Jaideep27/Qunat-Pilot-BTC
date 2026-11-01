"""Rule engine read API: rulebook, live account, journal, research and chart overlays."""
import pytest
from fastapi.testclient import TestClient

import pandas as pd

from agent_fixtures import START, frames as five_minute
from rules.test_rule_live import book, minute_bars
from quantpilot.rules import live as rule_live
from quantpilot.api.app import app


def test_rules_endpoints_before_the_engine_has_run(account):
    with TestClient(app) as client:
        body = client.get('/api/v1/rules').json()
        data = body['data']
        assert len(data['playbooks']) >= 20 and data['rulebook']['hash'] and data['account'] is None
        assert {'ml', 'news', 'learner', 'manage_rl'} <= set(data['layers'])
        assert client.get('/api/v1/rules/journal').json()['data'] == dict(hits=[], trades=[], triggers=[])
        assert client.get('/api/v1/rules/research').json()['data']['status'] == 'not_run'
        assert client.get('/api/v1/rules/levels').json()['data']['overlay'] is None


def test_rules_endpoints_after_cycles(account):
    data = five_minute(days=42)
    fast = minute_bars(data['candles'])
    t15 = START+pd.Timedelta(days=41, hours=3)
    for minutes in (0, 1, 80):
        rule_live.cycle(account, t15+pd.Timedelta(minutes=minutes, seconds=20), book=book(), frames=data, fast=fast,
                        macro_events=[], news_events=[])
    with TestClient(app) as client:
        status = client.get('/api/v1/rules').json()['data']
        assert status['account']['equity'] > 0 and status['account']['trades'] >= 1
        assert status['checklist'] and status['provenance_note']
        journal = client.get('/api/v1/rules/journal?limit=50').json()['data']
        assert journal['hits'] and journal['trades'] and journal['triggers']
        assert 'checks' not in journal['hits'][0]['payload']                 # compact by default
        levels = client.get('/api/v1/rules/levels').json()['data']
        assert levels['overlay']['vwap'] > 0 and levels['markers']


def test_the_accounts_endpoint_shows_one_master_bot_with_its_advisors(account):
    with TestClient(app) as client:
        data = client.get('/api/v1/rules/accounts').json()['data']
    assert [a['id'] for a in data['accounts']] == ['acct-master']
    m = data['accounts'][0]
    assert m['name'] == 'Master Bot' and m['style'] == 'X2' and m['status'] == 'NOT_STARTED' and m['equity'] == 10_000
    assert any('Trend Rider decides the direction' in line for line in m['rules'])
    assert not any('idle money' in line.lower() for line in m['rules'])                     # A13: parking is off
    assert any('Paper money' in line for line in m['rules']) and 'history_test' in m and 'members' in m
    assert m['report']['trades'] == 0 and m['report']['win_rate'] is None
    assert len(data['retired']) == 10 and {r['id'] for r in data['retired']} >= {'acct-carry', 'acct-combined', 'acct-a'}


def test_the_master_shows_its_open_trade_with_the_time_machine_and_its_advisors(account):
    from quantpilot.rules import journal
    from quantpilot.strategies import bot
    hit = 'X2:2026-11-01T00:00:00+00:00'
    st = bot.new(10_000.0, now=pd.Timestamp('2026-11-01', tz='UTC'))
    st['futures']['position'] = dict(side=1, quantity=.1, entry=80_000.0, stop=74_000.0, target=None, leverage=2.0, liquidation=40_000.0,
                                     entry_time='2026-11-01T00:01:00+00:00', plan=dict(playbook='T1', hit_id=hit, style='X2'))
    journal.save_account(account, 'acct-master', dict(
        st, status='RUNNING', round=1, in_round=0, number=0, style='X2', active=dict(on=True, reason='On: the daily trend is up'),
        members=dict(regime='TREND_UP', vote=1, leaders=['T1'], hedge=False, funding='Funding about +8.8% a year (buyers pay sellers).', volume=1.4),
        extra={hit: dict(time_machine=dict(stop_first=.3, target_first=0.0, neither=.7, median_r=.2), reason='T1: timed by PB08L')}),
        pd.Timestamp.now(tz='UTC'))
    with TestClient(app) as client:
        m = client.get('/api/v1/rules/accounts').json()['data']['accounts'][0]
    assert m['position']['target'] is None and m['position']['time_machine']['stop_first'] == .3
    assert m['members']['funding'].startswith('Funding about') and m['members']['volume'] == 1.4


def test_decisions_and_trades_filter_by_account(account):
    from quantpilot.accounts import journal as aj
    t = pd.Timestamp('2026-10-22 10:00', tz='UTC')
    aj.add_decisions(account, [dict(account=k, hit_id='PB01:x', time=t, decision='BELOW_CUTOFF', score=.1, confidence=.5,
                                    cutoff=.2, payload=dict(ticks=[])) for k in ('acct-a', 'acct-b')])
    with TestClient(app) as client:
        rows = client.get('/api/v1/rules/decisions?account=acct-b').json()['data']['decisions']
        assert [r['account'] for r in rows] == ['acct-b']
        assert client.get('/api/v1/rules/account-trades?account=acct-b').json()['data']['trades'] == []
        assert client.get('/api/v1/rules/reviews?account=acct-b').json()['data']['reviews'] == []


def test_resume_rejects_remote_callers_and_accounts_that_are_not_paused(account):
    from quantpilot.rules import journal
    with TestClient(app, client=('203.0.113.5', 5000)) as remote:
        assert remote.post('/api/v1/rules/accounts/acct-a/resume').status_code == 403
    with TestClient(app, client=('127.0.0.1', 5000)) as local:
        assert local.post('/api/v1/rules/accounts/acct-a/resume').status_code == 409
        journal.save_account(account, 'acct-a', dict(status='PAUSED_FOR_REVIEW', round=1, in_round=30, number=30,
                                                     futures=dict(paused=True)), pd.Timestamp.now(tz='UTC'))
        body = local.post('/api/v1/rules/accounts/acct-a/resume').json()
        assert body['data']['round'] == 2


def test_model_health_endpoint(account):
    with TestClient(app) as client:
        body = client.get('/api/v1/rules/model-health').json()
    assert body['data']['overall'] in ('OK', 'WATCH', 'ALARM') and body['data']['reasons']
    assert {'card', 'calibration', 'drift', 'data_checks', 'challenger'} <= set(body['data'])

