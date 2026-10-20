import pandas as pd
import pytest

from outlook_fixtures import panel
from quantpilot import analogs


def test_ranks_use_only_the_past():
    p = panel(n=500)
    base = analogs.state_ranks(p)
    changed = p.copy()
    changed.iloc[300:, changed.columns.get_loc('rsi_14')] += 100
    pd.testing.assert_frame_equal(base.iloc[:300], analogs.state_ranks(changed).iloc[:300])


def test_estimate_shrinks_toward_the_base_rate_and_never_uses_unfinished_outcomes():
    p = panel(n=24*60)
    ranks = analogs.state_ranks(p)
    outcome = pd.Series(1.0, index=p.index)            # every past window went up
    t = p.index[-1]
    result = analogs.estimate(ranks, outcome, t, h=24, k=10, m=10)
    # base 1.0 and all hits 1.0 -> 1.0; now make the base 0.5 with all neighbours up
    outcome.iloc[::2] = 0.0
    result = analogs.estimate(ranks, outcome.where(ranks.index < t), t, h=24, k=10, m=10)
    assert result['k_eff'] == 10
    assert result['probability'] == pytest.approx((result['hits']+10*result['base_rate'])/(10+10))
    # outcomes ending after t are excluded: the last 24 hours never count
    late = analogs.estimate(ranks, pd.Series(1.0, index=p.index), p.index[30], h=24, k=200, m=0)
    assert late['k_eff'] <= 2  # only cutoffs whose 24h window closed by index 30 (at most one per day)


def test_neighbours_are_at_most_one_per_utc_day():
    p = panel(n=24*30)
    ranks = analogs.state_ranks(p)
    result = analogs.estimate(ranks, pd.Series(1.0, index=p.index), p.index[-1], h=1, k=200, m=0)
    assert result['k_eff'] <= 30


def test_gate_requires_validation_confirmation_and_most_years():
    rows = {'24h': dict(skill_validation=.001, skill_confirmation=.0005, yearly=[.001, .002, -.001])}
    assert analogs.promote(rows['24h']) is True
    assert analogs.promote(dict(skill_validation=.001, skill_confirmation=-.0001, yearly=[.001])) is False
    assert analogs.promote(dict(skill_validation=.001, skill_confirmation=.001, yearly=[.001, -.001, -.002])) is False


def test_validation_on_noise_does_not_promote():
    report = analogs.validate(panel(), horizons=(24,), step_hours=48, burn_in_days=120, confirm_days=120)
    assert report['24h']['windows'] > 50
    assert report['24h']['promoted'] is False
