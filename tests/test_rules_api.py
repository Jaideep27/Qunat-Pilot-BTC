"""Rule engine read API: rulebook, live account, journal, research and chart overlays."""
from fastapi.testclient import TestClient

import pandas as pd

from agent_fixtures import START, frames as five_minute
from test_rule_live import book, minute_bars
from quantpilot import rule_live
from quantpilot.api import app


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


def test_accounts_endpoint_lists_four_with_rules_text(account):
    with TestClient(app) as client:
        data = client.get('/api/v1/rules/accounts').json()['data']
    assert [a['id'] for a in data['accounts']] == ['acct-a', 'acct-b', 'acct-c', 'acct-d']
    a = data['accounts'][0]
    assert a['status'] == 'NOT_STARTED' and a['equity'] == 10_000 and a['in_round'] == 0 and a['round_length'] == 30
    assert any('Leverage 2×' in line for line in a['rules']) and a['watches'] and a['leverage'] == 2
    assert data['config_hash'] and 'expectations' in a
    assert a['report']['trades'] == 0 and a['report']['win_rate'] is None and 'max_drawdown' in a['report']


def test_decisions_and_trades_filter_by_account(account):
    from quantpilot import account_journal as aj
    t = pd.Timestamp('2026-10-22 10:00', tz='UTC')
    aj.add_decisions(account, [dict(account=k, hit_id='PB01:x', time=t, decision='BELOW_CUTOFF', score=.1, confidence=.5,
                                    cutoff=.2, payload=dict(ticks=[])) for k in ('acct-a', 'acct-b')])
    with TestClient(app) as client:
        rows = client.get('/api/v1/rules/decisions?account=acct-b').json()['data']['decisions']
        assert [r['account'] for r in rows] == ['acct-b']
        assert client.get('/api/v1/rules/account-trades?account=acct-b').json()['data']['trades'] == []
        assert client.get('/api/v1/rules/reviews?account=acct-b').json()['data']['reviews'] == []


def test_resume_rejects_remote_callers_and_accounts_that_are_not_paused(account):
    from quantpilot import journal
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
