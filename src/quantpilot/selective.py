"""Selective-call scoring: a call is a trade that exits at a take-profit, a stop or a time limit.

Calls are simulated one position at a time, so every scored call is independent of the others.
Returns are net of the engine's fee and slippage (both sides) and of funding settlements crossed
while the position is open. "Hit rate" always means the share of calls with a net return above zero,
and it is always reported with the call rate. See docs/research/preregistration.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from .config import RiskConfig
from .event_study import wilson

_RISK = RiskConfig()
ROUND_TRIP = 2*(_RISK.fee_bps+_RISK.slippage_bps)/1e4
MIN_CALLS = {'intraday': 100, 'swing': 50}
EULER = .5772156649
SETTLEMENT_HOURS = (0, 8, 16)


def barrier_width(close, window, horizon, k=1.0):
    """Expected move over `horizon` bars as a fraction: past std of log returns x sqrt(horizon)."""
    return np.log(close).diff().rolling(window).std()*np.sqrt(horizon)*k


def settlement_funding(rates):
    """Funding a long pays in each bar: the rate in effect at settlement bars (00/08/16 UTC), else zero."""
    index = pd.DatetimeIndex(rates.index)
    settles = np.isin(index.hour, SETTLEMENT_HOURS) & (index.minute == 0)
    return rates.where(settles, 0.0).fillna(0.0)


def simulate(frame, signal, horizon, width, funding=None, round_trip=ROUND_TRIP, overlap=False):
    """Score every call of `signal` (+1 long, -1 short, 0 none) one position at a time.

    Entry at the signal bar's close; barriers at +/- width x entry; time exit at the close `horizon`
    bars later. A bar touching both barriers counts as the stop. Calls without a complete future path
    are not scored. With `overlap`, every signal bar is scored (labels for meta-labeling).
    """
    close, high, low = (frame[c].to_numpy(float) for c in ('close', 'high', 'low'))
    sig = pd.Series(signal, index=frame.index).reindex(frame.index).fillna(0).to_numpy(float)
    w = pd.Series(width, index=frame.index).reindex(frame.index).to_numpy(float)
    paid = (np.zeros(len(frame)) if funding is None
            else pd.Series(funding).reindex(frame.index).fillna(0).to_numpy(float))
    rows, n, i = [], len(frame), 0
    while i < n:
        s = sig[i]
        if s == 0 or not np.isfinite(w[i]) or w[i] <= 0:
            i += 1
            continue
        end = i+horizon
        if end >= n:
            break
        path = slice(i, end+1)
        if not (np.isfinite(close[path]).all() and np.isfinite(high[path]).all() and np.isfinite(low[path]).all()):
            i += 1
            continue
        entry = close[i]
        take, stop = entry*(1+s*w[i]), entry*(1-s*w[i])
        exit_price, reason, j = close[end], 'time', end
        for k in range(i+1, end+1):
            stopped = low[k] <= stop if s > 0 else high[k] >= stop
            taken = high[k] >= take if s > 0 else low[k] <= take
            if stopped or taken:
                exit_price, reason = (stop, 'sl') if stopped else (take, 'tp')
                j = k
                break
        gross = s*(exit_price/entry-1)
        carry = float(paid[i+1:j+1].sum())
        net = gross-round_trip-s*carry
        rows.append(dict(entry_time=frame.index[i], exit_time=frame.index[j], side=int(s), entry=entry,
                         exit=exit_price, reason=reason, bars_held=j-i, gross=gross, funding=carry, net=net,
                         hit=int(net > 0)))
        i = i+1 if overlap else j
    columns = ['entry_time', 'exit_time', 'side', 'entry', 'exit', 'reason', 'bars_held', 'gross', 'funding',
               'net', 'hit']
    return pd.DataFrame(rows, columns=columns)


def report(calls, bars):
    """Hit rate with its Wilson 95% interval, break-even hit rate, net expectancy and per-year results."""
    n = len(calls)
    out = dict(calls=n, bars=int(bars), coverage=n/bars if bars else None)
    if not n:
        return out | dict(hit_rate=None, wilson_low=None, wilson_high=None, break_even=None, mean_net=None,
                          median_net=None, profit_factor=None, sharpe=None, skew=None, kurtosis=None, by_year={})
    net = calls.net.astype(float)
    wins, losses = net[net > 0], -net[net <= 0]
    hits = int((net > 0).sum())
    low, high = wilson(hits, n)
    if len(wins) and len(losses):
        break_even = float(losses.mean()/(wins.mean()+losses.mean()))
    else:
        break_even = 0.0 if len(wins) else 1.0
    std = float(net.std(ddof=1)) if n > 1 else 0.0
    years = pd.DatetimeIndex(calls.entry_time).year
    by_year = {int(y): dict(calls=int(len(g)), mean_net=float(g.mean()), hit_rate=float((g > 0).mean()))
               for y, g in net.groupby(years)}
    return out | dict(
        hit_rate=hits/n, wilson_low=low, wilson_high=high, break_even=break_even, mean_net=float(net.mean()),
        median_net=float(net.median()), profit_factor=float(wins.sum()/losses.sum()) if losses.sum() > 0 else None,
        sharpe=float(net.mean()/std) if std > 0 else None,
        skew=float(stats.skew(net)) if n > 2 else 0.0,
        kurtosis=float(stats.kurtosis(net, fisher=False)) if n > 3 else 3.0, by_year=by_year)


def deflated_sharpe(sharpe, n, skew, kurtosis, trials, sharpe_variance):
    """Probability that the true per-call Sharpe exceeds the best expected from `trials` unskilled tries
    (Bailey and Lopez de Prado's deflated Sharpe ratio)."""
    if trials > 1 and sharpe_variance > 0:
        sr0 = np.sqrt(sharpe_variance)*((1-EULER)*stats.norm.ppf(1-1/trials)
                                        + EULER*stats.norm.ppf(1-1/(trials*np.e)))
    else:
        sr0 = 0.0
    spread = np.sqrt(max(1e-12, 1-skew*sharpe+(kurtosis-1)/4*sharpe**2))
    return float(stats.norm.cdf((sharpe-sr0)*np.sqrt(max(n-1, 1))/spread))


def gate(rep, track, trials, sharpe_variance, pbo):
    """The pre-registered pass rule (section 7). Every criterion must hold."""
    n = rep.get('calls', 0)
    dsr = (deflated_sharpe(rep['sharpe'], n, rep['skew'], rep['kurtosis'], trials, sharpe_variance)
           if n > 1 and rep.get('sharpe') is not None else 0.0)
    years = [y for y in rep.get('by_year', {}).values() if y['calls'] >= 5]
    criteria = dict(
        enough_calls=n >= MIN_CALLS[track],
        beats_break_even=bool(rep.get('wilson_low') is not None and rep['wilson_low'] > rep['break_even']),
        positive_mean=bool((rep.get('mean_net') or 0) > 0),
        deflated_sharpe=dsr >= .95,
        low_overfitting=bool(pbo is not None and pbo < .3),
        most_years_positive=bool(years) and np.mean([y['mean_net'] > 0 for y in years]) >= .6)
    criteria = {k: bool(v) for k, v in criteria.items()}
    return dict(passed=all(criteria.values()), criteria=criteria, deflated_sharpe=dsr, trials=trials)


def register(path, track, family, config, result):
    """Append one evaluated configuration to the trial registry (failures included)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(track=track, family=family, config=config, key=json.dumps(config, sort_keys=True),
                  at=pd.Timestamp.now(tz='UTC').isoformat(), **result)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, default=str)+'\n')


def _trials(paths, track):
    """Distinct (family, configuration) records of a track across one or more registry files."""
    seen = {}
    for path in ([paths] if isinstance(paths, (str, Path)) else paths):
        path = Path(path)
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            record = json.loads(line)
            if record['track'] == track:
                seen.setdefault((record['family'], record['key']), record)
    return seen


def trial_count(paths, track):
    return len(_trials(paths, track))


def trial_sharpes(paths, track, min_calls=None):
    """Per-trial Sharpe ratios; `min_calls` drops trials too small to estimate one (amendment A3)."""
    return [r['sharpe'] for r in _trials(paths, track).values() if r.get('sharpe') is not None
            and (min_calls is None or (r.get('calls') or 0) >= min_calls)]
