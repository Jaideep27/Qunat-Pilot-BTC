"""Owner's diagnostics tab: why a trade passed or failed, and whether a skip was right."""
import pytest

from quantpilot.rules import diagnosis


def outcome(**kw):
    base = dict(reason='STOP', net=-.012, r=-1.05, mfe_r=.3, mae_r=-1.0, reward_risk=2.0, target_after_exit=False)
    return base | kw


@pytest.mark.parametrize('kw, code', [
    (dict(target_after_exit=True), 'FAIL_STOP_HUNT'),
    (dict(mfe_r=1.6), 'FAIL_GREEDY_TP'),
    (dict(mfe_r=.1), 'FAIL_FADED_IMPULSE'),
    (dict(reason='TIME', net=-.0005, r=-.05, mae_r=-.4), 'FAIL_CHOP_BLEED'),
    (dict(reason='TARGET', net=.02, r=2.0, mae_r=-.2, mfe_r=2.0), 'PASS_CLEAN'),
    (dict(reason='TARGET', net=.02, r=2.0, mae_r=-.9, mfe_r=2.0), 'PASS_GRIND'),
    (dict(reason='TARGET', net=.02, r=2.0, mae_r=-.6, mfe_r=2.0), 'STANDARD_VARIANCE'),
    (dict(reason='ABORT', net=-.004, r=-.3, mfe_r=.5, mae_r=-.4), 'STANDARD_VARIANCE'),
])
def test_taken_trades(kw, code):
    assert diagnosis.code(outcome(**kw), taken=True)['code'] == code


def test_skips():
    assert diagnosis.code(outcome(), taken=False)['code'] == 'SKIP_GOOD_AVOID'
    assert diagnosis.code(outcome(reason='TARGET', net=.02, r=2, mae_r=-.2), taken=False)['code'] == 'SKIP_MISSED_WIN'
    assert diagnosis.code(outcome(reason='TARGET', net=.02, r=2, mae_r=-.8), taken=False)['code'] == 'SKIP_NEUTRAL'


def test_every_code_has_a_plain_reason_and_missing_outcomes_are_unknown():
    out = diagnosis.code(outcome(target_after_exit=True), taken=True)
    assert 'target' in out['why'].lower() and out['adjustment']
    assert diagnosis.code(None, taken=True)['code'] == 'UNKNOWN'


def test_futures_trade_records_convert_to_outcomes():
    trade = dict(side=1, entry=100.0, exit=98.4, initial_stop=98.5, initial_target=104.0, net=-16.6, quantity=1.0,
                 reason='STOP', max_favorable=.004, max_adverse=-.0165)
    out = diagnosis.from_trade(trade, post_high=104.2, post_low=97.0)
    assert out['r'] == pytest.approx(-16.6/100/(1.5/100))
    assert out['mfe_r'] == pytest.approx(.4/1.5) and out['mae_r'] == pytest.approx(-1.65/1.5)
    assert out['target_after_exit'] and out['reward_risk'] == pytest.approx(4/1.5)
    assert diagnosis.code(out, taken=True)['code'] == 'FAIL_STOP_HUNT'
