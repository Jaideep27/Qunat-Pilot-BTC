"""Readiness of the analysis app: the database answers, a completed candle is fresh, and the snapshot is reported."""
import pandas as pd
from fastapi.testclient import TestClient
from quantpilot.api import services
from quantpilot.api.app import app
from quantpilot.monitoring.operations import report
from quantpilot.storage.market import feed_status, save_frame


def candle(engine, at):
    bar = at.floor('5min')-pd.Timedelta(minutes=5)
    save_frame(engine, 'candles', pd.DataFrame([dict(event_time=bar, available_at=bar+pd.Timedelta(minutes=5),
                                                     received_at=at, source='fixture', open=100, high=102, low=99,
                                                     close=101, volume=1)]))


def test_ready_needs_a_fresh_candle_and_reports_the_snapshot(account):
    now = pd.Timestamp.now(tz='UTC')
    assert not report(account, now)['ready']                       # no candles at all
    candle(account, now)
    status = report(account, now)
    assert status['ready'] and status['checks']['completed_candles']['status'] == 'fresh'
    assert status['checks']['analysis_snapshot']['status'] == 'missing'   # informational only
    feed_status(account, 'analysis:snapshot', dict(status='healthy', built_at=now.isoformat()))
    assert report(account, now)['checks']['analysis_snapshot']['status'] == 'fresh'
    later = report(account, now+pd.Timedelta(hours=1))
    assert not later['ready'] and later['checks']['completed_candles']['status'] == 'stale'
    assert later['checks']['analysis_snapshot']['status'] == 'stale'


def test_the_ready_route_answers_200_or_503(account, monkeypatch):
    now = pd.Timestamp.now(tz='UTC')
    candle(account, now)
    with TestClient(app) as client:
        assert client.get('/api/v1/ready').status_code == 200
        assert client.post('/api/v1/operations').status_code == 405
        assert client.get('/api/v1/health').json()['data']['status'] == 'ok'
    monkeypatch.setattr(services, 'operations', lambda: services.envelope(report(account, now+pd.Timedelta(hours=1))))
    with TestClient(app) as client:
        assert client.get('/api/v1/ready').status_code == 503
