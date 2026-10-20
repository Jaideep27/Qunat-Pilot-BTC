"""API payloads carry what the dashboard and agents use; full detail stays available on request."""
import json

from fastapi.testclient import TestClient

from quantpilot import services
from quantpilot.config import settings


def write_evaluation():
    curve = [dict(time=f'2026-08-01T{h % 24:02d}:00:00+00:00', equity=10000+h, exposure=0.0) for h in range(2000)]
    policy = [dict(test_start='2026-01-01', kind='xgboost', threshold=.6, net_return=.01, max_drawdown=-.02, trades=3,
                   curve=curve[:500], fills=[dict(price=1.0)]*50)]
    comparison = dict(group='full', kind='xgboost', threshold=.6, walk_forward=[], walk_forward_policy=policy,
                      holdout=dict(brier=.25, calibration=[]), trading=dict(net_return=0.0, curve=curve, fills=[]))
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    (settings.artifact_dir/'evaluation.json').write_text(json.dumps(dict(status='released', comparisons=[comparison],
                                                                         benchmarks={})), encoding='utf-8')
    return curve


def test_backtests_drop_per_fold_detail_and_thin_curves_by_default(account):
    curve = write_evaluation()
    data = services.research()['data']['comparisons'][0]
    assert 'curve' not in data['walk_forward_policy'][0] and 'fills' not in data['walk_forward_policy'][0]
    assert data['walk_forward_policy'][0]['net_return'] == .01
    assert len(data['trading']['curve']) <= services.MAX_CURVE_POINTS
    assert data['trading']['curve'][0] == curve[0] and data['trading']['curve'][-1] == curve[-1]


def test_full_detail_on_request(account):
    curve = write_evaluation()
    data = services.research(detail=True)['data']['comparisons'][0]
    assert len(data['trading']['curve']) == len(curve) and 'curve' in data['walk_forward_policy'][0]
    with TestClient(services_app()) as client:
        assert len(client.get('/api/v1/backtests?detail=true').json()['data']['comparisons'][0]['trading']['curve']) == 2000
        assert len(client.get('/api/v1/backtests').json()['data']['comparisons'][0]['trading']['curve']) <= 400


def services_app():
    from quantpilot.api import app
    return app


def test_benchmarks_are_summarized_too(account):
    write_evaluation()
    path = settings.artifact_dir/'evaluation.json'
    result = json.loads(path.read_text(encoding='utf-8'))
    curve = [dict(time=str(i), equity=10000.0+i) for i in range(1500)]
    result['benchmarks'] = dict(buy_hold=dict(net_return=.3, max_drawdown=-.09, curve=curve, fills=[dict(price=1.0)]*900))
    path.write_text(json.dumps(result), encoding='utf-8')
    bench = services.research()['data']['benchmarks']['buy_hold']
    assert bench['net_return'] == .3 and bench['fills'] == 900 and len(bench['curve']) <= services.MAX_CURVE_POINTS
    full = services.research(detail=True)['data']['benchmarks']['buy_hold']
    assert len(full['fills']) == 900
