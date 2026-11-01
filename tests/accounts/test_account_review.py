"""A8 reasoning: per-trade diagnosis and explanation, the 30-trade review and resume."""
import numpy as np
import pandas as pd
import pytest

from quantpilot.accounts import journal as aj
from quantpilot.accounts import review as account_review
from quantpilot.rules import journal

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
    from quantpilot.accounts.journal import AccountDecision
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
    assert n['profit_factor'] == pytest.approx(250/800) and n['longest_losing_streak'] == 2
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


def carry_trade(k=0):
    entry = T+pd.Timedelta(days=3*k)
    return dict(side=1, quantity=0.0, entry=80_000.0, exit=82_000.0, entry_time=entry.isoformat(),
                exit_time=(entry+pd.Timedelta(days=2)).isoformat(), reason='FUNDING_TURNED', pnl=-5.0, fees=9.0,
                funding=20.0, net=6.0, return_on_equity=.0006, rebalances=0, max_favorable=0.0, max_adverse=0.0,
                initial_stop=None, initial_target=None, protection_changes=[], version='carry-v1',
                plan=dict(side='hedge', playbook='C1', style='C1', hit_id=f'C1:{entry.isoformat()}'))


def test_a_funding_cycle_is_recorded_without_a_price_diagnosis(account):
    aj.add_trade(account, 'acct-carry', 1, 1, carry_trade())
    assert account_review.diagnose_trades(account, window(24*4), T+pd.Timedelta(days=4)) == 1
    t = aj.trades(account, 'acct-carry')[0]
    assert t['diagnosis'] == 'FUNDING_CYCLE' and t['playbook'] == 'C1'


def test_run_pending_reviews_a_style_account_with_its_own_rules(account):
    for k in range(3):
        aj.add_trade(account, 'acct-range', 1, k+1, dict(trade(f'R1:h{k}', 25.0 if k else -40.0, k),
                                                          plan=dict(playbook='R1', style='R1', hit_id=f'R1:h{k}')))
    journal.save_account(account, 'acct-range', dict(status='PAUSED_FOR_REVIEW', round=1, in_round=30, number=30,
                                                     style='R1', futures=dict(paused=True)), T)
    cfg = dict(version=2, round_length=30, accounts=[dict(id='acct-range', name='Range Trader', style='R1', risk=.01)])
    seen = {}

    def runner(system, prompt, schema):
        seen['prompt'] = prompt
        return summary_runner(system, prompt, schema)
    out = account_review.run_pending(account, T, runner=runner, cfg=cfg, bars=None)
    assert out['reviews'] == ['acct-range']
    review = aj.reviews(account, 'acct-range')[0]['payload']
    assert review['learner']['available'] is False and review['manager']['available'] is False
    assert 'sideways' in seen['prompt']


def test_resuming_the_collector_restarts_its_thirty_day_clock(account):
    journal.save_account(account, 'acct-carry', dict(status='PAUSED_FOR_REVIEW', round=1, in_round=0, number=2, style='C1',
                                                     round_started=T.isoformat(), futures=dict(paused=True)), T)
    account_review.resume(account, 'acct-carry', T+pd.Timedelta(days=31))
    state = journal.load_account(account, 'acct-carry')
    assert state['round_started'] == (T+pd.Timedelta(days=31)).isoformat() and state['round'] == 2
