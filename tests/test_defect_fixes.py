"""Regression tests for the seven defects verified on 2026-10-20 (one or more tests per defect)."""
import subprocess

import pandas as pd
import pytest

from outlook_fixtures import ctx
from quantpilot import answers, news, outlook, services
from quantpilot.questions import Question, rule_parse

T0 = pd.Timestamp('2026-03-02T00:00', tz='UTC')


def answer(type_='direction', h=1, p=.7, base=.5, price=100.0, **question):
    return dict(question=dict(type=type_, horizon_hours=h, **question), asked_at=T0.isoformat(), price=price,
                headline=dict(kind='probability', probability=p, base_rate=base, method='m', confidence='no_edge'),
                range=None)


def bars(closes, start=T0, highs=None, lows=None):
    index = [start+pd.Timedelta(minutes=5*i) for i in range(len(closes))]
    return pd.DataFrame(dict(close=closes, high=highs or closes, low=lows or closes), index=index)


# 1. Up/down intent is preserved through parsing, probability and scoring.
def test_down_questions_parse_as_down_and_up_or_down_stays_up():
    now = T0.to_pydatetime()
    assert rule_parse('Will BTC go down in the next 4 hours?', now, 100.0).side == 'down'
    assert rule_parse('Will BTC fall over the next 24h?', now, 100.0).side == 'down'
    assert rule_parse('Up or down next 4h?', now, 100.0).side == 'up'


def test_down_questions_get_the_probability_of_falling():
    c = ctx()
    up = outlook.answer(Question(type='direction', horizon_hours=4), c)
    down = outlook.answer(Question(type='direction', horizon_hours=4, side='down'), c)
    assert down['headline']['probability'] == pytest.approx(1-up['headline']['probability'])
    assert down['headline']['base_rate'] == pytest.approx(1-up['headline']['base_rate'])
    assert down['question']['side'] == 'down'


def test_down_answers_score_a_fall_as_a_hit(account):
    answers.record(account, answer(side='down', p=.6), 'ask')
    answers.evaluate(account, bars([100.0]+[99.0]*12), now=T0+pd.Timedelta(hours=2))
    assert answers.history(account)[0]['outcome']['hit'] == 1.0


# 2. The live price never comes from an unfinished or future candle.
def test_context_price_ignores_forming_and_future_candles(account):
    from quantpilot.market_store import PERP, MarketCandle
    from sqlalchemy.orm import Session
    now = pd.Timestamp('2026-10-20 12:00:30', tz='UTC')
    rows = [(now-pd.Timedelta(minutes=10), True, 100.0), (now-pd.Timedelta(minutes=5), False, 105.0),
            (now+pd.Timedelta(minutes=5), True, 110.0)]
    with Session(account) as session:
        for event, complete, close in rows:
            session.add(MarketCandle(market=PERP, interval='5m', event_time=event.to_pydatetime(),
                                     available_at=(event+pd.Timedelta(minutes=5)).to_pydatetime(),
                                     received_at=now.to_pydatetime(), source='fixture', open=close, high=close,
                                     low=close, close=close, volume=1.0, complete=complete))
        session.commit()
    context = outlook.load_context(now=now, engine=account)
    assert context.price == 100.0


# 3. Scoring uses the recorded forecast price and needs a complete window.
def test_scoring_uses_the_price_the_answer_was_given_at(account):
    answers.record(account, answer(price=101.5, p=.6), 'ask')         # answer was relative to 101.5
    answers.evaluate(account, bars([100.0]+[101.0]*12), now=T0+pd.Timedelta(hours=2))
    assert answers.history(account)[0]['outcome']['hit'] == 0.0        # 101 is below 101.5: not up


def test_windows_with_gaps_are_not_scored_until_complete_then_marked(account):
    answers.record(account, answer('touch', level=103.0, side='above', p=.3), 'ask')
    gappy = bars([100.0]*13).drop(index=[T0+pd.Timedelta(minutes=30)])   # one missing 5-minute bar
    assert answers.evaluate(account, gappy, now=T0+pd.Timedelta(hours=2)) == 0
    assert 'outcome' not in answers.history(account)[0]
    answers.evaluate(account, gappy, now=T0+pd.Timedelta(days=3))
    outcome = answers.history(account)[0]['outcome']
    assert outcome['unscored'] is True and outcome['reason'] == 'incomplete_data'


def test_old_scores_are_versioned_and_the_original_kept(account):
    answer_id = answers.record(account, answer(price=101.5, p=.6), 'ask')
    from quantpilot.answers import OutlookAnswer
    from sqlalchemy.orm import Session
    with Session(account) as session:
        row = session.get(OutlookAnswer, answer_id)
        row.payload = {**row.payload, 'outcome': dict(hit=1.0, evaluated_at=T0.isoformat())}   # version-1 score
        session.commit()
    answers.evaluate(account, bars([100.0]+[101.0]*12), now=T0+pd.Timedelta(hours=2))
    row = answers.history(account)[0]
    assert row['outcome']['hit'] == 0.0 and row['outcome']['scoring_version'] == answers.SCORING_VERSION
    assert row['previous_outcome']['hit'] == 1.0


# 4. Stale inputs never carry validated labels or validated methods.
def stale(c):
    c.now = c.now+pd.Timedelta(hours=5)
    return c


def test_stale_ranges_are_not_labelled_validated():
    report = dict(ranges={'24h': dict(promoted=True, coverage=.8)})
    result = outlook.answer(Question(type='range', horizon_hours=24), stale(ctx(report=report)))
    assert result['stale'] is True and result['headline']['confidence'] != 'validated'


def test_stale_touch_answers_fall_back_to_history_with_no_edge():
    report = dict(touch=dict(promoted=True, use_calibration=False))
    c = stale(ctx(report=report))
    result = outlook.answer(Question(type='touch', horizon_hours=24, level=c.price*1.03), c)
    assert result['headline']['method'] == 'historical_frequency' and result['headline']['confidence'] == 'no_edge'


def test_base_rate_headlines_are_labelled_no_edge_even_when_the_gate_passed():
    report = dict(analogs={'4h': dict(promoted=True, relative_improvement=.05, windows=900)})
    result = outlook.answer(Question(type='direction', horizon_hours=4), stale(ctx(report=report)))
    assert result['headline']['method'] == 'base_rate' and result['headline']['confidence'] == 'no_edge'


# 5. A Claude timeout falls back to the quant answer.
def test_claude_timeouts_become_a_timeout_error(monkeypatch):
    monkeypatch.setattr(news, 'claude_executable', lambda: 'claude.exe')
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd='claude', timeout=1)
    monkeypatch.setattr(news.subprocess, 'run', slow)
    with pytest.raises(TimeoutError):
        news.claude_json('system', 'prompt', {'type': 'object'})


def test_ask_survives_an_ai_timeout(account, monkeypatch):
    monkeypatch.setattr(services, '_context', lambda: ctx())
    def slow(result):
        raise TimeoutError('Claude Code timed out')
    monkeypatch.setattr(services.ask_ai, 'explain', slow)
    result = services.ask('up or down next 4h', ai=True)
    assert result['ai']['status'] == 'timeout' and result['headline']['probability'] is not None


# 6. Codex subprocesses never see any OpenAI or Azure OpenAI key.
def test_subscription_env_strips_every_openai_variable(monkeypatch):
    for name in ('OPENAI_API_KEY', 'OPENAI_ORG_ID', 'AZURE_OPENAI_API_KEY', 'CODEX_API_KEY', 'QUANTPILOT_X'):
        monkeypatch.setenv(name, 'secret')
    monkeypatch.setenv('CODEX_HOME', 'C:/codex')
    env = {k.upper(): v for k, v in news.subscription_env().items()}
    assert not {'OPENAI_API_KEY', 'OPENAI_ORG_ID', 'AZURE_OPENAI_API_KEY', 'CODEX_API_KEY', 'QUANTPILOT_X'} & set(env)
    assert env['CODEX_HOME'] == 'C:/codex'      # the saved login location is kept


# 7. The release smoke check accepts the tools added after the release.
def test_smoke_check_requires_the_release_tools_without_rejecting_later_ones():
    import importlib.util
    spec = importlib.util.spec_from_file_location('smoke', 'scripts/smoke_release.py')
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    listed = set(smoke.ROUTES) | {'get_strategy_research', 'ask_outlook'}
    assert smoke.missing_release_tools(listed) == set()
    assert smoke.missing_release_tools(listed-{'get_prediction'}) == {'get_prediction'}


def test_claude_prompts_carry_the_direction_side():
    from quantpilot import ask_ai
    assert 'side' in ask_ai.PARSE_SYSTEM.split('touch')[0]          # direction questions set side up/down
    assert "question's side" in ask_ai.EXPLAIN_SYSTEM                # Claude's own probability uses that side
