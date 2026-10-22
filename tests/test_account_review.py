"""A8 reasoning: per-trade diagnosis and explanation, the 30-trade review and resume."""
import numpy as np
import pandas as pd
import pytest

from quantpilot import account_journal as aj
from quantpilot import account_review, journal

T = pd.Timestamp('2026-10-22 10:00', tz='UTC')


def trade(hit, net, k=0, side=1, reason='STOP'):
    entry = T+pd.Timedelta(hours=3*k)
    return dict(side=side, quantity=.1, entry=80_000.0, exit=79_200.0 if net < 0 else 81_600.0, entry_time=entry.isoformat(),
                exit_time=(entry+pd.Timedelta(hours=1)).isoformat(), reason=reason, pnl=net, fees=1.0, funding=0.0, net=net,
                return_on_equity=net/10_000, max_favorable=.004, max_adverse=.006, initial_stop=79_200.0,
                initial_target=81_600.0, protection_changes=[], plan=dict(playbook='PB08L' if k % 2 else 'PB01', hit_id=hit,
                                                                           side='long', stop=79_200.0, target=81_600.0))


def window(hours=8):
    times = pd.date_range(T, T+pd.Timedelta(hours=hours), freq='1min', tz='UTC')
    return pd.DataFrame(dict(available_at=times, high=80_500.0, low=79_000.0))


def seed(engine, n=1, account='acct-a'):
    for k in range(n):
        hit = f'h{k}'
        aj.add_trade(engine, account, 1, k+1, trade(hit, 25.0 if k % 3 == 0 else -40.0, k))
        aj.add_decisions(engine, [dict(account=account, hit_id=hit, time=T+pd.Timedelta(hours=3*k), decision='TAKEN',
                                       score=.3, confidence=.55, cutoff=.2, trade_id=f'{account}:{hit}', payload=dict(
                                           ticks=[dict(text='close > vwap', ok=True)],
                                           watch=[dict(name='book_imbalance', value=.1 if k % 3 == 0 else -.1,
                                                       ok=k % 3 == 0)],
                                           snapshot=dict(global_ratio=1.5+(k % 3 == 0), funding_rate=1e-4),
                                           playbook='PB01', regime='RANGE'))])


def test_trades_are_diagnosed_only_after_the_window(account):
    seed(account)
    assert account_review.diagnose_trades(account, window(), T+pd.Timedelta(hours=2)) == 0
    assert account_review.diagnose_trades(account, window(), T+pd.Timedelta(hours=6)) == 1
    row = aj.trades(account, 'acct-a')[0]
    assert row['diagnosis'] and row['payload']['diagnosis']['why']


def test_explanation_is_stored_from_the_runner(account):
    seed(account)
    account_review.diagnose_trades(account, window(), T+pd.Timedelta(hours=6))
    seen = {}

    def runner(system, prompt, schema):
        seen.update(system=system, prompt=prompt)
        return dict(why='Price swept the stop before turning.', lesson='Wider stop near liquidity.')
    assert account_review.explain_trades(account, T+pd.Timedelta(hours=6), runner) == 1
    row = aj.trades(account, 'acct-a')[0]
    assert row['explanation']['why'].startswith('Price swept') and 'close > vwap' in seen['prompt']
    assert 'data' in seen['system'].lower()


def test_runner_failure_leaves_the_trade_pending_for_a_retry(account):
    seed(account)
    account_review.diagnose_trades(account, window(), T+pd.Timedelta(hours=6))

    def broken(*_):
        raise RuntimeError('Claude Code failed')
    assert account_review.explain_trades(account, T+pd.Timedelta(hours=6), broken) == 0
    assert aj.trades(account, unexplained=True)[0]['id'] == 'acct-a:h0'
