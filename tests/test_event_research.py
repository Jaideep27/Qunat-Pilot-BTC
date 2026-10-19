import numpy as np
import pandas as pd
import pytest

from quantpilot import event_research as er


def test_drop_touch_target_uses_future_lows_not_only_the_endpoint():
    index = pd.date_range('2025-01-01', periods=300, freq='5min', tz='UTC')
    close = pd.Series(100.0, index=index)
    low = close.copy()
    low.iloc[5] = 94.0  # an intrahorizon -6% wick that recovers by the horizon end
    y = er.targets(close, low)
    assert y['drop5_24h'].iloc[0] == 1.0           # wick inside bars 1..288 after cutoff 0
    assert y['return_24h'].iloc[0] == pytest.approx(0.0)
    assert y['drop5_24h'].iloc[5] == 0.0           # the wick is the cutoff bar itself, already known
    assert np.isnan(y['drop5_24h'].iloc[-1])       # future not yet observed


def test_garch_fit_recovers_simulated_parameters():
    rng = np.random.default_rng(3)
    omega, alpha, beta = 2e-6, .08, .9
    n, var = 6000, np.empty(6000)
    returns = np.empty(n)
    var[0] = omega/(1-alpha-beta)
    for t in range(n):
        if t:
            var[t] = omega+alpha*returns[t-1]**2+beta*var[t-1]
        returns[t] = np.sqrt(var[t])*rng.standard_normal()
    fitted = er.garch_fit(returns)
    assert fitted['alpha'] == pytest.approx(alpha, abs=.03)
    assert fitted['beta'] == pytest.approx(beta, abs=.04)
    assert fitted['alpha']+fitted['beta'] < 1


def test_garch_multistep_variance_reverts_to_its_long_run_level():
    params = dict(omega=1e-6, alpha=.1, beta=.8)
    long_run = 1e-6/(1-.9)
    one = er.garch_forecast(params, variance=4*long_run, shock=0.0, steps=1)
    many = er.garch_forecast(params, variance=4*long_run, shock=0.0, steps=5000)
    # Next variance: omega + beta*current (zero shock); far horizons approach steps * long run.
    # The decaying excess sums to about (v1 - L)/(1 - .9) = 2.3e-4, i.e. 0.46% of 5000 * L.
    assert one == pytest.approx(1e-6+.8*4*long_run)
    assert many/5000 == pytest.approx(long_run, rel=.01)


def test_qlike_is_minimized_by_the_true_variance():
    realized = np.array([1e-4, 4e-4])
    assert er.qlike(realized, realized) < er.qlike(realized, realized*2)
    assert er.qlike(realized, realized) < er.qlike(realized, realized/2)


def test_block_bootstrap_resamples_whole_days():
    index = pd.date_range('2025-01-01', periods=288*20, freq='5min', tz='UTC')
    y = np.tile([0., 1.], len(index)//2)
    good, base = np.where(y == 1, .9, .1), np.full(len(index), .5)
    result = er.block_bootstrap_skill(y, good, base, index, seed=1)
    assert result['blocks'] == 20
    assert result['brier_improvement'] == pytest.approx(.25-.01)
    assert result == er.block_bootstrap_skill(y, good, base, index, seed=1)
    # Identical days make the interval collapse onto the estimate, up to float rounding.
    assert 0 < result['ci95'][0] <= result['brier_improvement']+1e-12
    assert result['brier_improvement'] <= result['ci95'][1]+1e-12


def test_experiment_runs_end_to_end_without_leaking_or_promoting(account, tmp_path):
    rng = np.random.default_rng(0)
    n = 288*120
    events = pd.date_range('2025-01-01', periods=n, freq='5min', tz='UTC')
    close = 40000*np.exp(np.cumsum(rng.normal(0, .002, n)))
    open_ = np.r_[close[0], close[:-1]]
    meta = dict(event_time=events, available_at=events+pd.Timedelta(minutes=5), source='synthetic',
                received_at=events+pd.Timedelta(minutes=5))
    candles = pd.DataFrame(dict(**meta, open=open_, high=np.maximum(open_, close)*1.001,
                                low=np.minimum(open_, close)*.999, close=close, volume=rng.uniform(100, 500, n),
                                trade_count=1000., taker_buy_volume=rng.uniform(20, 80, n)))
    frame = pd.DataFrame(meta)
    frames = dict(candles=candles, mark=frame.assign(mark_price=close), index=frame.assign(index_price=close),
                  positioning=frame.assign(open_interest=np.linspace(1e4, 2e4, n), global_ratio=1.1,
                                           top_account_ratio=1.2, top_position_ratio=1.05),
                  funding=frame.iloc[::96].assign(funding_rate=1e-4),
                  sentiment=frame.iloc[::288].assign(fear_greed=50.))
    macro_events = [dict(kind='cpi', scheduled_at=t.isoformat(), values={'CPIAUCSL': dict(change_pct=.2)})
                    for t in pd.date_range('2024-12-15 13:30', periods=6, freq='30D', tz='UTC')]
    report = er.train_experiment(tmp_path/'run', folds=1, frames=frames, runs=[], macro_events=macro_events,
                                 min_days=40, groups=('technical', 'macro'), horizons=('1h', '24h'),
                                 reserve_months=0)
    assert report['promotion_allowed'] is False
    assert {row['group'] for row in report['comparisons']} == {'technical', 'macro'}
    for row in report['comparisons']:
        assert row['windows']['train'][1] < row['windows']['test'][0]
        assert 0 <= row['brier'] <= 1 and row['interval_coverage_80'] is not None
    assert report['drop_risk'] and report['volatility']['models'] == ['ewma', 'garch']
    assert (tmp_path/'run'/'evaluation.json').exists()


def test_training_windows_are_thinned_but_evaluation_keeps_every_cutoff():
    index = pd.date_range('2025-01-01', periods=10, freq='5min', tz='UTC')
    windows = dict(train=index[:6], calibration=index[6:8], validation=index[8:9], test=index[9:])
    thinned = er.thin(windows, step=3)
    assert list(thinned['train']) == [index[0], index[3]]
    assert list(thinned['calibration']) == [index[6]]
    assert list(thinned['test']) == [index[9]] and thinned['validation'].equals(windows['validation'])
