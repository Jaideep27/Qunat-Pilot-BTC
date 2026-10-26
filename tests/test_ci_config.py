"""GitHub Actions reads these files on every push; a broken one fails CI before any test runs."""
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_are_valid_yaml():
    for path in sorted((ROOT/'.github'/'workflows').glob('*.yml')):
        workflow = yaml.safe_load(path.read_text(encoding='utf-8'))
        assert workflow.get('jobs'), path.name


def test_ci_lock_files_cover_every_dependency():
    project = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']
    name = lambda spec: re.split(r'[\[<>=!~ ;]', spec, maxsplit=1)[0].lower().replace('_', '-')
    for lock, extras in (('requirements.lock', []), ('requirements-dev.lock', ['dev'])):
        pinned = {line.split('==')[0].lower() for line in (ROOT/lock).read_text(encoding='utf-8').splitlines() if '==' in line}
        wanted = project['dependencies'] + [spec for extra in extras for spec in project['optional-dependencies'][extra]]
        assert sorted({name(spec) for spec in wanted} - pinned) == [], lock
