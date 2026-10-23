"""A8 live fan-out: four paper accounts decide on the shared setups, journal everything and survive restarts."""
import pandas as pd
import pytest

from agent_fixtures import START
from quantpilot import account_journal, account_live, account_score, journal, meta, patterns, rule_live
from test_rule_live import book, market  # noqa: F401  (module fixture)

T15 = START+pd.Timedelta(days=41, hours=3)
SCORES = {'L1': .9, 'L2': .1}


def cfg(round_length=30):
    common = dict(daily_brake=.5, percentile=50, full_risk_percentile=75)
    return dict(version=1, round_length=round_length, floor=5000.0, balance=10_000.0, hash='testcfg',
                model=dict(hash='stub'), accounts=[
                    dict(common, id='acct-a', name='Careful', cutoff_score=.5, full_risk_score=.8, leverage=2.0, risk=.01),
                    dict(common, id='acct-d', name='Aggressive', cutoff_score=0.0, full_risk_score=.5, leverage=5.0, risk=.025)])


def stub(hit, features):
    return dict(p=.6, score=SCORES[hit['playbook']], missing=[])


def run(engine, now, market, b, config=None, scorer=stub, restart=False):  # noqa: F811
    data, fast = market
    if restart:   # a server restart: nothing survives but the database
        rule_live._cache.clear()
        account_live._cache.clear()
    return rule_live.cycle(engine, now, book=b, frames=data, fast=fast, macro_events=[], news_events=[],
                           accounts_cfg=config or cfg(), scorer=scorer)


def state(engine, account_id):
    return journal.load_account(engine, account_id)


def test_each_account_decides_independently(account, market):  # noqa: F811
    run(account, T15+pd.Timedelta(seconds=20), market, book())
    rows = {(d['account'], d['hit_id'].split(':')[0]): d for d in account_journal.decisions(account)}
    assert rows[('acct-a', 'L1')]['decision'] == 'TAKEN' and rows[('acct-a', 'L2')]['decision'] == 'BELOW_CUTOFF'
    assert rows[('acct-d', 'L1')]['decision'] == 'TAKEN' and rows[('acct-d', 'L2')]['decision'] == 'LOWER_SCORE'
    taken = rows[('acct-a', 'L1')]
    assert taken['score'] == .9 and taken['cutoff'] == .5
    assert {'ticks', 'watch', 'sizing', 'snapshot', 'model'} <= set(taken['payload'])
    assert taken['payload']['sizing']['leverage'] == 2.0 and taken['payload']['snapshot']['close'] is not None
    assert state(account, 'acct-a')['futures']['pending']['action'] == 'enter'
    assert state(account, 'acct-d')['futures']['pending']['plan']['leverage'] == 5.0
    assert rule_live.load_account(account)['futures']['pending']['action'] == 'enter'     # the reference still runs


def test_pending_entry_survives_a_restart_and_fills_once(account, market):  # noqa: F811
    b = book()
    run(account, T15+pd.Timedelta(seconds=20), market, b)
    run(account, T15+pd.Timedelta(minutes=6, seconds=20), market, b, restart=True)     # down for 6 minutes
    f = state(account, 'acct-a')['futures']
    nxt = market[1].set_index('event_time').open.loc[T15]
    assert f['position']['entry'] == pytest.approx(nxt*(1+f['position']['slippage'])) and f['pending'] is None
    assert not [r for r in f['rejections'] if r['reason'] == 'POSITION_OPEN']


def cycles(engine, market, b, hours, config=None, start=T15, restart=False):  # noqa: F811
    for k in range(hours*4):
        run(engine, start+pd.Timedelta(minutes=15*k, seconds=20), market, b, config, restart=restart and k == 0)


def test_trade_numbers_continue_across_restarts(account, market):  # noqa: F811
    b = book()
    b['gates']['cooldown'] = dict(losses=99, hours=24)   # keep trading through losses; numbering is the subject
    cycles(account, market, b, 4)               # 1h time stops: two trades close within four hours
    first = account_journal.trades(account, 'acct-a')
    assert len(first) >= 2
    cycles(account, market, b, 3, start=T15+pd.Timedelta(hours=4), restart=True)
    numbers = [t['number'] for t in account_journal.trades(account, 'acct-a')]
    assert numbers == list(range(1, len(numbers)+1)) and len(numbers) > len(first)
    assert state(account, 'acct-a')['number'] == len(numbers) and state(account, 'acct-a')['in_round'] == len(numbers)


def test_pause_at_round_length(account, market):  # noqa: F811
    b = book()
    cycles(account, market, b, 6, config=cfg(round_length=2))
    s = state(account, 'acct-a')
    assert s['status'] == 'PAUSED_FOR_REVIEW' and s['in_round'] == 2 and s['futures']['paused']
    assert len(account_journal.trades(account, 'acct-a')) == 2
    assert account_journal.decisions(account, 'acct-a', limit=1)[0]['decision'] == 'PAUSED'


def test_missing_model_blocks_accounts_not_the_reference(account, market, monkeypatch):  # noqa: F811
    monkeypatch.setattr(account_score, 'model_path', lambda: account.url.database+'.missing')
    run(account, T15+pd.Timedelta(seconds=20), market, book(), scorer=None, restart=True)
    assert state(account, 'acct-a')['status'] == 'NO_SCORE_MODEL'
    assert {d['decision'] for d in account_journal.decisions(account)} == {'NO_SCORE_MODEL'}
    assert state(account, 'acct-a')['futures']['pending'] is None
    assert rule_live.load_account(account)['futures']['pending']['action'] == 'enter'


def test_long_downtime_settles_the_open_position_on_missed_bars(account, market):  # noqa: F811
    b = book()
    run(account, T15+pd.Timedelta(seconds=20), market, b)
    run(account, T15+pd.Timedelta(minutes=1, seconds=20), market, b)
    assert state(account, 'acct-a')['futures']['position'] is not None
    run(account, T15+pd.Timedelta(hours=3, seconds=10), market, b, restart=True)          # back three hours later
    trades = account_journal.trades(account, 'acct-a')
    assert len(trades) == 1 and trades[0]['number'] == 1
    assert pd.Timestamp(trades[0]['exit_time']) <= T15+pd.Timedelta(hours=1, minutes=16)


def test_live_features_match_research_columns(market):  # noqa: F811
    data, _ = market
    pf = patterns.pattern_frame(data)
    from quantpilot.playbook_research import ML_FEATURES
    assert set(ML_FEATURES) <= set(pf.columns)
    hit = dict(playbook='L1', side='long', regime='RANGE', confluence=dict(score=1),
               plan=dict(reward_risk=1.2, risk_fraction=.01), trigger=dict(facts=dict(oi_change=-.01)))
    assert list(meta.matrix(account_score.live_row(hit, pf.iloc[-1].to_dict())).columns) == meta.FEATURES


def test_closed_trades_are_diagnosed_in_the_cycle_after_four_hours(account, market):  # noqa: F811
    b = book()
    run(account, T15+pd.Timedelta(seconds=20), market, b)
    run(account, T15+pd.Timedelta(minutes=1, seconds=20), market, b)
    run(account, T15+pd.Timedelta(hours=6, seconds=10), market, b)
    trade = account_journal.trades(account, 'acct-a')[0]
    assert trade['diagnosis'] and trade['payload']['diagnosis']['outcome']


def test_an_account_failure_is_reported_and_the_reference_keeps_running(account, market):  # noqa: F811
    from sqlalchemy import select
    from quantpilot.market_store import MarketFeed

    def broken(hit, features):
        raise RuntimeError('score model exploded')
    out = run(account, T15+pd.Timedelta(seconds=20), market, book(), scorer=broken)
    assert 'score model exploded' in out['accounts']['error']
    assert rule_live.load_account(account)['futures']['pending']['action'] == 'enter'
    with account.connect() as connection:
        state = connection.execute(select(MarketFeed.state).where(MarketFeed.id == 'rules:engine')).scalar()
    assert 'score model exploded' in state['accounts']['error']


def _hashed(inputs):
    def scorer(hit, features):
        return dict(stub(hit, features), inputs=dict(inputs, side=hit['side'], regime=hit.get('regime')), challenger=None)
    scorer.hash = 'stub'
    return scorer


REF = dict(model_hash='stub', critical=['f_move_z_15m'], inputs={'f_move_z_15m': dict(min=-3.0, max=3.0, present=1.0)})


def test_bad_inputs_hold_the_setup_back(account, market, monkeypatch):  # noqa: F811
    from quantpilot import model_health
    monkeypatch.setattr(model_health, 'load_reference', lambda h, path=None: REF if h == 'stub' else None)
    run(account, T15+pd.Timedelta(seconds=20), market, book(), scorer=_hashed({'f_move_z_15m': float('inf')}))
    rows = account_journal.decisions(account)
    assert rows and {d['decision'] for d in rows} == {'DATA_CHECK_FAILED'}
    assert all(d['payload']['data_check'] == ['inf:f_move_z_15m'] for d in rows)
    assert state(account, 'acct-a')['futures']['pending'] is None


def test_good_inputs_pass_and_are_recorded(account, market, monkeypatch):  # noqa: F811
    from quantpilot import model_health
    monkeypatch.setattr(model_health, 'load_reference', lambda h, path=None: REF if h == 'stub' else None)
    run(account, T15+pd.Timedelta(seconds=20), market, book(), scorer=_hashed({'f_move_z_15m': .5}))
    rows = {(d['account'], d['hit_id'].split(':')[0]): d for d in account_journal.decisions(account)}
    taken = rows[('acct-a', 'L1')]
    assert taken['decision'] == 'TAKEN' and taken['payload']['data_check'] == []
    assert taken['payload']['inputs']['f_move_z_15m'] == .5 and taken['payload']['challenger'] is None


def test_no_reference_skips_the_check(account, market, monkeypatch):  # noqa: F811
    from quantpilot import model_health
    monkeypatch.setattr(model_health, 'load_reference', lambda h, path=None: None)
    run(account, T15+pd.Timedelta(seconds=20), market, book(), scorer=_hashed({'f_move_z_15m': float('inf')}))
    rows = account_journal.decisions(account)
    assert 'TAKEN' in {d['decision'] for d in rows}
    assert {d['payload']['data_check'] for d in rows} == {'no reference'}


def test_scorer_without_inputs_is_noted(account, market):  # noqa: F811
    run(account, T15+pd.Timedelta(seconds=20), market, book())
    assert {d['payload']['data_check'] for d in account_journal.decisions(account)} == {'no inputs'}


def test_default_scorer_returns_inputs():
    if account_score.load_model() is None:
        pytest.skip('no frozen score model on this machine')
    scorer = account_live.default_scorer()
    hit = dict(side='short', regime='TREND_DOWN', plan=dict(reward_risk=1.5, risk_fraction=.004), trigger=dict(facts={}))
    out = scorer(hit, {'premium': -.0004, 'rsi_15m': 40.0})
    assert {'f_premium', 'side', 'regime'} <= set(out['inputs']) and out['inputs']['f_premium'] == -.0004
    assert 'challenger' in out
