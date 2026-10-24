import pandas as pd
import pytest

from outlook_fixtures import ctx
from quantpilot.outlook import engine as outlook
from quantpilot.outlook.questions import Question


def test_touch_probability_matches_the_reflection_formula():
    # ln(1.05) / 0.05 = 0.9758 -> 2 * (1 - Phi(0.9758)) = 0.3292
    assert outlook.touch_probability(100.0, 105.0, .05) == pytest.approx(.3292, abs=1e-3)
    assert outlook.touch_probability(100.0, 100.0, .05) == 1.0
    assert outlook.touch_probability(100.0, 95.0, 0.0) == 0.0


def test_unpromoted_direction_answers_with_the_base_rate_and_no_edge():
    c = ctx()
    result = outlook.answer(Question(type='direction', horizon_hours=4), c)
    assert result['headline']['confidence'] == 'no_edge'
    assert result['headline']['probability'] == pytest.approx(result['headline']['base_rate'])
    assert result['context']['analog']['probability'] is not None


def test_promoted_direction_uses_the_analog_estimate():
    report = dict(analogs={'4h': dict(promoted=True, relative_improvement=.02, windows=500)})
    result = outlook.answer(Question(type='direction', horizon_hours=4), ctx(report=report))
    assert result['headline']['method'] == 'analogs'
    assert result['headline']['confidence'] == 'validated'
    assert result['headline']['probability'] == pytest.approx(result['context']['analog']['probability'])


def test_nonstandard_horizons_follow_the_nearest_standard_gate():
    report = dict(analogs={'12h': dict(promoted=True, relative_improvement=.005, windows=500)})
    result = outlook.answer(Question(type='direction', horizon_hours=10), ctx(report=report))
    assert result['headline']['method'] == 'analogs' and result['headline']['confidence'] == 'weak'


def test_touch_and_range_answers_are_consistent():
    c = ctx()
    up = outlook.answer(Question(type='touch', horizon_hours=24, level=c.price*1.03, side='above'), c)
    far = outlook.answer(Question(type='touch', horizon_hours=24, level=c.price*1.20, side='above'), c)
    assert 0 <= far['headline']['probability'] < up['headline']['probability'] <= 1
    rng = outlook.answer(Question(type='range', horizon_hours=24), c)
    assert rng['range']['low'] < c.price < rng['range']['high']


def test_missing_or_stale_cache_degrades_to_a_warning_not_a_crash():
    c = ctx()
    c.now = c.now+pd.Timedelta(hours=5)
    result = outlook.answer(Question(type='direction', horizon_hours=1), c)
    assert result['stale'] is True and any('stale' in w for w in result['warnings'])
    empty = outlook.Context(panel=pd.DataFrame(), ranks=pd.DataFrame(), report={}, macro_events=[], news_items=[],
                            forecasts=[], price=None, price_time=None, now=c.now)
    result = outlook.answer(Question(type='direction', horizon_hours=1), empty)
    assert result['headline']['probability'] is None and 'warming up' in ' '.join(result['warnings'])


def test_explain_has_no_probability_and_lists_recent_changes():
    result = outlook.answer(Question(type='explain', horizon_hours=24), ctx())
    assert result['headline']['kind'] == 'explain' and result['headline']['probability'] is None
    assert {'return_window', 'volume_ratio_24h', 'funding_rate'} <= set(result['evidence'])


def test_evidence_carries_percentiles():
    result = outlook.answer(Question(type='direction', horizon_hours=24), ctx())
    funding = result['evidence']['funding_rate']
    assert 0 <= funding['percentile'] <= 100
