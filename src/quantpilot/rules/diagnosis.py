"""Why a trade passed or failed, and whether a skip was right (owner's diagnostics tab, rulebook `diagnostics`).

Codes are checked in a fixed order on an outcome measured in R after costs (see `outcomes.simulate`).
They describe what happened; nothing here changes a rule.
"""
from __future__ import annotations

from quantpilot.simulation.tradeplan import ROUND_TRIP

ADVICE = {
    'FAIL_STOP_HUNT': ('Direction was right: the original target was reached within 4h of the stop-out.',
                       'Stop sat inside noise; compare the wide-stop outcome for this playbook and regime.'),
    'FAIL_GREEDY_TP': ('Reached at least 75% of the way to the target, then reversed into a loss.',
                       'Target may be beyond the reachable range; compare a nearer target or an earlier break-even.'),
    'FAIL_FADED_IMPULSE': ('Stopped with almost no favourable move (under 0.15R): the entry fought the flow.',
                           'Check taker flow and open interest at entry for this setup.'),
    'FAIL_CHOP_BLEED': ('Time stop in a flat market; costs ate the result.',
                        'Setup needs more volatility or a shorter time stop.'),
    'PASS_CLEAN': ('Won with little adverse movement (under 0.5R).', 'Keep.'),
    'PASS_GRIND': ('Won after a deep adverse move (0.75R or more).', 'Entry was early; consider waiting for a retest.'),
    'STANDARD_VARIANCE': ('Ordinary outcome for the plan.', 'No change suggested.'),
    'SKIP_GOOD_AVOID': ('Skipped, and the simulated trade lost.', 'The skip was right.'),
    'SKIP_MISSED_WIN': ('Skipped, and the simulated trade won cleanly.', 'The filter that skipped it cost money here.'),
    'SKIP_NEUTRAL': ('Skipped; the simulated trade was neither a clean win nor a loss.', 'No change suggested.'),
    'UNKNOWN': ('No outcome yet.', 'Wait for the trade window to finish.'),
}


def _result(code, outcome):
    why, adjustment = ADVICE[code]
    return dict(code=code, why=why, adjustment=adjustment,
                r=None if outcome is None else outcome.get('r'), net=None if outcome is None else outcome.get('net'))


def code(outcome, taken):
    if not outcome or outcome.get('net') is None:
        return _result('UNKNOWN', outcome)
    net, reason = outcome['net'], str(outcome.get('reason') or '')
    mfe, mae, rr = outcome.get('mfe_r') or 0.0, outcome.get('mae_r') or 0.0, outcome.get('reward_risk') or 0.0
    if not taken:
        if net < 0:
            return _result('SKIP_GOOD_AVOID', outcome)
        return _result('SKIP_MISSED_WIN' if mae > -.5 else 'SKIP_NEUTRAL', outcome)
    stopped = reason.endswith('STOP') and reason not in ('TIME_STOP',)
    if stopped and outcome.get('target_after_exit'):
        return _result('FAIL_STOP_HUNT', outcome)
    if net < 0 and rr > 0 and mfe >= .75*rr:
        return _result('FAIL_GREEDY_TP', outcome)
    if stopped and mfe < .15:
        return _result('FAIL_FADED_IMPULSE', outcome)
    if reason in ('TIME', 'TIME_EXIT', 'TIME_STOP') and abs(net) < ROUND_TRIP:
        return _result('FAIL_CHOP_BLEED', outcome)
    if net > 0 and mae > -.5:
        return _result('PASS_CLEAN', outcome)
    if net > 0 and mae <= -.75:
        return _result('PASS_GRIND', outcome)
    return _result('STANDARD_VARIANCE', outcome)


def from_trade(trade, post_high=None, post_low=None):
    """An outcome (in R) from a `futures-v1` trade record and the 4h after its exit."""
    s, entry = trade['side'], trade['entry']
    risk = abs(entry-trade['initial_stop'])/entry
    target = trade.get('initial_target')
    after = None
    if target is not None and post_high is not None and post_low is not None:
        after = post_high >= target if s == 1 else post_low <= target
    net = trade['net']/(trade['quantity']*entry)
    return dict(reason=trade['reason'], net=net, r=net/risk if risk else None,
                mfe_r=trade['max_favorable']/risk if risk else None, mae_r=trade['max_adverse']/risk if risk else None,
                reward_risk=abs(target-entry)/entry/risk if risk and target is not None else None,
                target_after_exit=bool(after))
