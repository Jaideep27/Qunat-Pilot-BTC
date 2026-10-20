import numpy as np
import pandas as pd
import pytest

from quantpilot import forecaster as fc


def synthetic_frames(days=75, seed=0):
    rng = np.random.default_rng(seed)
    n = 288*days
    events = pd.date_range('2025-01-01', periods=n, freq='5min', tz='UTC')
    close = 40000*np.exp(np.cumsum(rng.normal(0, .002, n)))
    open_ = np.r_[close[0], close[:-1]]
    meta = dict(event_time=events, available_at=events+pd.Timedelta(minutes=5))
    candles = pd.DataFrame(dict(**meta, open=open_, high=np.maximum(open_, close)*1.001,
                                low=np.minimum(open_, close)*.999, close=close, volume=rng.uniform(100, 500, n),
                                taker_buy_volume=rng.uniform(20, 80, n)))
    frame = pd.DataFrame(meta)
    return dict(candles=candles, mark=frame.assign(mark_price=close), index=frame.assign(index_price=close),
                positioning=frame.assign(open_interest=np.linspace(1e4, 2e4, n), global_ratio=1.1,
                                         top_account_ratio=1.2, top_position_ratio=1.05),
                funding=frame.iloc[::96].assign(funding_rate=1e-4),
                sentiment=frame.iloc[::288].assign(fear_greed=50.))


CHAMPIONS = dict(direction={'1h': dict(group='technical', training='expanding'), '24h': dict(group='constant', training=None)},
                 range={'1h': dict(group='price', training='expanding'), '24h': dict(group='constant', training=None)},
                 drop=dict(group='technical', training='expanding'), volatility={'1h': 'ewma', '24h': 'garch'})


@pytest.fixture(scope='module')
def fitted():
    frames = synthetic_frames()
    return frames, fc.fit(frames, CHAMPIONS, macro_events=[], min_days=20)


def test_fit_covers_each_horizon_and_records_its_training_end(fitted):
    frames, bundle = fitted
    assert set(bundle['horizons']) == {'1h', '24h'}
    assert bundle['horizons']['24h']['direction']['kind'] == 'constant'
    assert bundle['horizons']['1h']['direction']['kind'] == 'ensemble'
    assert bundle['horizons']['1h']['range']['kind'] == 'model'
    assert bundle['horizons']['24h']['range']['kind'] == 'unconditional'
    # Labels need the future: training must stop a full horizon before the data ends.
    last = pd.Timestamp(frames['candles'].available_at.max())
    assert pd.Timestamp(bundle['horizons']['1h']['direction']['training_end']) <= last-pd.Timedelta(hours=1)


def test_forecast_uses_the_latest_completed_bar_and_is_internally_consistent(fitted):
    frames, bundle = fitted
    forecasts = fc.predict(bundle, frames, macro_events=[], runs=[])
    by = {f['horizon']: f for f in forecasts}
    assert by['1h']['cutoff'] == pd.Timestamp(frames['candles'].available_at.max()).isoformat()
    one = by['1h']
    assert 0 <= one['probability_up'] <= 1 and one['status'] == 'forecast'
    assert one['return_q10'] <= one['return_q50'] <= one['return_q90']
    assert one['volatility']['ewma'] > 0 and one['volatility']['garch'] > 0
    assert one['range_status'] == 'model' and by['24h']['range_status'] == 'unconditional'
    assert by['24h']['volatility']['model'] == 'garch' and by['24h']['volatility']['value'] == by['24h']['volatility']['garch']
    assert by['24h']['status'] == 'no_validated_skill' and by['24h']['probability_up'] == by['24h']['base_rate']
    assert 0 <= by['24h']['drop5_risk'] <= 1
    assert 'news_count_1h' in one['news']


def test_forecasts_ignore_data_after_their_cutoff(fitted):
    frames, bundle = fitted
    # Forecast as of a past cutoff on the full history, then rewrite every price after that cutoff.
    cut = pd.Timestamp(frames['candles'].available_at.iloc[-300])
    before = {f['horizon']: f for f in fc.predict(bundle, frames, macro_events=[], runs=[], as_of=cut)}
    altered = {k: v.copy() for k, v in frames.items()}
    late = altered['candles'].event_time >= cut
    altered['candles'].loc[late, ['open', 'high', 'low', 'close']] *= 1.5
    redo = {f['horizon']: f for f in fc.predict(bundle, altered, macro_events=[], runs=[], as_of=cut)}
    assert before['1h']['cutoff'] == cut.isoformat()
    assert before['1h']['probability_up'] == pytest.approx(redo['1h']['probability_up'])
    assert before['1h']['return_q50'] == pytest.approx(redo['1h']['return_q50'])


def test_recording_is_idempotent_and_outcomes_score_against_the_constant(account):
    t0 = pd.Timestamp('2025-03-01T00:00', tz='UTC')
    forecast = dict(cutoff=t0.isoformat(), horizon='1h', model_version='v1', status='forecast',
                    probability_up=.7, base_rate=.5, return_q10=-.01, return_q50=.0, return_q90=.02,
                    drop5_risk=.1, close=100.0)
    assert fc.record(account, [forecast]) == 1
    assert fc.record(account, [forecast]) == 0
    closes = pd.Series([100.0, 101.0], index=[t0, t0+pd.Timedelta(hours=1)])
    assert fc.evaluate(account, closes, now=t0+pd.Timedelta(hours=2)) == 1
    scored = fc.forecasts(account)[0]
    assert scored['outcome']['up'] == 1.0 and scored['outcome']['return'] == pytest.approx(.01)
    assert scored['outcome']['brier'] == pytest.approx(.09)          # (1 - .7)^2
    assert scored['outcome']['constant_brier'] == pytest.approx(.25)  # (1 - .5)^2
    assert scored['outcome']['inside_80'] is True
    assert fc.evaluate(account, closes, now=t0+pd.Timedelta(hours=3)) == 0  # already scored


def test_forward_summary_reports_skill_only_from_scored_forecasts(account):
    t0 = pd.Timestamp('2025-03-01T00:00', tz='UTC')
    rows = [dict(cutoff=(t0+pd.Timedelta(minutes=5*i)).isoformat(), horizon='1h', model_version='v1',
                 status='forecast', probability_up=.6, base_rate=.5, return_q10=-.01, return_q50=0., return_q90=.01,
                 drop5_risk=.05, close=100.0) for i in range(3)]
    fc.record(account, rows)
    closes = pd.Series([100.0, 100., 100., 102., 99., 101.],
                       index=[t0+pd.Timedelta(minutes=5*i) for i in range(3)]+[t0+pd.Timedelta(minutes=60+5*i) for i in range(3)])
    fc.evaluate(account, closes, now=t0+pd.Timedelta(hours=3))
    summary = fc.forward_summary(account)['1h']
    assert summary['scored'] == 3 and summary['pending'] == 0
    # Outcomes up, down, up: model (.16+.36+.16)/3, constant .25.
    assert summary['brier'] == pytest.approx((.16+.36+.16)/3)
    assert summary['constant_brier'] == pytest.approx(.25)


def test_champion_spec_falls_back_to_simple_forecasts_without_a_risk_report():
    found = {'direction': ('d.json', dict(champions={'1h': dict(group='constant', training=None)}))}
    spec = fc.champion_spec(found)
    assert spec['direction'] == {'1h': dict(group='constant', training=None)}
    assert spec['range'] == {} and spec['volatility'] == {}
    assert spec['drop'] == dict(group='constant', training=None)
