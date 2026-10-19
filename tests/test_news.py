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
        sentiment='uncertain', novelty='new', entities=['BTC', 'CPI'],
        summary='Reported inflation and BTC movement coincide.',
        transmission=['rates'], evidence=[dict(source_id=i['id'], quote=i['payload']['title']) for i in items],
        uncertainties=['The headline does not establish causation.'])]}


# Verbatim stderr shape from codex-cli 0.163.0 when the ChatGPT allowance is exhausted.
LIMIT_STDERR = ('OpenAI Codex v0.163.0\n--------\nmodel: gpt-6-luna\n'
                'ERROR: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), '
                'visit https://chatgpt.com/settings/usage to purchase more credits or try again at 4:19 AM.\n')


def fake_cli(monkeypatch, returncode, stderr):
    monkeypatch.setattr(news.shutil, 'which', lambda _: 'codex')
    monkeypatch.setattr(news.subprocess, 'run', lambda args, **kw:
                        subprocess.CompletedProcess(args, returncode, stdout='', stderr=stderr))


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


def test_analysis_requires_novelty_classification():
    data = answer(sample())
    del data['events'][0]['novelty']
    with pytest.raises(ValueError):
        news.validate_batch(data, sample())


def test_cli_usage_limit_is_distinguished_from_other_failures(monkeypatch):
    fake_cli(monkeypatch, 1, LIMIT_STDERR)
    with pytest.raises(news.UsageLimitReached):
        news.invoke_codex([dict(id='x', cluster='c', payload={})])
    fake_cli(monkeypatch, 1, 'ERROR: model not found')
    with pytest.raises(RuntimeError) as other:
        news.invoke_codex([dict(id='x', cluster='c', payload={})])
    assert not isinstance(other.value, news.UsageLimitReached)


def test_usage_limit_pauses_analysis_without_spending_daily_budget(account, monkeypatch):
    clock = {'t': STAMP+timedelta(seconds=10)}
    monkeypatch.setattr(news, 'now', lambda: clock['t'])
    monkeypatch.setattr(settings, 'news_daily_batches', 1)
    news.persist_items(account, sample())
    def limited(items):
        raise news.UsageLimitReached('allowance exhausted')
    assert news.analyze(account, runner=limited, check_login=False)['status'] == 'usage_limited'
    assert news.snapshot(account)['analysis_status'] == 'delayed_usage_limit'
    clock['t'] += timedelta(minutes=30)
    assert news.analyze(account, runner=answer, check_login=False)['status'] == 'usage_limit_backoff'
    clock['t'] += timedelta(minutes=31)
    # The refused attempt never reached the model, so the one-batch budget is still available.
    assert news.analyze(account, runner=answer, check_login=False)['status'] == 'completed'
    assert news.snapshot(account)['analysis_status'] == 'current'


def test_forward_analysis_becomes_an_event_research_feature(account, monkeypatch):
    import pandas as pd
    from quantpilot.event_research import news_features
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(seconds=10))
    news.persist_items(account, sample())  # published 2h before collection: forward, not retrospective
    news.analyze(account, runner=answer, check_login=False)
    with Session(account) as session:
        runs = [dict(status=r.status, **r.payload) for r in session.scalars(select(news.NewsRun)).all()]
    assert runs[0]['retrospective'] is False
    cutoffs = pd.date_range(STAMP, periods=3, freq='5min', tz='UTC')
    features = news_features(cutoffs, runs)
    assert features['news_count_1h'].isna().iloc[0]  # analysis finished after the first cutoff
    assert features['news_count_1h'].iloc[1:].tolist() == [1.0, 1.0]


def test_retrospective_items_mark_their_analysis_retrospective(account, monkeypatch):
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(days=3))
    # First collected two days after publication, so a model may already know the outcome.
    news.persist_items(account, news.parse_feed(FEED, 'fixture', STAMP+timedelta(days=2)))
    news.analyze(account, runner=answer, check_login=False)
    with Session(account) as session:
        run = session.scalars(select(news.NewsRun)).one()
    assert run.payload['retrospective'] is True


def test_collection_cycles_space_out_reasoning(account, monkeypatch):
    clock = {'t': STAMP+timedelta(seconds=10)}
    monkeypatch.setattr(news, 'now', lambda: clock['t'])
    calls = []
    def analyzer(engine):
        calls.append(clock['t'])
        return {'status': 'idle'}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        first, last = news.cycle(account, True, None, client=client, analyzer=analyzer)
        clock['t'] += timedelta(minutes=5)
        second, last = news.cycle(account, True, last, client=client, analyzer=analyzer)
        clock['t'] += timedelta(minutes=11)
        third, last = news.cycle(account, True, last, client=client, analyzer=analyzer)
        quiet, _ = news.cycle(account, False, None, client=client, analyzer=analyzer)
    assert second['analysis'] == {'status': 'waiting_interval'}
    assert calls == [STAMP+timedelta(seconds=10), STAMP+timedelta(minutes=16, seconds=10)]
    assert 'analysis' not in quiet and len(first['feeds']) == len(news.FEEDS)
