import pandas as pd
import pytest

from quantpilot import answers

T0 = pd.Timestamp('2026-03-02T00:00', tz='UTC')


def result(type_='direction', h=1, p=.7, base=.5, **extra):
    return dict(question=dict(type=type_, horizon_hours=h, **extra), asked_at=T0.isoformat(), price=100.0,
                headline=dict(kind='probability', probability=p, base_rate=base, method='m', confidence='no_edge'),
                range=None)


def bars(path):
    index = [T0+pd.Timedelta(minutes=5*i) for i in range(len(path))]
    return pd.DataFrame(dict(close=[c for c, _, _ in path], high=[h for _, h, _ in path], low=[l for _, _, l in path]),
                        index=index)


def test_direction_is_scored_against_quant_ai_and_base(account):
    answer_id = answers.record(account, result(), 'ask', ai=dict(probability=.4, reasons=[], status='ok'))
    path = [(100, 100, 100)]+[(101, 101, 100)]*12   # one hour later the close is 101 -> up
    assert answers.evaluate(account, bars(path), now=T0+pd.Timedelta(hours=2)) == 1
    row = answers.history(account)[0]
    assert row['id'] == answer_id and row['outcome']['hit'] == 1.0
    assert row['outcome']['brier'] == pytest.approx(.09)
    assert row['outcome']['ai_brier'] == pytest.approx(.36)
    assert row['outcome']['base_brier'] == pytest.approx(.25)


def test_touch_and_big_move_use_highs_and_lows_inside_the_window(account):
    answers.record(account, result('touch', 1, .3, level=103.0, side='above'), 'ask')
    answers.record(account, result('big_move', 1, .1, threshold_pct=2.0, side='down'), 'ask')
    path = [(100, 100, 100)]+[(100, 103.5, 97.9)]+[(100, 100, 100)]*11
    answers.evaluate(account, bars(path), now=T0+pd.Timedelta(hours=2))
    outcomes = {r['question']['type']: r['outcome']['hit'] for r in answers.history(account)}
    assert outcomes == {'touch': 1.0, 'big_move': 1.0}


def test_answers_are_not_scored_before_their_horizon_ends(account):
    answers.record(account, result(h=4), 'ask')
    assert answers.evaluate(account, bars([(100, 100, 100)]*13), now=T0+pd.Timedelta(hours=1)) == 0


def test_scoreboard_withholds_verdicts_until_thirty_independent_answers(account):
    for i in range(40):
        r = result(p=.6)
        r['asked_at'] = (T0+pd.Timedelta(minutes=10*i)).isoformat()   # overlapping one-hour windows
        answers.record(account, r, 'scheduled')
    path = [(100+i % 2, 101, 99) for i in range(13*8)]
    answers.evaluate(account, bars(path), now=T0+pd.Timedelta(hours=12))
    board = answers.scoreboard(account)['direction:1h']
    assert board['scored'] == 40 and board['independent'] < 30 and board['verdict'] == 'too early to judge'


def test_ai_view_can_only_be_set_for_answers_from_the_same_run(account):
    answer_id = answers.record(account, result(), 'chat', thread_id='t1', run_id='run-a')
    assert answers.set_ai_view(account, answer_id, .55, ['x'], run_id='run-b', now=T0) is False
    assert answers.set_ai_view(account, answer_id, .55, ['x'], run_id='run-a', now=T0) is True
    assert answers.history(account)[0]['ai']['probability'] == .55


def test_scheduled_questions_run_hourly_with_a_daily_ai_cap(account, monkeypatch):
    from quantpilot.config import settings
    monkeypatch.setattr(settings, 'ask_scheduled_ai_per_day', 2)
    calls = []
    def ask_fn(text, ai, source):
        calls.append((text, ai))
        rid = answers.record(account, result(), source, ai=dict(status='ok' if ai else 'off'))
        return dict(id=rid)
    first = answers.scheduled_cycle(account, ask_fn, now=T0)
    again = answers.scheduled_cycle(account, ask_fn, now=T0+pd.Timedelta(minutes=20))   # same hour: skipped
    answers.scheduled_cycle(account, ask_fn, now=T0+pd.Timedelta(hours=1))   # next hour: asked again
    assert first['asked'] == len(answers.SCHEDULED) and again['asked'] == 0
    assert sum(ai for _, ai in calls) == 2   # cap holds across cycles in one UTC day
