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
