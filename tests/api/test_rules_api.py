"""Rule engine read API: rulebook, live account, journal, research and chart overlays."""
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


def test_accounts_endpoint_lists_the_four_styles_with_rules_text(account):
    with TestClient(app) as client:
        data = client.get('/api/v1/rules/accounts').json()['data']
    assert [a['id'] for a in data['accounts']] == ['acct-trend', 'acct-range', 'acct-breakout', 'acct-carry']
    assert [a['style'] for a in data['accounts']] == ['T1', 'R1', 'B1', 'C1']
    a = data['accounts'][1]
    assert a['name'] == 'Range Trader' and a['status'] == 'NOT_STARTED' and a['equity'] == 10_000 and a['round_length'] == 30
    assert any('sideways' in line for line in a['rules']) and any('Paper money' in line for line in a['rules'])
    assert a['leverage'] == 2 and a['risk'] == .01 and 'history_test' in a and 'active' in a
    assert a['report']['trades'] == 0 and a['report']['win_rate'] is None and 'max_drawdown' in a['report']
    carry = data['accounts'][3]
    assert carry['round_unit'] == 'days' and carry['round_length'] == 30
    assert data['config_hash'] and data['retired'] and data['retired'][0]['id'] == 'acct-a'


def test_an_open_style_position_shows_its_time_machine_and_a_stop_only_trend(account):
    from quantpilot.rules import journal
    hit = 'T1:2026-11-01T00:00:00+00:00'
    journal.save_account(account, 'acct-trend', dict(
        status='RUNNING', round=1, in_round=0, number=0, style='T1', active=dict(on=True, reason='On: up'),
        extra={hit: dict(time_machine=dict(stop_first=.3, target_first=0.0, neither=.7, median_r=.2))},
        futures=dict(equity=10_100.0, position=dict(side=1, quantity=.1, entry=80_000.0, stop=74_000.0, target=None,
                                                    leverage=2.0, liquidation=40_000.0, entry_time='2026-11-01T00:01:00+00:00',
                                                    plan=dict(playbook='T1', hit_id=hit, style='T1')))),
        pd.Timestamp.now(tz='UTC'))
    with TestClient(app) as client:
        a = client.get('/api/v1/rules/accounts').json()['data']['accounts'][0]
    assert a['position']['target'] is None and a['position']['time_machine']['stop_first'] == .3
    assert a['active']['reason'] == 'On: up'


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
