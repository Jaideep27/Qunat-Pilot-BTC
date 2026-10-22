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


def seed_round(engine, n=30, account='acct-a'):
    seed(engine, n, account)
    from quantpilot.account_journal import AccountDecision
    from sqlalchemy.orm import Session
    with Session(engine) as session, session.begin():
        for row in session.query(AccountDecision).all():
            row.payload = dict(row.payload, sizing=dict(risk_amount=100.0))   # $100 risked: R = net/100


def summary_runner(system, prompt, schema):
    assert 'suggestions' in schema['properties']
    return dict(where_it_went_wrong='Most losses were longs into falling order books.', why='Sellers dominated.',
                suggestions=['Require buy-side book imbalance.'])


def test_review_numbers_and_separators(account):
    seed_round(account)
    review = account_review.build_review(account, 'acct-a', 1, dict(id='acct-a', risk=.01),
                                         expectations=dict(win_rate=.5, mean_r=-.1), runner=summary_runner,
                                         learner_stats={}, manager=None, bars=None)
    n = review['numbers']
    assert n['trades'] == 30 and n['wins'] == 10 and n['win_rate'] == pytest.approx(1/3)
    assert n['mean_r'] == pytest.approx((10*25-20*40)/30/100)
    assert n['by_playbook']['PB01']['n'] + n['by_playbook']['PB08L']['n'] == 30 and n['expected']['win_rate'] == .5
    seps = {s['name']: s for s in review['separators']}
    assert seps['watch:book_imbalance']['label'] == 'consistent' and seps['snapshot:global_ratio']['effect'] > .8
    assert review['learner']['would_skip'] == 30            # no learner history: it skips everything
    assert review['summary']['suggestions'] == ['Require buy-side book imbalance.']


def test_review_is_stored_without_a_summary_when_the_runner_fails(account):
    seed_round(account)

    def broken(*_):
        raise RuntimeError('Claude Code failed')
    review = account_review.build_review(account, 'acct-a', 1, dict(id='acct-a', risk=.01), runner=broken,
                                         learner_stats={}, manager=None, bars=None)
    assert review['summary'] is None and 'failed' in review['summary_error'] and review['numbers']['trades'] == 30


def test_manager_replay_compares_the_rl_policy_with_the_fixed_rule(account):
    seed_round(account, n=3)
    times = pd.date_range(T, T+pd.Timedelta(hours=12), freq='5min', tz='UTC')
    path = 80_000+np.sin(np.arange(len(times))/6)*900
    bars = pd.DataFrame(dict(open=path, high=path+150, low=path-150, close=path), index=times)
    review = account_review.build_review(account, 'acct-a', 1, dict(id='acct-a', risk=.01), runner=summary_runner,
                                         learner_stats={}, manager=lambda state: 'hold', bars=bars,
                                         time_stops={'PB01': 1, 'PB08L': 1})
    m = review['manager']
    assert m['trades'] == 3 and m['policy_mean_r'] is not None and m['fixed_rule_mean_r'] is not None


def test_resume_only_from_paused_and_starts_the_next_round(account):
    import pytest as _p
    with _p.raises(ValueError):
        account_review.resume(account, 'acct-a', T)
    journal.save_account(account, 'acct-a', dict(status='PAUSED_FOR_REVIEW', round=1, in_round=30, number=30,
                                                 futures=dict(paused=True)), T)
    aj.save_review(account, 'acct-a', 1, dict(numbers={}), 'ready', T)
    out = account_review.resume(account, 'acct-a', T+pd.Timedelta(days=1))
    state = journal.load_account(account, 'acct-a')
    assert out['round'] == 2 and state['status'] == 'RUNNING' and state['in_round'] == 0 and not state['futures']['paused']
    assert aj.reviews(account, 'acct-a')[0]['status'] == 'resumed'
    with _p.raises(ValueError):
        account_review.resume(account, 'acct-a', T)


def test_run_pending_builds_one_review_per_paused_round(account):
    seed_round(account)
    journal.save_account(account, 'acct-a', dict(status='PAUSED_FOR_REVIEW', round=1, in_round=30, number=30,
                                                 futures=dict(paused=True)), T)
    cfg = dict(accounts=[dict(id='acct-a', risk=.01), dict(id='acct-b', risk=.015)])
    kw = dict(runner=summary_runner, cfg=cfg, learner_stats={}, manager=None, bars=None)
    assert account_review.run_pending(account, T, **kw)['reviews'] == ['acct-a']
    assert account_review.run_pending(account, T, **kw)['reviews'] == []
    assert aj.reviews(account, 'acct-a')[0]['status'] == 'ready'
