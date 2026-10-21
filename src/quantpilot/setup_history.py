"""'What happened the last times this setup appeared', per playbook, from a playbook research run.

For every confirmed setup in the development span: did price move the playbook's way 1h and 4h later
(with a Wilson 95% interval), the median 4h move toward the side, and the simulated plan's win rate and
mean R after costs. Descriptive history shown beside each live setup; nothing is trained or tuned.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import settings
from .event_study import wilson
from .playbook_research import _load, load_hits

PATH = settings.artifact_dir/'rule-engine'/'setup-history.json'
HORIZONS = {'1h': pd.Timedelta(hours=1), '4h': pd.Timedelta(hours=4)}


def build(output):
    hits = load_hits(output)
    bars = _load(output, 'bars')
    if hits.empty or bars.empty:
        return {}
    close = bars.close.astype(float)
    index = pd.DatetimeIndex(bars.index)
    out = {}
    for pid, group in hits.groupby('playbook'):
        s = np.where(group.side == 'long', 1.0, -1.0)
        now = close.reindex(group.time).to_numpy()
        entry = dict(setups=int(len(group)), first=str(group.time.min()), last=str(group.time.max()))
        for label, delta in HORIZONS.items():
            later_idx = index.searchsorted(pd.DatetimeIndex(group.time)+delta)
            valid = later_idx < len(index)
            later = np.where(valid, close.to_numpy()[np.minimum(later_idx, len(index)-1)], np.nan)
            move = s*(later/now-1)
            known = np.isfinite(move)
            ok = int((move[known] > 0).sum())
            low, high = wilson(ok, int(known.sum())) if known.any() else (None, None)
            entry[f'went_their_way_{label}'] = dict(n=int(known.sum()), share=ok/known.sum() if known.any() else None,
                                                     low=low, high=high)
            if label == '4h':
                entry['median_move_4h'] = float(np.median(move[known])) if known.any() else None
        plan = group[(group.get('o_valid') == True) & group.o_r.notna()] if 'o_r' in group else group.iloc[0:0]  # noqa: E712
        entry['plan'] = dict(n=int(len(plan)), win_rate=float((plan.o_net > 0).mean()) if len(plan) else None,
                             mean_r=float(plan.o_r.mean()) if len(plan) else None,
                             tradeable=int(plan.tradeable.sum()) if len(plan) else 0)
        out[pid] = entry
    return out


def write(output, path=PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(run=Path(output).name, generated=pd.Timestamp.now(tz='UTC').isoformat(),
                                    playbooks=build(output)), indent=2, default=str), encoding='utf-8')
    _read.cache_clear()
    return path


@lru_cache(maxsize=4)
def _read(path):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}


def lookup(playbook, path=PATH):
    stored = _read(str(path))
    found = stored.get('playbooks', {}).get(playbook)
    return None if found is None else dict(found, run=stored.get('run'))
