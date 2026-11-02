"""A8 account config: eligibility, sizing per account, highest-score choice, plain-English rules, watch items."""
import pytest

from quantpilot.accounts import config as accounts


@pytest.fixture(scope='module')
def cfg():
    return {a['id']: a for a in accounts.load(accounts.V1)['accounts']}


def plan(stop, entry=80_000.0, side='long'):
    return dict(side=side, entry=entry, stop=stop, target=entry*1.02 if side == 'long' else entry*.98)


def test_config_has_the_registered_accounts(cfg):
    assert [cfg[k]['leverage'] for k in ('acct-a', 'acct-b', 'acct-c', 'acct-d')] == [2, 3, 4, 5]
    assert [cfg[k]['risk'] for k in ('acct-a', 'acct-b', 'acct-c', 'acct-d')] == [.01, .015, .02, .025]
    loaded = accounts.load(accounts.V1)
    assert len(loaded['hash']) == 12 and loaded['round_length'] == 30 and loaded['floor'] == 5000


def test_eligible_and_full_risk_follow_the_resolved_scores(cfg):
    a = cfg['acct-a']
    assert accounts.eligible(a, a['cutoff_score']) and not accounts.eligible(a, a['cutoff_score']-1e-9)
    assert accounts.full_risk(a, a['full_risk_score']) and not accounts.full_risk(a, a['cutoff_score'])


def test_size_risks_the_account_fraction(cfg):
    a = cfg['acct-a']
    full = accounts.size(a, plan(79_200.0), a['full_risk_score'], 10_000.0, 10_000.0, 0.0)
    half = accounts.size(a, plan(79_200.0), a['cutoff_score'], 10_000.0, 10_000.0, 0.0)
    assert full['rejections'] == [] and full['risk_amount'] == pytest.approx(100, abs=1.5)
    assert half['risk_amount'] == pytest.approx(50, abs=1.5) and half['risk_fraction_used'] == pytest.approx(.005, abs=2e-4)
    assert full['leverage'] == 2 and full['margin'] == pytest.approx(full['notional']/2)


def test_leverage_caps_the_notional(cfg):
    d = cfg['acct-d']
    out = accounts.size(d, plan(79_920.0), d['full_risk_score'], 10_000.0, 10_000.0, 0.0)
    assert out['notional'] <= 50_000+1e-6 and out['risk_fraction_used'] < .025


def test_stop_beyond_liquidation_rejects(cfg):
    d = cfg['acct-d']
    assert 'STOP_BEYOND_LIQUIDATION' in accounts.size(d, plan(60_000.0), d['full_risk_score'], 10_000.0, 10_000.0, 0.0)['rejections']


def test_short_sizes_from_a_stop_above(cfg):
    a = cfg['acct-a']
    out = accounts.size(a, plan(80_800.0, side='short'), a['full_risk_score'], 10_000.0, 10_000.0, 0.0)
    assert out['rejections'] == [] and out['risk_amount'] == pytest.approx(100, abs=1.5)


def test_choose_takes_the_highest_score():
    assert accounts.choose([dict(id='x', score=.1), dict(id='y', score=.4), dict(id='z', score=-.2)])['id'] == 'y'
    assert accounts.choose([]) is None


def test_describe_mentions_cutoff_risk_leverage_and_30_trades(cfg):
    text = ' '.join(accounts.describe(cfg['acct-a'], 30)['rules'])
    assert 'top 10%' in text and '1%' in text and 'Leverage 2×' in text and '30 trades' in text
    assert accounts.describe(cfg['acct-a'], 30)['watches']


def test_watch_items_for_a_long():
    features = dict(imbalance_15bps=.12, spread_bps=.02, liq_long_usd_15m=2_500_000.0, liq_short_usd_15m=0.0)
    items = {w['name']: w for w in accounts.watch_items('long', features, dict(direction='bearish', impact='high',
                                                                               title='x'), 45)}
    assert items['book_imbalance']['ok'] and items['spread']['ok']
    assert not items['liquidations_against']['ok'] and not items['news_latest']['ok'] and not items['macro_next']['ok']
    quiet = {w['name']: w for w in accounts.watch_items('short', {}, None, None)}
    assert quiet['book_imbalance']['ok'] is None and quiet['macro_next']['ok']


def test_v2_config_runs_four_different_styles_and_retires_the_a8_accounts():
    v2 = accounts.load(accounts.V2)
    assert v2['version'] == 2 and [a['style'] for a in v2['accounts']] == ['X1', 'X2', 'T1', 'R1', 'B1', 'C1']
    assert v2['retired'] == ['acct-a', 'acct-b', 'acct-c', 'acct-d'] and len(v2['hash']) == 12
    assert all(a['risk'] == .01 and a['leverage'] == 2.0 for a in v2['accounts'] if a['style'] != 'C1')
    assert all(len(a['id']) <= 16 for a in v2['accounts'])      # the journal's account column


def test_v3_is_one_master_account_and_retires_every_other():
    v3 = accounts.load(accounts.V3)            # A14 (retired by A15; still loadable)
    assert v3['version'] == 3 and [(a['id'], a['style'], a['park']) for a in v3['accounts']] == [('acct-master', 'X2', False)]
    assert set(v3['retired']) == {'acct-a', 'acct-b', 'acct-c', 'acct-d', 'acct-combined', 'acct-trend-bot', 'acct-trend',
                                   'acct-range', 'acct-breakout', 'acct-carry'}


def test_v4_has_no_accounts_and_retires_the_master_too():
    v4 = accounts.load()                       # A15: trading switched off
    assert v4['version'] == 4 and v4['accounts'] == [] and len(v4['retired']) == 11 and 'acct-master' in v4['retired']
