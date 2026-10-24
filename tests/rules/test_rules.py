"""Rulebook loading, the safe condition evaluator, stops/targets and the confluence score."""
import pytest

from quantpilot.rules import rulebook as rules


@pytest.fixture(scope='module')
def book():
    return rules.load(rules.RULEBOOK_V1)   # v1 behaviour (nearest target); v2 has its own tests below


def test_rulebook_loads_with_a_stable_hash_and_unique_ids(book):
    assert book['version'] == 1 and len(book['hash']) == 12
    assert rules.load(rules.RULEBOOK_V1)['hash'] == book['hash']
    assert rules.validate(rules.load()) == []
    ids = [p['id'] for p in book['playbooks']]
    assert len(ids) == len(set(ids)) >= 20
    assert {'PB01', 'PB02', 'PB12L', 'PB13S'} <= set(ids)


def test_every_referenced_feature_is_known(book):
    from quantpilot.features import patterns  # noqa: F401  (pattern columns are listed in rules.PATTERN_COLUMNS)
    unknown = rules.validate(book)
    assert unknown == []


def test_validate_reports_an_unknown_feature(book):
    broken = dict(book, playbooks=[dict(book['playbooks'][0], conditions=[['no_such_feature', '>', 1]])])
    assert rules.validate(broken) == [('PB01', 'no_such_feature')]


def test_operators_and_feature_comparisons():
    f = dict(a=1.0, b=2.0)
    assert rules.check(['a', '<', 'b'], f)[0] and rules.check(['a', '<=', 1], f)[0]
    assert not rules.check(['a', '>', 'b'], f)[0] and rules.check(['b', '!=', 'a'], f)[0]
    assert rules.check({'any': [['a', '>', 5], ['b', '==', 2]]}, f)[0]
    assert not rules.check({'all': [['a', '>', 0], ['b', '>', 5]]}, f)[0]


def test_a_missing_input_makes_the_condition_false_and_is_reported():
    ok, records = rules.check(['a', '>', 'b'], dict(a=1.0, b=None))
    assert not ok and records[0]['missing'] == ['b']
    ok, records = rules.check(['a', '>', 0], dict(a=float('nan')))
    assert not ok and records[0]['missing'] == ['a']


def test_unknown_operators_are_rejected():
    with pytest.raises(ValueError):
        rules.check(['a', 'in', 1], dict(a=1))


def test_evaluate_requires_the_regime_and_every_condition(book):
    pb = rules.playbook(book, 'PB10L')
    f = dict(vwap_z=-2.5, rsi_15m=30, adx_1h=15, oi_change_15m=0.0)
    hit = rules.evaluate(pb, f, 'RANGE')
    assert hit['ok'] and all(c['ok'] for c in hit['checks'])
    assert not rules.evaluate(pb, f, 'TREND_UP')['ok']
    miss = rules.evaluate(pb, f | dict(rsi_15m=None), 'RANGE')
    assert not miss['ok'] and miss['missing'] == ['rsi_15m']


def test_stop_and_target_for_a_long(book):
    pb = rules.playbook(book, 'PB01')
    f = {'close': 100.0, 'atr_1h': 2.0, 'trigger.low': 96.0, 'vwap': 104.0, 'resistance_1h': 101.0,
         'resistance_4h': 108.0, 'prior_day_high': None, 'band4_high': 106.0}
    levels = rules.levels(pb, f, book['defaults'])
    assert levels['stop'] == pytest.approx(95.5)              # anchor 96 - 0.25 ATR
    assert levels['target'] == pytest.approx(101.0)           # nearest candidate beyond 0.25 ATR
    assert levels['anchor'] == 96.0


def test_target_is_capped_and_falls_back_to_r_multiple(book):
    pb = rules.playbook(book, 'PB02')
    f = {'close': 100.0, 'atr_1h': 1.0, 'broken_support': 101.0, 'support_1h': None, 'support_4h': None,
         'prior_day_low': None, 'band4_low': 97.0}
    levels = rules.levels(pb, f, book['defaults'])
    assert levels['stop'] == pytest.approx(101.5)
    assert levels['target'] == pytest.approx(97.0)            # fallback 2R = 97.0, also at the cap
    f2 = dict(f, band4_low=98.5)
    assert rules.levels(pb, f2, book['defaults'])['target'] == pytest.approx(98.5)


def test_stop_is_widened_to_the_minimum_and_rejects_beyond_the_maximum(book):
    pb = rules.playbook(book, 'PB09L')
    f = {'close': 100.0, 'atr_1h': 2.0, 'low_15m': 99.9, 'vwap': 110.0, 'resistance_1h': None, 'resistance_4h': None,
         'prior_day_high': None, 'band4_high': 120.0}
    assert rules.levels(pb, f, book['defaults'])['stop'] == pytest.approx(99.0)   # 0.5 ATR minimum
    far = rules.levels(pb, dict(f, low_15m=90.0), book['defaults'])
    assert far['stop'] is None and far['reason'] == 'STOP_TOO_WIDE'
    wrong = rules.levels(pb, dict(f, low_15m=101.0), book['defaults'])
    assert wrong['stop'] == pytest.approx(99.0)              # an anchor on the wrong side falls back to the minimum


def test_confluence_is_signed_toward_the_side():
    f = dict(ema_stack_1h=1, adx_1h=30, rsi_15m=60, volume_z_15m=2, taker_share_15m=.6, vwap_z=1, oi_price_state=1,
             funding_z=-2, range_pos_4h=.2)
    assert rules.confluence(f, 'long')['score'] == 9
    assert rules.confluence(f, 'short')['score'] == -7      # volume counts for both sides
    assert rules.confluence({}, 'long')['score'] == 0


def test_v2_targets_the_nearest_level_that_clears_the_minimum_reward_risk():
    v2 = rules.load()
    assert v2['version'] == 2 and v2['defaults']['target_selection'] == 'first_meeting_min_rr'
    pb = rules.playbook(v2, 'PB01')
    f = {'close': 100.0, 'atr_1h': 2.0, 'trigger.low': 98.0, 'vwap': 100.8, 'resistance_1h': 103.0,
         'resistance_4h': 104.5, 'prior_day_high': None, 'band4_high': 106.0, 'funding_rate': 0.0}
    levels = rules.levels(pb, f, v2['defaults'])
    assert levels['stop'] == pytest.approx(97.5)
    # 100.8 and 103.0 give less than 1.5 net reward:risk on a 2.5 risk; 104.5 is the first that clears it
    assert levels['target'] == pytest.approx(104.5)
    assert rules.levels(pb, dict(f, **{'resistance_4h': None}), v2['defaults'])['target'] == pytest.approx(105.0)  # 2R
    nearest = rules.levels(pb, f, dict(v2['defaults'], target_selection='nearest'))
    assert nearest['target'] == pytest.approx(100.8)


def test_v1_is_kept_for_the_record():
    v1 = rules.load(rules.RULEBOOK_V1)
    assert v1['version'] == 1 and v1['hash'] == '3ea0f47e52c1'
    assert v1['defaults'].get('target_selection', 'nearest') == 'nearest'
