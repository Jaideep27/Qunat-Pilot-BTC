"""The strategy research verdict and forward-collection status, as served to the dashboard and MCP."""
import json

import pandas as pd
from fastapi.testclient import TestClient

from quantpilot import forward_data, services
from quantpilot.config import settings


def write_report(passing=()):
    folder = settings.artifact_dir/'strategy-research'/'run-1'
    folder.mkdir(parents=True)
    gate = dict(passed=False, criteria=dict(enough_calls=True, deflated_sharpe=False), deflated_sharpe=.01, trials=81)
    result = dict(id='M1', track='swing', kind='meta', configurations=18, pbo=.42, gate=gate,
                  report=dict(calls=295, coverage=.115, hit_rate=.583, wilson_low=.526, wilson_high=.638,
                              break_even=.516, mean_net=.0079, profit_factor=1.31))
    report = dict(generated='2026-10-20T15:00:00+00:00', development_end='2025-10-20', trials=dict(swing=81, intraday=105),
                  results=dict(M1=result), passing=list(passing), frozen={})
    (folder/'report.json').write_text(json.dumps(report), encoding='utf-8')
    return folder


def test_strategy_view_summarizes_the_latest_run(account):
    write_report()
    data = services.strategy_research()['data']
    row = data['families'][0]
    assert row['id'] == 'M1' and row['passed'] is False and row['failed'] == ['deflated_sharpe']
    assert row['hit_rate'] == .583 and row['calls'] == 295
    assert data['passing'] == [] and data['holdout_evaluated'] is False
    assert data['trials'] == dict(swing=81, intraday=105)
    assert data['next_test'] == '2027-04-20'


def test_strategy_view_reports_forward_collection(account):
    write_report()
    rows = pd.DataFrame([dict(event_time=pd.Timestamp('2026-10-20 12:00', tz='UTC'),
                              available_at=pd.Timestamp('2026-10-20 12:01', tz='UTC'),
                              received_at=pd.Timestamp('2026-10-20 12:01', tz='UTC'), source='x',
                              mid=1.0, spread_bps=.1, bid_qty_5bps=1.0, ask_qty_5bps=1.0, imbalance_5bps=0.0,
                              bid_qty_15bps=1.0, ask_qty_15bps=1.0, imbalance_15bps=0.0,
                              bid_reach_bps=17.0, ask_reach_bps=17.0)])
    forward_data.save(account, 'depth', rows)
    status = services.strategy_research()['data']['forward']
    assert status['depth']['rows'] == 1 and status['liquidations']['rows'] == 0


def test_strategy_view_without_a_run(account):
    data = services.strategy_research()['data']
    assert data['families'] == [] and data['status'] == 'not_run'


def test_strategy_endpoint(account):
    write_report()
    with TestClient(services_app()) as client:
        assert client.get('/api/v1/strategy-research').json()['data']['families'][0]['id'] == 'M1'


def services_app():
    from quantpilot.api import app
    return app
