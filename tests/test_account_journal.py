"""A8 journal: per-account decisions, trades and 30-trade reviews in their own tables."""
import pandas as pd

from quantpilot import account_journal as aj

T = pd.Timestamp('2026-10-22 10:00', tz='UTC')


def decision(account, hit='PB01:2026-10-22T10:00:00+00:00', status='TAKEN'):
    return dict(account=account, hit_id=hit, time=T, decision=status, score=.3, confidence=.6, cutoff=.2,
                trade_id=None, payload=dict(ticks=[dict(name='x', ok=True)]))


def trade(hit='PB01:2026-10-22T10:00:00+00:00', net=12.5, exit_time=T+pd.Timedelta(hours=2)):
    return dict(side=1, entry_time=T.isoformat(), exit_time=exit_time.isoformat(), net=net, r=.5, reason='TARGET',
                plan=dict(playbook='PB01', hit_id=hit))


def test_same_hit_in_two_accounts_makes_two_rows(account):
    assert aj.add_decisions(account, [decision('acct-a'), decision('acct-d')]) == 2
    assert aj.add_decisions(account, [decision('acct-a')]) == 0
    rows = aj.decisions(account)
    assert {r['id'] for r in rows} == {'acct-a:PB01:2026-10-22T10:00:00+00:00', 'acct-d:PB01:2026-10-22T10:00:00+00:00'}


def test_add_trade_is_idempotent_and_numbered(account):
    assert aj.add_trade(account, 'acct-b', 1, 7, trade())
    assert not aj.add_trade(account, 'acct-b', 1, 7, trade())
    row = aj.trades(account, 'acct-b')[0]
    assert row['id'] == 'acct-b:PB01:2026-10-22T10:00:00+00:00' and row['number'] == 7 and row['round'] == 1
    assert row['playbook'] == 'PB01' and row['side'] == 'long' and row['net'] == 12.5


def test_trades_filter_by_account_undiagnosed_and_unexplained(account):
    aj.add_trade(account, 'acct-a', 1, 1, trade('h1'))
    aj.add_trade(account, 'acct-c', 1, 1, trade('h2'))
    assert [t['account'] for t in aj.trades(account, 'acct-c')] == ['acct-c']
    aj.diagnose(account, 'acct-a:h1', 'STOP_TOO_TIGHT', dict(why='x'), T)
    assert [t['id'] for t in aj.trades(account, undiagnosed=True)] == ['acct-c:h2']
    assert [t['id'] for t in aj.trades(account, unexplained=True)] == ['acct-a:h1']   # diagnosed, not explained
    aj.explain(account, 'acct-a:h1', dict(why='it fell', lesson='wait'), T)
    assert aj.trades(account, unexplained=True) == []
    assert aj.trades(account, 'acct-a')[0]['explanation'] == dict(why='it fell', lesson='wait')


def test_review_round_trip_and_resume(account):
    aj.save_review(account, 'acct-a', 1, dict(numbers=dict(win_rate=.4)), 'ready', T)
    review = aj.reviews(account, 'acct-a')[0]
    assert review['id'] == 'acct-a:round-1' and review['status'] == 'ready' and review['payload']['numbers']['win_rate'] == .4
    aj.mark_resumed(account, 'acct-a', 1, T+pd.Timedelta(days=1))
    review = aj.reviews(account, 'acct-a')[0]
    assert review['status'] == 'resumed' and review['resumed_at'].startswith('2026-10-23')
