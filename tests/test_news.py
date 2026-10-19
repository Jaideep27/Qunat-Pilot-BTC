from datetime import datetime, timezone, timedelta
import json
import subprocess

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantpilot import news, services
from quantpilot.config import settings

STAMP = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
FEED = b'''<rss><channel><item><title>Bitcoin falls as CPI rises</title>
<link>https://example.com/bitcoin</link><description>Inflation rises; rates uncertain.</description>
<pubDate>Tue, 20 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'''


def sample():
    return news.parse_feed(FEED, 'fixture', STAMP)


def answer(items):
    return {'events': [dict(source_ids=[i['id'] for i in items], topic='macro', relevance='direct',
        sentiment='uncertain', summary='Reported inflation and BTC movement coincide.',
        transmission=['rates'], evidence=[dict(source_id=i['id'], quote=i['payload']['title']) for i in items],
        uncertainties=['The headline does not establish causation.'])]}


def test_revisions_and_availability_are_immutable(account):
    rows = sample()
    assert rows[0]['available_at'] == STAMP.isoformat()
    assert news.persist_items(account, rows) == 1
    assert news.persist_items(account, news.parse_feed(FEED, 'fixture', STAMP+timedelta(hours=1))) == 0
    changed = news.parse_feed(FEED.replace(b'falls', b'rebounds'), 'fixture', STAMP+timedelta(hours=2))
    assert news.persist_items(account, changed) == 1
    with Session(account) as session:
        assert len(session.scalars(select(news.NewsItem)).all()) == 2
        assert session.get(news.NewsItem, rows[0]['id']).available_at == STAMP.isoformat()


def test_rejects_unsafe_feeds_and_unknown_or_invented_evidence():
    with pytest.raises(ValueError):
        news.parse_feed(b'<!DOCTYPE rss><rss/>', 'fixture', STAMP)
    data = answer(sample())
    assert news.validate_batch(data, sample()) == data
    data['events'][0]['evidence'][0]['quote'] = 'CPI caused a guaranteed 5 percent crash'
    with pytest.raises(ValueError, match='quotation'):
        news.validate_batch(data, sample())
    data = answer(sample())
    data['events'][0]['source_ids'] = ['invented']
    with pytest.raises(ValueError, match='unknown'):
        news.validate_batch(data, sample())


def test_reasoning_budget_caching_and_completion_time(account, monkeypatch):
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(seconds=10))
    monkeypatch.setattr(settings, 'news_daily_batches', 1)
    news.persist_items(account, sample())
    result = news.analyze(account, runner=answer, check_login=False)
    assert result['status'] == 'completed'
    with Session(account) as session:
        run = session.get(news.NewsRun, result['run_id'])
        assert run.payload['available_at'] > sample()[0]['available_at']
        assert run.payload['prompt_version'] == news.PROMPT_VERSION
    assert news.analyze(account, runner=answer, check_login=False)['status'] == 'daily_budget_reached'
    monkeypatch.setattr(settings, 'news_daily_batches', 48)
    assert news.analyze(account, runner=answer, check_login=False)['status'] == 'idle'


def test_failed_jobs_do_not_publish_sentiment(account, monkeypatch):
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(seconds=10))
    news.persist_items(account, sample())
    def fail(items):
        raise subprocess.TimeoutExpired('codex', 1)
    assert news.analyze(account, runner=fail, check_login=False)['status'] == 'failed'
    state = news.snapshot(account)
    assert 'result' not in state['runs'][0]
    assert state['forecasting_status'] == 'shadow_only_no_policy_changes'


def test_feed_failure_is_independent_and_recorded(account):
    def respond(request):
        return httpx.Response(200, content=FEED) if 'federalreserve' in str(request.url) else httpx.Response(503)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        results = news.collect(account, client)
    assert results[0]['status'] == 'ok'
    assert results[1]['status'] == 'unavailable'
    assert news.snapshot(account)['total_items'] == 1


def test_no_paid_api_environment_or_fallback(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'never-pass')
    monkeypatch.setenv('CODEX_API_KEY', 'never-pass')
    monkeypatch.setenv('QUANTPILOT_DATABASE_URL', 'never-pass')
    assert not any(key in news.subscription_env() for key in
                   ('OPENAI_API_KEY', 'CODEX_API_KEY', 'QUANTPILOT_DATABASE_URL'))
    monkeypatch.setattr(news.shutil, 'which', lambda _: None)
    assert news.codex_status()['status'] == 'cli_missing'


def test_news_api_shares_service_and_rejects_invalid_bounds(account):
    from fastapi.testclient import TestClient
    from quantpilot.api import app
    with TestClient(app) as client:
        assert client.get('/api/v1/news').json()['data'] == services.news()['data']
        assert client.get('/api/v1/news?limit=101').status_code == 422
        assert client.post('/api/v1/news').status_code == 405
