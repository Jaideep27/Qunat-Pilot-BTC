"""M4: article text via the isolated crawler, LLM classification of impact, NEWS_SHOCK events and the veto."""
import json

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from quantpilot import news, news_events

NOW = pd.Timestamp('2026-10-21 12:00', tz='UTC')
FEED = b'''<rss><channel><item><title>Major exchange hacked, bitcoin withdrawals halted</title>
<link>https://example.com/hack</link><description>Hackers drained hot wallets at a major crypto exchange.</description>
<pubDate>Wed, 21 Oct 2026 11:30:00 GMT</pubDate></item>
<item><title>Bitcoin ETF inflows continue</title><link>https://example.com/etf</link>
<description>Spot bitcoin ETF flows were positive.</description><pubDate>Wed, 21 Oct 2026 11:00:00 GMT</pubDate></item></channel></rss>'''


@pytest.fixture
def items(account):
    rows = news.parse_feed(FEED, 'fixture', NOW.to_pydatetime())
    news.persist_items(account, rows)
    return rows


def crawler(job):
    return [dict(url=u, ok=True, title='T', markdown='[Menu](https://x)\n\nHackers took 40,000 BTC.\n[Share](https://y)\n'
                 if 'hack' in u else 'Inflows of $300m.', error=None) for u in job['urls']]


def classifier(system, prompt, schema):
    article = json.loads(prompt)
    assert 'untrusted' in system.lower() and article['title']
    hack = 'hack' in article['title'].lower()
    return dict(impact='high' if hack else 'medium', category='hack' if hack else 'etf',
                direction='bearish' if hack else 'bullish', confidence=.85 if hack else .6,
                horizon='hours', summary='Exchange hack.' if hack else 'ETF inflows.')


def test_articles_are_fetched_once_and_cleaned(account, items):
    assert news_events.fetch_articles(account, runner=crawler, now=NOW) == 2
    assert news_events.fetch_articles(account, runner=crawler, now=NOW) == 0
    with Session(account) as session:
        text = session.get(news_events.NewsArticle, items[0]['id']).text
    assert text == 'Hackers took 40,000 BTC.'


def test_classification_is_validated_budgeted_and_available_only_when_made(account, items, monkeypatch):
    news_events.fetch_articles(account, runner=crawler, now=NOW)
    assert news_events.classify(account, runner=classifier, now=NOW) == 2
    assert news_events.classify(account, runner=classifier, now=NOW) == 0
    events = news_events.recent_events(account, now=NOW+pd.Timedelta(minutes=5))
    hack = next(e for e in events if e['category'] == 'hack')
    assert hack['impact'] == 'high' and hack['direction'] == 'bearish' and hack['available_at'] == NOW.isoformat()
    assert news_events.recent_events(account, now=NOW-pd.Timedelta(minutes=1)) == []
    assert news_events.recent_events(account, now=NOW+pd.Timedelta(hours=7)) == []


def test_bad_classifier_output_is_rejected(account, items):
    def broken(system, prompt, schema):
        return dict(impact='catastrophic', category='hack', direction='bearish', confidence=2, horizon='hours', summary='x')
    assert news_events.classify(account, runner=broken, now=NOW) == 0


def test_daily_budget(account, items, monkeypatch):
    monkeypatch.setattr(news_events.settings, 'news_event_daily_limit', 1)
    assert news_events.classify(account, runner=classifier, now=NOW) == 1
    assert news_events.classify(account, runner=classifier, now=NOW) == 0


def test_veto_blocks_longs_after_bearish_hack_news(account, items):
    news_events.classify(account, runner=classifier, now=NOW)
    check = news_events.live_check(account, now=lambda: NOW+pd.Timedelta(hours=1))
    assert check(dict(side='long'), {})['verdict'] == 'veto'
    assert check(dict(side='short'), {})['verdict'] == 'take'
    later = news_events.live_check(account, now=lambda: NOW+pd.Timedelta(hours=7))
    assert later(dict(side='long'), {})['verdict'] == 'take'


def test_crawler_subprocess_reads_the_marker_line(monkeypatch):
    class Done:
        returncode, stderr = 0, ''
        stdout = 'progress noise\nQP-JSON:[{"url": "https://a", "ok": true, "title": null, "markdown": "x", "error": null}]\n'
    seen = {}
    def fake_run(args, **kw):
        seen.update(args=args, kw=kw)
        return Done()
    monkeypatch.setattr(news_events.subprocess, 'run', fake_run)
    out = news_events.run_crawler(dict(urls=['https://a']))
    assert out[0]['ok'] and seen['args'][1] == '-I' and 'ANTHROPIC_API_KEY' not in seen['kw']['env']
    assert seen['kw']['env']['PYTHONIOENCODING'] == 'utf-8' and json.loads(seen['kw']['input'])['urls'] == ['https://a']
