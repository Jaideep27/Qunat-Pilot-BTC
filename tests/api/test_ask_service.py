import pytest
from fastapi.testclient import TestClient

from outlook_fixtures import ctx
from quantpilot.api import services
from quantpilot.api.app import app


def test_ask_answers_quant_only_when_ai_is_off(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    result = services.ask('Will BTC go up in the next 4 hours?', ai=False)
    assert result['headline']['confidence'] == 'no_edge' and result['ai']['status'] == 'off'
    assert services.answers_view()['data']['history'][0]['id'] == result['id']


def test_unsupported_questions_raise_a_clear_error(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    monkeypatch.setattr(services.ask_ai, 'parse', lambda *a: None)
    with pytest.raises(ValueError, match='Supported'):
        services.ask('write me a poem', ai=True)


def test_answers_carry_their_live_forward_record(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    result = services.ask('up or down next 4h', ai=False)
    assert 'forward' in result['track_record']   # None until answers in this bucket have been scored


def test_ai_failure_falls_back_to_the_quant_answer(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    def limited(result):
        from quantpilot.ai.news import UsageLimitReached
        raise UsageLimitReached('limit')
    monkeypatch.setattr(services.ask_ai, 'explain', limited)
    result = services.ask('up or down next 1h', ai=True)
    assert result['ai']['status'] == 'usage_limited' and result['headline']['probability'] is not None


def test_write_routes_are_loopback_only_and_validated(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    with TestClient(app, client=('203.0.113.5', 5000)) as remote:
        assert remote.post('/api/v1/ask', json=dict(question='up next 4h', ai=False)).status_code == 403
    with TestClient(app, client=('127.0.0.1', 5000)) as local:
        assert local.post('/api/v1/ask', json=dict(question='x'*2001, ai=False)).status_code == 422
        response = local.post('/api/v1/ask', json=dict(question='up next 4h', ai=False))
        assert response.status_code == 200 and response.json()['data']['headline']['kind'] == 'probability'
        assert local.post('/api/v1/outlook', json=dict(type='direction', horizon_hours=300)).status_code == 422
