"""Decision layers for the live rule engine: ML filter (M3), news check (M4) and learner (M5).

Each layer is a callable `(hit, features) -> verdict dict`. The arbiter records every verdict and acts on
one only where that layer is `live` for the playbook. Live modes are earned through the pre-registered
gates and stored in artifacts/rule-engine/modes.json; without that file every layer is in shadow.
A layer whose model or data is missing returns `{'verdict': 'none'}` and never blocks a trade.
"""
from __future__ import annotations

import json

from .config import settings

MODES = settings.artifact_dir/'rule-engine'/'modes.json'


def modes():
    """{(layer, playbook): 'live'} from the gate results."""
    if not MODES.exists():
        return {}
    stored = json.loads(MODES.read_text(encoding='utf-8'))
    return {(layer, pid): mode for layer, playbooks in stored.items() for pid, mode in playbooks.items()}


def live_layers():
    """The layer callables available now (each loads its own artifacts and degrades to 'none')."""
    found = {}
    for name, factory in (('ml', _ml), ('news', _news), ('learner', _learner), ('forecast', _forecast)):
        fn = factory()
        if fn is not None:
            found[name] = fn
    return found


def news_events():
    """Classified high-impact news for the NEWS_SHOCK triggers (empty until M4 classification exists)."""
    try:
        from . import news_events as module
    except ImportError:
        return []
    return module.recent_events()


def _ml():
    try:
        from . import meta
    except ImportError:
        return None
    return meta.live_filter()


def _news():
    try:
        from . import news_events as module
    except ImportError:
        return None
    return module.live_check()


def _forecast():
    """Shadow-only record of the research forecaster's latest calls (never acts: trained on the
    development span, so it was not usable as a historical input; amendment A6)."""
    from . import forecaster
    from .store import database
    def verdict(hit, features):
        try:
            rows = forecaster.forecasts(database(), limit=12)
        except Exception:
            return dict(verdict='none')
        latest = {}
        for row in rows:
            latest.setdefault(row.get('horizon'), row)
        pick = {h: dict(probability_up=r.get('probability_up'), return_q10=r.get('return_q10'),
                        return_q90=r.get('return_q90'), cutoff=r.get('cutoff')) for h, r in latest.items() if h in ('1h', '4h')}
        return dict(verdict='none', forecasts=pick)
    return verdict


def _learner():
    try:
        from . import learner
    except ImportError:
        return None
    return learner.live_policy()
