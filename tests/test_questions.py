from datetime import datetime, timezone

import pytest

from quantpilot.questions import Question, extract_level, resolve_horizon, rule_parse

NOW = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)  # a Tuesday


@pytest.mark.parametrize('text,expected', [
    ('Will BTC go up in the next 4 hours?', dict(type='direction', horizon_hours=4)),
    ('up or down next 1h', dict(type='direction', horizon_hours=1)),
    ('Will bitcoin be higher tomorrow?', dict(type='direction', horizon_hours=24)),
    ('will btc touch 120k by friday', dict(type='touch', level=120000.0, side='above', horizon_hours=87)),
    ('Can BTC fall below $100,000 this week?', dict(type='touch', level=100000.0, side='below', horizon_hours=168)),
    ('chance of a 5% drop today', dict(type='big_move', threshold_pct=5.0, side='down', horizon_hours=15)),
    ('Where will BTC be in 3 days?', dict(type='range', horizon_hours=72)),
    ('why is btc dumping', dict(type='explain', horizon_hours=24)),
])
def test_common_phrasings_become_canonical_questions(text, expected):
    q = rule_parse(text, NOW, price=110000.0)
    assert q is not None
    assert {k: getattr(q, k) for k in expected} == expected


def test_unrecognized_text_is_left_for_the_ai_parser():
    assert rule_parse('tell me a joke', NOW, price=110000.0) is None


def test_small_bare_numbers_are_not_prices():
    assert extract_level('touch 70 by friday') is None
    assert extract_level('touch 70k by friday') == 70000.0
    assert extract_level('hit $95,500') == 95500.0


def test_horizons_resolve_in_utc_and_respect_bounds():
    assert resolve_horizon('next 2 days', NOW) == 48
    assert resolve_horizon('by end of day', NOW) == 15   # 14.5 h to midnight, rounded up
    assert resolve_horizon('in 10 days', NOW) is None    # beyond 7 days
    with pytest.raises(ValueError):
        Question(type='direction', horizon_hours=200)
    with pytest.raises(ValueError):
        Question(type='touch', horizon_hours=24)          # touch needs a level
