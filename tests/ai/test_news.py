from datetime import datetime, timezone, timedelta
import json
import subprocess

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from quantpilot.ai import news
from quantpilot.api import services
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
    from quantpilot.api.app import app
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
    from quantpilot.research.events import news_features
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
    monkeypatch.setattr(settings, 'news_reasoners', ['claude', 'codex'])
    def analyzer(engine, provider):
        calls.append((clock['t'], provider))
        return {'status': 'idle'}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        first, last = news.cycle(account, True, None, client=client, analyzer=analyzer)
        clock['t'] += timedelta(minutes=5)
        second, last = news.cycle(account, True, last, client=client, analyzer=analyzer)
        clock['t'] += timedelta(minutes=11)
        third, last = news.cycle(account, True, last, client=client, analyzer=analyzer)
        quiet, _ = news.cycle(account, False, None, client=client, analyzer=analyzer)
    assert second['analysis'] == {'status': 'waiting_interval'}
    first_at, later_at = STAMP+timedelta(seconds=10), STAMP+timedelta(minutes=16, seconds=10)
    assert calls == [(first_at, 'claude'), (first_at, 'codex'), (later_at, 'claude'), (later_at, 'codex')]
    assert set(third['analysis']) == {'claude', 'codex'}
    assert 'analysis' not in quiet and len(first['feeds']) == len(news.FEEDS)



def claude_output(**fields):
    base = dict(type='result', subtype='success', is_error=False, result='', structured_output=None)
    return json.dumps({**base, **fields})


def fake_claude(monkeypatch, returncode, stdout, seen=None):
    monkeypatch.setattr(news, 'claude_executable', lambda: 'claude.exe')
    def run(args, **kwargs):
        if seen is not None:
            seen.update(args=args, input=kwargs.get('input'), env=kwargs.get('env'), cwd=kwargs.get('cwd'))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr='')
    monkeypatch.setattr(news.subprocess, 'run', run)


def test_claude_reasoner_runs_isolated_without_tools_and_validates_output(monkeypatch):
    items = sample()
    seen = {}
    fake_claude(monkeypatch, 0, claude_output(structured_output=answer(items)), seen)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'never-pass')
    assert news.invoke_claude(items) == answer(items)
    args = seen['args']
    assert args[args.index('--model')+1] == settings.news_claude_model
    assert args[args.index('--tools')+1] == '' and '--strict-mcp-config' in args
    assert '--no-session-persistence' in args and '--disable-slash-commands' in args
    assert items[0]['payload']['title'] in seen['input']  # untrusted text only via stdin
    assert not any(items[0]['payload']['title'] in a for a in args)
    assert 'ANTHROPIC_API_KEY' not in seen['env']


def test_claude_output_with_invented_evidence_is_rejected(monkeypatch):
    items = sample()
    bad = answer(items)
    bad['events'][0]['evidence'][0]['quote'] = 'Bitcoin will crash 50 percent'
    fake_claude(monkeypatch, 0, claude_output(structured_output=bad))
    with pytest.raises(ValueError, match='quotation'):
        news.invoke_claude(items)


def test_claude_usage_limit_is_distinguished_from_other_failures(monkeypatch):
    fake_claude(monkeypatch, 1, claude_output(is_error=True, subtype='error',
                                              result='Claude AI usage limit reached|1792483200'))
    with pytest.raises(news.UsageLimitReached):
        news.invoke_claude(sample())
    fake_claude(monkeypatch, 1, claude_output(is_error=True, subtype='error', result='Invalid model name'))
    with pytest.raises(RuntimeError) as other:
        news.invoke_claude(sample())
    assert not isinstance(other.value, news.UsageLimitReached)


def test_claude_environment_drops_api_keys_and_parent_session(monkeypatch):
    for name in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDECODE',
                 'CLAUDE_CODE_SESSION_ID', 'QUANTPILOT_DATABASE_URL'):
        monkeypatch.setenv(name, 'x')
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', 'keep-me')
    env = news.claude_env()
    assert not {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDECODE',
                'CLAUDE_CODE_SESSION_ID', 'QUANTPILOT_DATABASE_URL'} & set(env)
    assert env['CLAUDE_CONFIG_DIR'] == 'keep-me' and 'PATH' in {k.upper() for k in env}


def test_claude_executable_avoids_cmd_shims(monkeypatch, tmp_path):
    shim = tmp_path/'claude.CMD'
    shim.write_text('@echo off')
    monkeypatch.setattr(news.shutil, 'which', lambda _: str(shim))
    assert news.claude_executable() is None  # JSON arguments through cmd.exe are not safe
    native = tmp_path/'node_modules'/'@anthropic-ai'/'claude-code'/'bin'/'claude.exe'
    native.parent.mkdir(parents=True)
    native.write_bytes(b'')
    assert news.claude_executable() == str(native)


def test_claude_requires_a_subscription_login(monkeypatch):
    monkeypatch.setattr(news, 'claude_executable', lambda: 'claude.exe')
    def status(method):
        return lambda args, **kw: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(dict(loggedIn=True, authMethod=method)), stderr='')
    monkeypatch.setattr(news.subprocess, 'run', status('claude.ai'))
    assert news.claude_status()['status'] == 'ready'
    monkeypatch.setattr(news.subprocess, 'run', status('api_key'))
    assert news.claude_status()['status'] == 'subscription_login_required'


def test_reasoners_track_work_budgets_and_limits_independently(account, monkeypatch):
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(seconds=10))
    news.persist_items(account, sample())
    def limited(items):
        raise news.UsageLimitReached('claude allowance exhausted')
    assert news.analyze(account, runner=limited, check_login=False, provider='claude')['status'] == 'usage_limited'
    # A Claude limit never blocks Codex, and each provider analyzes the same items itself.
    assert news.analyze(account, runner=answer, check_login=False, provider='codex')['status'] == 'completed'
    assert news.analyze(account, runner=answer, check_login=False, provider='codex')['status'] == 'idle'
    state = news.snapshot(account)
    assert state['providers']['claude']['analysis_status'] == 'delayed_usage_limit'
    assert state['providers']['codex']['analysis_status'] == 'current'
    assert {r['provider'] for r in state['runs']} == {'claude', 'codex'}
    assert state['providers']['claude']['model'] == settings.news_claude_model


def test_research_features_use_one_reasoner_so_items_are_not_double_counted(account, monkeypatch):
    import pandas as pd
    from quantpilot.research.events import news_features
    monkeypatch.setattr(news, 'now', lambda: STAMP+timedelta(seconds=10))
    news.persist_items(account, sample())
    for provider in ('claude', 'codex'):
        news.analyze(account, runner=answer, check_login=False, provider=provider)
    with Session(account) as session:
        runs = [dict(status=r.status, **r.payload) for r in session.scalars(select(news.NewsRun)).all()]
    cutoffs = pd.date_range(STAMP+timedelta(minutes=5), periods=1, freq='5min', tz='UTC')
    assert news_features(cutoffs, runs, provider='claude')['news_count_1h'].tolist() == [1.0]
