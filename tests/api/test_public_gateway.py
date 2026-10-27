"""Public read-only gateway: the only door a tunnel may point at."""
import httpx
import pytest
from starlette.testclient import TestClient

from quantpilot.api import public


class Upstream:
    """Stands in for the local API and records every request that reaches it."""

    def __init__(self, status=200):
        self.calls, self.status = [], status

    def __call__(self, request):
        self.calls.append(str(request.url))
        return httpx.Response(self.status, json={'data': {'n': len(self.calls)}})


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now


@pytest.fixture
def dist(tmp_path):
    (tmp_path/'assets').mkdir()
    (tmp_path/'index.html').write_text('<!doctype html><html><head><title>QuantPilot</title></head><body></body></html>', encoding='utf-8')
    (tmp_path/'assets'/'app.js').write_text('console.log(1)', encoding='utf-8')
    (tmp_path.parent/'secret.txt').write_text('private', encoding='utf-8')
    return tmp_path


def client(dist, upstream, **options):
    app = public.create_app(dist, upstream='http://api.test', transport=httpx.MockTransport(upstream), **options)
    return TestClient(app)


def test_allowed_reads_reach_the_api_with_their_query(dist):
    upstream = Upstream()
    response = client(dist, upstream).get('/api/v1/rules/decisions?account=acct-a&limit=5')
    assert response.status_code == 200 and response.json() == {'data': {'n': 1}}
    assert upstream.calls == ['http://api.test/api/v1/rules/decisions?account=acct-a&limit=5']


@pytest.mark.parametrize('path', ['/api/v1/rules/accounts/acct-a/resume', '/api/v1/chat', '/api/v1/ask',
                                  '/api/v1/outlook', '/api/v1/answers/x/ai-view', '/api/v1/rules/accounts'])
def test_writes_are_refused_before_they_reach_the_api(dist, path):
    upstream = Upstream()
    web = client(dist, upstream)
    for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        assert web.request(method, path, json={}).status_code == 405
    assert upstream.calls == []


@pytest.mark.parametrize('path', ['/api/v1/chat/threads', '/api/v1/chat/threads/abc', '/api/v1/answers',
                                  '/docs', '/openapi.json', '/api/v1/rules/accounts/acct-a/resume', '/api/v1/unknown'])
def test_private_and_unlisted_reads_are_not_found(dist, path):
    upstream = Upstream()
    assert client(dist, upstream).get(path).status_code == 404
    assert upstream.calls == []


def test_reads_are_cached_briefly_so_visitors_cannot_load_the_laptop(dist):
    upstream, clock = Upstream(), Clock()
    web = client(dist, upstream, clock=clock, ttl=15)
    assert web.get('/api/v1/portfolio').json() == web.get('/api/v1/portfolio').json()
    assert len(upstream.calls) == 1
    clock.now += 16
    assert web.get('/api/v1/portfolio').json() == {'data': {'n': 2}}


def test_failed_reads_are_not_cached(dist):
    upstream = Upstream(status=503)
    web = client(dist, upstream)
    assert web.get('/api/v1/ready').status_code == 503 and web.get('/api/v1/ready').status_code == 503
    assert len(upstream.calls) == 2


def test_each_visitor_is_rate_limited_on_reads_that_reach_the_api(dist):
    clock = Clock()
    web = client(dist, Upstream(), clock=clock, per_minute=3)
    first = {'X-Forwarded-For': '203.0.113.7'}
    assert [web.get(f'/api/v1/candles?limit={n}', headers=first).status_code for n in range(4)] == [200, 200, 200, 429]
    assert web.get('/api/v1/candles?limit=9', headers={'X-Forwarded-For': '198.51.100.2'}).status_code == 200
    clock.now += 61
    assert web.get('/api/v1/candles?limit=8', headers=first).status_code == 200


def test_cached_reads_do_not_use_up_the_limit(dist):
    """Without a forwarded address every visitor shares one key, so only API calls may count against it."""
    web = client(dist, Upstream(), per_minute=2)
    assert {web.get('/api/v1/portfolio').status_code for _ in range(20)} == {200}


def test_an_unreachable_api_is_a_clean_503(dist):
    def down(request): raise httpx.ConnectError('refused')
    response = client(dist, down).get('/api/v1/health')
    assert response.status_code == 503 and 'detail' in response.json()


def test_the_dashboard_is_served_marked_public_with_safety_headers(dist):
    web = client(dist, Upstream())
    page = web.get('/')
    assert page.status_code == 200 and public.MARKER in page.text
    assert "frame-ancestors 'none'" in page.headers['content-security-policy']
    assert page.headers['x-content-type-options'] == 'nosniff'
    assert web.get('/assets/app.js').text == 'console.log(1)'
    assert web.get('/assets/../../secret.txt').status_code == 404
    assert web.get('/assets/%2e%2e/%2e%2e/secret.txt').status_code == 404
    assert web.get('/anything-else').status_code == 404
