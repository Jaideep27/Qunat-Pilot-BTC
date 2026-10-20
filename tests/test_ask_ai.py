import json
import subprocess

import pytest

from quantpilot import ask_ai, news


def fake(monkeypatch, structured, returncode=0, seen=None, error=None):
    monkeypatch.setattr(news, 'claude_executable', lambda: 'claude.exe')
    out = json.dumps(dict(type='result', is_error=bool(error), result=error or '', structured_output=structured))
    def run(args, **kw):
        if seen is not None:
            seen.update(args=args, input=kw['input'])
        return subprocess.CompletedProcess(args, returncode, stdout=out, stderr='')
    monkeypatch.setattr(news.subprocess, 'run', run)


RESULT = dict(question=dict(type='direction', horizon_hours=4), price=100.0,
              headline=dict(kind='probability', probability=.51, base_rate=.51, method='base_rate', confidence='no_edge'),
              evidence={'funding_rate': dict(value=.0001, percentile=92.0, label='funding rate')},
              events=[], news=[], warnings=[], range=None)


def test_explanations_must_cite_existing_evidence(monkeypatch):
    good = dict(answer='About a coin flip.', ai_probability=.5, agrees_with_quant=True,
                reasons=[dict(evidence_key='funding_rate', text='Funding is high.')])
    seen = {}
    fake(monkeypatch, good, seen=seen)
    assert ask_ai.explain(RESULT)['ai_probability'] == .5
    assert 'funding_rate' in seen['input'] and '--tools' in seen['args']
    bad = {**good, 'reasons': [dict(evidence_key='invented_metric', text='x')]}
    fake(monkeypatch, bad)
    with pytest.raises(ValueError, match='evidence'):
        ask_ai.explain(RESULT)


def test_parse_returns_none_for_unsupported_questions(monkeypatch):
    from datetime import datetime, timezone
    fake(monkeypatch, dict(supported=False, reason='not a market question', question=None))
    assert ask_ai.parse('write me a poem', datetime(2026, 10, 20, tzinfo=timezone.utc), 100.0) is None
    fake(monkeypatch, dict(supported=True, reason='', question=dict(type='direction', horizon_hours=6)))
    assert ask_ai.parse('btc over the next six hours?', datetime(2026, 10, 20, tzinfo=timezone.utc), 100.0).horizon_hours == 6


def test_usage_limits_surface_as_usage_limit_errors(monkeypatch):
    fake(monkeypatch, None, returncode=1, error='Claude AI usage limit reached|1792483200')
    with pytest.raises(news.UsageLimitReached):
        ask_ai.explain(RESULT)
