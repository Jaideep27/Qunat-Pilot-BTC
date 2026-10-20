"""Selective strategy research, exactly as pre-registered (docs/research/preregistration.md).

Rule families are scored on the development span with walk-forward selection over 6-month blocks;
meta-labeling filters high-recall primaries; the pass rule is applied with the trial registry's
count; and the holdout year is evaluated once, behind a lock file. Nothing here trades.
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from . import selective, signals
from .config import settings
from .event_research import backtest_overfitting

DEV_END = pd.Timestamp('2025-10-20', tz='UTC')
HOLDOUT_END = pd.Timestamp('2026-10-20', tz='UTC')
MONTHS, BURN_IN = 6, pd.DateOffset(years=1)
WINDOWS = {'intraday': 168, 'swing': 30}
MIN_TRAIN_CALLS, MIN_META_TRAIN, MIN_META_VALIDATION = 10, 200, 20
META_CONFIGS = list(product(('logit', 'xgb'), ('expanding', 'rolling', 'recency'), (.05, .10, .25)))


def output_dir(output=None):
    return Path(output) if output else settings.artifact_dir/'strategy-research'


def blocks(first, end=DEV_END, months=MONTHS):
    """Consecutive 6-month test blocks ending at `end`; the first starts >= 1 year after `first`."""
    starts, start = [], end-pd.DateOffset(months=months)
    while start >= first+BURN_IN:
        starts.append(start)
        start -= pd.DateOffset(months=months)
    return [(s, s+pd.DateOffset(months=months)) for s in sorted(starts)]


def score_config(panel, track, signal, horizon, overlap=False):
    width = selective.barrier_width(panel.close, WINDOWS[track], horizon)
    funding = panel['funding_paid'] if 'funding_paid' in panel else None
    return selective.simulate(panel, signal, horizon, width, funding=funding, overlap=overlap)


def non_overlapping(calls):
    """One position at a time across the pooled record (block boundaries can otherwise overlap)."""
    if calls.empty:
        return calls
    keep, last_exit = [], None
    for row in calls.sort_values('entry_time').itertuples():
        if last_exit is None or row.entry_time >= last_exit:
            keep.append(row.Index)
            last_exit = row.exit_time
    return calls.loc[keep].reset_index(drop=True)


def _pbo(rows, configs):
    if configs < 2:
        return 0.0   # a single configuration involves no selection, so there is no selection bias
    return backtest_overfitting(rows, metric='score').get('family', {}).get('pbo')


def _row(fold, key, validation, test):
    return dict(horizon='family', group=key, training='rule', fold=fold, validation_constant_score=0.0,
                validation_score=-validation, constant_score=0.0, score=-test)


def walk_forward(calls_by_key, block_list, rolling_days=None, min_calls=MIN_TRAIN_CALLS):
    """Per block: pick the configuration with the best mean net return on calls that EXITED before the
    block (optionally only the last `rolling_days`), then keep its calls entering inside the block."""
    picks, tested, rows = [], [], []
    for fold, (start, end) in enumerate(block_list):
        lower = start-pd.Timedelta(days=rolling_days) if rolling_days else None
        train, test = {}, {}
        for key, calls in calls_by_key.items():
            past = calls[calls.exit_time <= start]
            if lower is not None:
                past = past[past.entry_time >= lower]
            if len(past) >= min_calls:
                train[key] = float(past.net.mean())
            test[key] = calls[(calls.entry_time >= start) & (calls.entry_time < end)]
            if key in train and len(test[key]):
                rows.append(_row(fold, key, train[key], float(test[key].net.mean())))
        key = max(train, key=train.get) if train else None
        picks.append(dict(block=fold, start=str(start), key=key, train_mean=train.get(key),
                          test_calls=len(test[key]) if key else 0))
        if key:
            tested.append(test[key])
    pooled = non_overlapping(pd.concat(tested, ignore_index=True)) if tested else pd.DataFrame(
        columns=['entry_time', 'exit_time', 'net', 'hit'])
    return pooled, picks, _pbo(rows, len(calls_by_key))


def _key(params, horizon):
    return json.dumps(dict(params, H=horizon), sort_keys=True, default=list)


def _dev(panel):
    return panel[panel.index < DEV_END]


def _bars(panel, block_list):
    if not block_list:
        return 0
    return int(((panel.index >= block_list[0][0]) & (panel.index < block_list[-1][1])).sum())


def _save(output, name, result, calls):
    folder = output_dir(output)/'families'
    folder.mkdir(parents=True, exist_ok=True)
    calls.to_parquet(folder/f'{name}.parquet')
    (folder/f'{name}.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')


def _load(output, name):
    path = output_dir(output)/'families'/f'{name}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def run_family(panel, family, registry, output):
    track, dev = family['track'], _dev(panel)
    calls_by_key = {}
    for params, horizon in signals.configurations(family):
        calls = score_config(dev, track, family['fn'](dev, **params), horizon)
        key = _key(params, horizon)
        calls_by_key[key] = calls
        rep = selective.report(calls, len(dev))
        selective.register(registry, track, family['id'], json.loads(key),
                           dict(calls=rep['calls'], mean_net=rep['mean_net'], hit_rate=rep['hit_rate'],
                                sharpe=rep['sharpe'], scope='development, all calls'))
    entries = [c.entry_time.min() for c in calls_by_key.values() if len(c)]
    block_list = blocks(min(entries)) if entries else []
    pooled, picks, pbo = walk_forward(calls_by_key, block_list)
    rolling, _, rolling_pbo = walk_forward(calls_by_key, block_list, rolling_days=730)
    bars = _bars(dev, block_list)
    result = dict(id=family['id'], track=track, kind='rule', configurations=len(calls_by_key),
                  blocks=[[str(s), str(e)] for s, e in block_list], picks=picks, pbo=pbo,
                  report=selective.report(pooled, bars),
                  rolling=dict(report=selective.report(rolling, bars), pbo=rolling_pbo))
    _save(output, family['id'], result, pooled)
    return result


def run_rules(panels, families, registry, output=None):
    results = {}
    for family in families:
        done = _load(output, family['id'])
        results[family['id']] = done or run_family(panels[family['track']], family, registry, output)
    return results


def gate_all(results, registry):
    """Apply the pass rule with each track's CURRENT trial count and cross-trial Sharpe variance."""
    for result in results.values():
        track = result['track']
        sharpes = selective.trial_sharpes(registry, track)
        result['gate'] = selective.gate(result['report'], track, selective.trial_count(registry, track),
                                        float(np.var(sharpes)) if len(sharpes) > 1 else 0.0, result['pbo'])
    return results


# Meta-labeling

def meta_features(panel, track):
    """Causal features at each bar for the meta-model (pre-registration section 6)."""
    from .event_study import STATE
    close = panel.close
    if track == 'intraday':
        frame = panel.reindex(columns=STATE).astype(float)
        share = panel.get('taker_share_1h')
        if share is not None:
            frame['taker_z'] = (share-share.rolling(168, min_periods=84).mean())/share.rolling(168, min_periods=84).std()
        frame['coinbase_premium'] = panel.get('coinbase_premium')
        frame['dvol_gap'] = panel.get('dvol', np.nan)/100-panel.realized_vol_24h*np.sqrt(365)
        return frame
    vol = np.log(close).diff().rolling(20).std()
    frame = pd.DataFrame(index=panel.index)
    for k in (1, 7, 30):
        frame[f'ret_{k}d'] = close/close.shift(k)-1
    frame['vol_20d'] = vol
    frame['vol_pct'] = signals.trailing_percentile(vol, 365, 180)
    for k in (20, 55):
        frame[f'dist_high_{k}'] = close/panel.high.shift(1).rolling(k).max()-1
        frame[f'dist_low_{k}'] = close/panel.low.shift(1).rolling(k).min()-1
    funding = panel.get('funding_rate', pd.Series(np.nan, index=panel.index)).rolling(3).mean()
    frame['funding_3d'] = funding
    frame['funding_pct'] = signals.trailing_percentile(funding, 365, 180)
    oi = panel.get('open_interest', pd.Series(np.nan, index=panel.index))
    frame['oi_7d'] = oi/oi.shift(7)-1
    premium = panel.get('coinbase_premium', pd.Series(np.nan, index=panel.index))
    frame['premium_z'] = (premium-premium.rolling(30, min_periods=15).mean())/premium.rolling(30, min_periods=15).std()
    frame['dvol_gap'] = panel.get('dvol', np.nan)/100-vol*np.sqrt(365)
    return frame.replace([np.inf, -np.inf], np.nan)


def _model(kind):
    if kind == 'logit':
        from sklearn.impute import SimpleImputer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        return make_pipeline(SimpleImputer(strategy='median'), StandardScaler(),
                             LogisticRegression(C=.1, max_iter=1000))
    from xgboost import XGBClassifier
    return XGBClassifier(max_depth=3, n_estimators=200, learning_rate=.05, min_child_weight=50, subsample=.8,
                         colsample_bytree=.8, random_state=0, n_jobs=2, verbosity=0)


def _fit(kind, x, y, weights):
    model = _model(kind)
    fit = {'logisticregression__sample_weight': weights} if kind == 'logit' else dict(sample_weight=weights)
    model.fit(x, y, **fit)
    return model


def meta_block(panel, track, side, horizon, labels, features, start, end, config):
    """Calls inside one test block for one meta configuration (empty when there is too little data)."""
    kind, scheme, rate = config
    validation_start = start-pd.DateOffset(months=MONTHS)
    train = labels[labels.exit_time <= validation_start]
    if scheme == 'rolling':
        train = train[train.entry_time >= validation_start-pd.Timedelta(days=730)]
    validation = labels[(labels.entry_time >= validation_start) & (labels.exit_time <= start)]
    if len(train) < MIN_META_TRAIN or train.hit.nunique() < 2 or len(validation) < MIN_META_VALIDATION:
        return None
    age = (validation_start-train.entry_time).dt.total_seconds()/86400
    weights = (.5**(age/180)).to_numpy() if scheme == 'recency' else np.ones(len(train))
    columns = list(features.columns)+['side']
    design = lambda rows: features.reindex(rows.entry_time).assign(side=rows.side.to_numpy())[columns].to_numpy(float)
    model = _fit(kind, design(train), train.hit.to_numpy(), weights)
    threshold = float(np.quantile(model.predict_proba(design(validation))[:, 1], 1-rate))
    candidates = labels[(labels.entry_time >= start) & (labels.entry_time < end)]
    if candidates.empty:
        return pd.DataFrame(columns=labels.columns)
    chosen = candidates[model.predict_proba(design(candidates))[:, 1] >= threshold]
    filtered = pd.Series(0, index=panel.index)
    filtered.loc[chosen.entry_time] = chosen.side.to_numpy()
    calls = score_config(panel, track, filtered, horizon)
    return calls[(calls.entry_time >= start) & (calls.entry_time < end)]


def meta_primary(panel, track, side, horizon, features, configs=None, registry=None, name='meta'):
    """Walk-forward meta-labeling of one primary over the development span (section 6)."""
    configs = configs or META_CONFIGS
    dev = _dev(panel)
    side = side.reindex(dev.index).fillna(0)
    labels = score_config(dev, track, side, horizon, overlap=True)
    block_list = blocks(dev.index.min())
    per_config = {}
    for config in configs:
        per_config[config] = [meta_block(dev, track, side, horizon, labels, features, s, e, config)
                              for s, e in block_list]
        oos = [c for c in per_config[config] if c is not None]
        rep = selective.report(non_overlapping(pd.concat(oos, ignore_index=True)) if oos else oos_empty(), len(dev))
        if registry:
            selective.register(registry, track, name, dict(model=config[0], scheme=config[1], rate=config[2],
                                                           H=horizon),
                               dict(calls=rep['calls'], mean_net=rep['mean_net'], hit_rate=rep['hit_rate'],
                                    sharpe=rep['sharpe'], scope='development, walk-forward meta'))
    # Selection across configurations: per block, the best mean net on EARLIER out-of-sample blocks.
    picks, tested, rows = [], [], []
    for fold, (start, _) in enumerate(block_list):
        prior = {}
        for config, outs in per_config.items():
            history = [c for c in outs[:fold] if c is not None and len(c)]
            if history and sum(map(len, history)) >= MIN_TRAIN_CALLS:
                prior[config] = float(pd.concat(history).net.mean())
            now = outs[fold]
            if config in prior and now is not None and len(now):
                rows.append(_row(fold, str(config), prior[config], float(now.net.mean())))
        best = max(prior, key=prior.get) if prior else None
        picks.append(dict(block=fold, start=str(start), key=list(best) if best else None,
                          prior_mean=prior.get(best)))
        if best and per_config[best][fold] is not None:
            tested.append(per_config[best][fold])
    pooled = non_overlapping(pd.concat(tested, ignore_index=True)) if tested else oos_empty()
    bars = _bars(dev, block_list)
    return dict(id=name, track=track, kind='meta', configurations=len(configs), horizon=horizon,
                blocks=[[str(s), str(e)] for s, e in block_list], picks=picks, pbo=_pbo(rows, len(configs)),
                report=selective.report(pooled, bars)), pooled


def oos_empty():
    return pd.DataFrame(columns=['entry_time', 'exit_time', 'side', 'net', 'hit'])


def run_meta(panels, registry, output=None, primaries=None):
    results = {}
    for name, primary in (primaries or signals.PRIMARIES).items():
        done = _load(output, name)
        if done:
            results[name] = done
            continue
        panel = panels[primary['track']]
        result, pooled = meta_primary(panel, primary['track'], primary['side'](panel), primary['horizon'],
                                      meta_features(panel, primary['track']), registry=registry, name=name)
        _save(output, name, result, pooled)
        results[name] = result
    return results


# Holdout (evaluated once)

def holdout(report, panels, output=None):
    """Evaluate the frozen passing configurations on the holdout year exactly once."""
    folder = output_dir(output)
    lock = folder/'holdout.lock'
    if lock.exists():
        raise RuntimeError(f'The holdout was already evaluated ({lock}); it is never run twice.')
    evaluated = []
    for name in report['passing']:
        frozen = report['frozen'][name]
        panel = panels[frozen['track']]
        signal = frozen_signal(panel, frozen)
        calls = score_config(panel[panel.index < HOLDOUT_END], frozen['track'], signal, frozen['horizon'])
        calls = calls[calls.entry_time >= DEV_END]
        rep = selective.report(calls, int(((panel.index >= DEV_END) & (panel.index < HOLDOUT_END)).sum()))
        passed = bool(rep['calls'] >= 10 and (rep['mean_net'] or 0) > 0
                      and (rep['hit_rate'] or 0) >= frozen['dev_break_even'])
        evaluated.append(dict(id=name, frozen=frozen, report=rep, passed=passed))
    folder.mkdir(parents=True, exist_ok=True)
    result = dict(evaluated=evaluated, at=pd.Timestamp.now(tz='UTC').isoformat(), span=[str(DEV_END), str(HOLDOUT_END)])
    lock.write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    return result


def frozen_signal(panel, frozen):
    """The frozen configuration's signal over the whole panel (signals only use past rows)."""
    if frozen['kind'] == 'rule':
        return signals.FAMILIES[frozen['family']]['fn'](panel, **frozen['params'])
    if frozen['family'] not in signals.PRIMARIES:
        raise NotImplementedError('holdout of a meta-labeled rule family needs its walk-forward primary')
    primary = signals.PRIMARIES[frozen['family']]
    side = primary['side'](panel)
    features = meta_features(panel, frozen['track'])
    labels = score_config(panel[panel.index < DEV_END], frozen['track'], side, frozen['horizon'], overlap=True)
    calls = meta_block(panel[panel.index < HOLDOUT_END], frozen['track'], side, frozen['horizon'], labels,
                       features, DEV_END, HOLDOUT_END, tuple(frozen['config']))
    signal = pd.Series(0, index=panel.index)
    if calls is not None and len(calls):
        signal.loc[calls.entry_time] = calls.side.to_numpy()
    return signal


def freeze(result):
    """The configuration a passing family would trade from the development end onward."""
    if result['kind'] == 'rule':
        last = next((p for p in reversed(result['picks']) if p['key']), None)
        config = json.loads(last['key'])
        horizon = config.pop('H')
        if 'lookbacks' in config:
            config['lookbacks'] = tuple(config['lookbacks'])
        return dict(kind='rule', family=result['id'], track=result['track'], params=config, horizon=horizon,
                    dev_break_even=result['report']['break_even'])
    last = next((p for p in reversed(result['picks']) if p['key']), None)
    return dict(kind='meta', family=result['id'], track=result['track'], config=last['key'],
                horizon=result['horizon'], dev_break_even=result['report']['break_even'])


# Orchestration and report

def load_panels(root=None):
    from .research_data import root_dir
    folder = root_dir(root)
    return dict(intraday=pd.read_parquet(folder/'hourly_panel.parquet'),
                swing=pd.read_parquet(folder/'daily_panel.parquet'))


def _rule_primary(panel, family, result):
    """Meta-labeling primary for a passing rule family: each block uses that block's walk-forward pick,
    and the most often picked holding period."""
    picks = [p for p in result['picks'] if p['key']]
    configs = [json.loads(p['key']) for p in picks]
    horizon = int(pd.Series([c['H'] for c in configs]).mode().iloc[0])
    side = pd.Series(0, index=panel.index)
    bounds = [pd.Timestamp(p['start']) for p in picks]+[HOLDOUT_END]
    for config, lo, hi in zip(configs, [panel.index.min()]+bounds[1:-1], bounds[1:]):
        params = {k: (tuple(v) if isinstance(v, list) else v) for k, v in config.items() if k != 'H'}
        window = (panel.index >= lo) & (panel.index < hi)
        side[window] = family['fn'](panel, **params)[window]
    return side, horizon


def run(output=None, panels=None):
    """Development research for every pre-registered family and primary; writes report.json/.md."""
    folder = output_dir(output)
    folder.mkdir(parents=True, exist_ok=True)
    registry = folder/'trials.jsonl'
    panels = panels or load_panels()
    results = run_rules(panels, list(signals.FAMILIES.values()), registry, folder)
    results.update(run_meta(panels, registry, folder))
    gate_all(results, registry)
    passing_rules = [k for k, r in results.items() if r['kind'] == 'rule' and r['gate']['passed']]
    extra = {}
    for name in passing_rules:
        family, panel = signals.FAMILIES[name], panels[results[name]['track']]
        side, horizon = _rule_primary(panel, family, results[name])
        primary = {f'{name}-meta': dict(track=family['track'], side=lambda _p, s=side: s, horizon=horizon)}
        extra.update(run_meta(panels, registry, folder, primaries=primary))
    if extra:
        results.update(gate_all(extra, registry))
    passing = [k for k, r in results.items() if r['gate']['passed']]
    report = dict(generated=pd.Timestamp.now(tz='UTC').isoformat(), development_end=str(DEV_END),
                  holdout=[str(DEV_END), str(HOLDOUT_END)],
                  trials={t: selective.trial_count(registry, t) for t in ('intraday', 'swing')},
                  results=results, passing=passing,
                  frozen={k: freeze(results[k] | ({'id': k.removesuffix('-meta')} if k.endswith('-meta') else {}))
                          for k in passing})
    (folder/'report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    (folder/'report.md').write_text(render(report), encoding='utf-8')
    return report


def _pct(value, digits=1):
    return '—' if value is None else f'{value*100:.{digits}f}%'


def render(report):
    lines = ['# Selective strategy research: development results', '',
             f"Development span ends {report['development_end'][:10]}. Trials registered: "
             f"intraday {report['trials']['intraday']}, swing {report['trials']['swing']}.", '',
             '| Family | Track | Configs | Calls | Call rate | Hit rate (95% CI) | Break-even | Mean net/call '
             '| Profit factor | Deflated Sharpe | PBO | Years positive | Verdict |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for name, r in report['results'].items():
        rep, gate = r['report'], r['gate']
        years = [y for y in rep.get('by_year', {}).values() if y['calls'] >= 5]
        positive = f"{sum(y['mean_net'] > 0 for y in years)}/{len(years)}" if years else '—'
        ci = f"{_pct(rep['hit_rate'])} ({_pct(rep['wilson_low'])}–{_pct(rep['wilson_high'])})" if rep['calls'] else '—'
        failed = [k for k, ok in gate['criteria'].items() if not ok]
        verdict = 'PASS' if gate['passed'] else 'fail: '+', '.join(failed)
        pf = '—' if rep.get('profit_factor') is None else f"{rep['profit_factor']:.2f}"
        pbo = '—' if r['pbo'] is None else f"{r['pbo']:.2f}"
        lines.append(f"| {name} | {r['track']} | {r['configurations']} | {rep['calls']} | {_pct(rep['coverage'], 2)} "
                     f"| {ci} | {_pct(rep['break_even'])} | {_pct(rep['mean_net'], 3)} | {pf} "
                     f"| {gate['deflated_sharpe']:.2f} | {pbo} | {positive} | {verdict} |")
    lines += ['', '## Robustness: rolling 2-year selection (reported, not used for the verdict)', '',
              '| Family | Calls | Hit rate | Mean net/call | PBO |', '|---|---|---|---|---|']
    for name, r in report['results'].items():
        if r['kind'] == 'rule':
            rep = r['rolling']['report']
            pbo = '—' if r['rolling']['pbo'] is None else f"{r['rolling']['pbo']:.2f}"
            lines.append(f"| {name} | {rep['calls']} | {_pct(rep['hit_rate'])} | {_pct(rep['mean_net'], 3)} | {pbo} |")
    lines += ['', f"Passing on development: {', '.join(report['passing']) or 'none'}."]
    return '\n'.join(lines)+'\n'
