import asyncio

import httpx
from fastapi.testclient import TestClient

from quantpilot import mcp_proxy, services
from quantpilot.api import app


def test_proxy_tools_return_the_same_envelopes_as_the_api(account, monkeypatch):
    client = TestClient(app, client=('127.0.0.1', 5000))
    monkeypatch.setattr(mcp_proxy, 'client_factory', lambda: client)
    assert asyncio.run(mcp_proxy.get_paper_portfolio())['data'] == services.paper()['data']
    assert asyncio.run(mcp_proxy.get_macro_calendar())['data'] == services.macro()['data']
    assert asyncio.run(mcp_proxy.get_rule_engine())['data'] == services.rules_status()['data']


def test_chat_run_identity_is_forwarded(account, monkeypatch):
    seen = {}
    def handler(request):
        seen['body'] = request.content.decode()
        return httpx.Response(200, json=dict(data=dict(id='a1')))
    monkeypatch.setattr(mcp_proxy, 'client_factory',
                        lambda: httpx.Client(base_url='http://x', transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('QP_CHAT_THREAD', 't1')
    monkeypatch.setenv('QP_CHAT_RUN', 'run-1')
    asyncio.run(mcp_proxy.ask_outlook(type='direction', horizon_hours=4))
    assert '"run_id":"run-1"' in seen['body'].replace(' ', '') and '"thread_id":"t1"' in seen['body'].replace(' ', '')


def test_api_down_returns_a_clear_error_the_agent_can_relay(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError('refused', request=request)
    monkeypatch.setattr(mcp_proxy, 'client_factory',
                        lambda: httpx.Client(base_url='http://x', transport=httpx.MockTransport(refuse)))
    result = asyncio.run(mcp_proxy.get_sentiment())
    assert result['error'] == 'QuantPilot services are not running; start them with node scripts/start.cjs'
