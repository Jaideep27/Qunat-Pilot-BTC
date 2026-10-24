"""Regression tests for the final-review findings on the outlook Q&A (one test per finding)."""

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from outlook_fixtures import ctx
from quantpilot.outlook import answers
from quantpilot.outlook import engine as outlook
from quantpilot.api import services
from quantpilot.outlook.questions import Question, extract_level

T0 = pd.Timestamp('2026-03-02T00:00', tz='UTC')


def touch(level, side=None, h=1, p=.2):
    question = dict(type='touch', horizon_hours=h, level=level)
    if side:
        question['side'] = side
    return dict(question=question, asked_at=T0.isoformat(), price=100.0, range=None,
                headline=dict(kind='probability', probability=p, base_rate=.1, method='m', confidence='weak'))


def flat_bars(hours=2):
    index = [T0+pd.Timedelta(minutes=5*i) for i in range(12*hours+1)]
    return pd.DataFrame(dict(close=100.0, high=100.5, low=99.5), index=index)


# Critical 1: touch answers without a side were scored against the low.
def test_touch_without_a_side_is_scored_against_the_level(account):
    answers.record(account, touch(120.0), 'mcp')
    answers.evaluate(account, flat_bars(), now=T0+pd.Timedelta(hours=2))
    assert answers.history(account)[0]['outcome']['hit'] == 0.0


def test_touch_questions_normalize_up_and_down_and_record_the_inferred_side():
    assert Question(type='touch', horizon_hours=24, level=120000, side='up').side == 'above'
    assert Question(type='touch', horizon_hours=24, level=90000, side='down').side == 'below'
    c = ctx()
    result = outlook.answer(Question(type='touch', horizon_hours=24, level=c.price*1.05), c)
    assert result['question']['side'] == 'above'


# Important 2: one missing state feature blanked the direction headline.
def test_missing_state_features_fall_back_to_the_base_rate_with_a_reason():
    c = ctx()
    c.panel.iloc[-1, c.panel.columns.get_loc('funding_rate')] = np.nan
    c.ranks = outlook.analogs.state_ranks(c.panel)
    result = outlook.answer(Question(type='direction', horizon_hours=4), c)
    assert result['headline']['probability'] is not None
    assert result['headline']['probability'] == result['headline']['base_rate']
    assert any('funding_rate' in w for w in result['warnings'])


# Important 3: hourly answers were labeled stale by the 10-minute envelope rule.
def test_outlook_envelopes_use_the_engines_staleness_rule(account, monkeypatch):
    def recent():
        # The envelope ages against the real clock, so end the synthetic panel 30 minutes before now.
        c = ctx()
        shift = pd.Timestamp.now(tz='UTC').floor('min')-pd.Timedelta(minutes=30)-c.panel.index[-1]
        c.panel.index, c.ranks.index = c.panel.index+shift, c.ranks.index+shift
        c.now = c.price_time = c.panel.index[-1]+pd.Timedelta(minutes=30)
        return c
    monkeypatch.setattr(services, '_context', recent)
    with TestClient(services_app(), client=('127.0.0.1', 5000)) as local:
        body = local.post('/api/v1/ask', json=dict(question='up or down next 4h', ai=False)).json()
    assert body['provenance']['freshness'] == 'fresh'


def services_app():
    from quantpilot.api.app import app
    return app


# Important 4: an old latest price was used without warning.
def test_old_prices_are_flagged_stale():
    c = ctx()
    c.price_time = c.now-pd.Timedelta(minutes=20)
    result = outlook.answer(Question(type='range', horizon_hours=24), c)
    assert result['stale'] is True and any('price' in w.lower() for w in result['warnings'])


# Important 5: a stale panel still used a promoted method.
def test_stale_panels_never_use_promoted_methods():
    c = ctx(report=dict(analogs={'4h': dict(promoted=True, relative_improvement=.05, windows=900)}))
    c.now = c.now+pd.Timedelta(hours=5)
    result = outlook.answer(Question(type='direction', horizon_hours=4), c)
    assert result['headline']['method'] == 'base_rate'


# Important 6: parsing spent Claude calls outside the budget; no limit on parallel AI calls; chat views counted.
def test_ai_parsing_counts_against_the_daily_budget(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    monkeypatch.setattr(services.settings, 'ask_daily_limit', 1)
    calls = []
    monkeypatch.setattr(services.ask_ai, 'parse', lambda *a: calls.append('parse'))
    monkeypatch.setattr(services.ask_ai, 'explain', lambda r: calls.append('explain') or dict(
        answer='x', ai_probability=.5, reasons=[], agrees_with_quant=True))
    with pytest.raises(ValueError):
        services.ask('write me a poem', ai=True)
    assert services.ask('up or down next 4h', ai=True)['ai']['status'] == 'daily_limit'
    with pytest.raises(ValueError):
        services.ask('another poem', ai=True)
    assert calls == ['parse']


def test_parallel_ai_asks_fall_back_to_quant_only(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    with services._ai_lock:
        result = services.ask('up or down next 4h', ai=True)
    assert result['ai']['status'] == 'busy' and result['headline']['probability'] is not None


def test_chat_ai_views_do_not_spend_the_ask_budget(account):
    answer_id = answers.record(account, touch(120.0, 'above'), 'chat', run_id='r1')
    answers.set_ai_view(account, answer_id, .3, ['x'], run_id='r1', now=T0)
    assert answers.ai_calls_today(account, T0, services.ASK_SOURCES) == 0


# Important 7: views could overwrite other answers or be added after the outcome was known.
def test_ai_views_without_a_run_are_limited_to_fresh_unviewed_mcp_answers(account):
    ask_id = answers.record(account, touch(120.0, 'above'), 'ask', ai=dict(status='ok', probability=.4))
    mcp_id = answers.record(account, touch(120.0, 'above'), 'mcp')
    assert answers.set_ai_view(account, ask_id, .9, ['x'], now=T0) is False
    assert answers.set_ai_view(account, mcp_id, .3, ['x'], now=T0) is True
    assert answers.set_ai_view(account, mcp_id, .9, ['x'], now=T0) is False          # already viewed
    late_id = answers.record(account, touch(120.0, 'above'), 'chat', run_id='r2')
    assert answers.set_ai_view(account, late_id, .3, ['x'], run_id='r2', now=T0+pd.Timedelta(hours=2)) is False


# Important 8: the news tool payload was too large for the agent.
def test_compact_news_view_drops_raw_runs(account):
    data = services.news(10, compact=True)['data']
    assert 'runs' not in data and 'feeds' not in data and 'analyses' in data and 'items' in data


# Important 10: years parsed as prices; implausible touch levels accepted.
def test_years_are_not_prices_and_implausible_levels_are_rejected():
    assert extract_level('In 2027 will BTC hit 150k?') == 150000.0
    c = ctx()
    with pytest.raises(ValueError, match='level'):
        outlook.answer(Question(type='touch', horizon_hours=24, level=70.0), c)
