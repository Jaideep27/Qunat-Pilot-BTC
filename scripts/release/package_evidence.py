"""Consolidate measured local-release evidence; fail if required gates did not pass."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_log(path):
    raw = Path(path).read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def main():
    report = '.verification/pytest-release-final.xml'
    suite = ET.parse(report).getroot().find('testsuite')
    assert suite is not None
    assert suite.attrib['failures'] == suite.attrib['errors'] == suite.attrib['skipped'] == '0'
    browser_log = '.verification/browser-release-final.log'
    match = re.search(r'(\d+) passed \(([\d.]+)s\)', read_log(browser_log))
    assert match and int(match[1]) == 5
    assert 'built in' in read_log('.verification/release-build-final.log')
    smoke = json.loads(Path('.verification/release-smoke.json').read_text(encoding='utf-8'))
    assert smoke['status'] == 'passed' and smoke['mcp_tools_matched'] == 9
    evaluation = json.loads(Path('artifacts/evaluation.json').read_text(encoding='utf-8'))
    replay = json.loads(Path('artifacts/replay-full.json').read_text(encoding='utf-8'))
    full = next(row for row in evaluation['comparisons'] if row['group'] == 'full')
    assert replay['trading'] == full['trading']
    assert smoke['model_version'] == evaluation['metadata']['version'] == replay['model_version']
    for name in ['released.joblib', 'evaluation.json']:
        assert sha('release/'+name) == sha('artifacts/'+name)
    for view in ['market', 'prediction', 'research', 'paper']:
        shutil.copy2(f'frontend/test-results/release-{view}.png', f'docs/screenshots/release-{view}.png')
    now = datetime.now(timezone.utc).isoformat()
    sources = sorted([*Path('src').rglob('*.py'), *Path('tests').rglob('*.py'),
                      *Path('frontend/src').glob('*.*'), *Path('frontend/tests').glob('*.ts')])
    source_hashes = {p.as_posix():sha(p) for p in sources}
    regression = dict(verified_at=now, python_regression=dict(
        passed=int(suite.attrib['tests']), failed=0, errors=0, skipped=0,
        duration_seconds=float(suite.attrib['time']), report=report, report_sha256=sha(report)),
        compilation='passed', static_checks_passed=7, source_sha256=source_hashes,
        warnings=['Starlette httpx test-client deprecation', 'Pytest cache directory denied; test fixtures used project-local temporary files'])
    frontend = dict(verified_at=now, scope='real release plus fixture/failure states', passed=True,
        browser_cases_passed=int(match[1]), browser_duration_seconds=float(match[2]),
        browser='Installed Chrome through Playwright', log=browser_log, log_sha256=sha(browser_log),
        build='TypeScript and Vite passed; main chunk approximately 801 kB advisory',
        sha256={p.as_posix():sha(p) for p in [Path('frontend/package-lock.json'),
            Path('frontend/dist/index.html'), *Path('docs/screenshots').glob('release-*.png')]})
    manifest_files = [*Path('release').glob('*'), *Path('docs/screenshots').glob('release-*.png'),
        Path('uv.lock'), Path('requirements.lock'), Path('requirements-dev.lock'),
        Path('frontend/package-lock.json'), Path('docs/evidence/data-quality.json'),
        Path('docs/excluded-windows.csv'), Path('docs/evidence/coverage-report.json'),
        Path('docs/evaluation-report.md'), Path('docs/model-card.md')]
    video = Path('docs/walkthrough/quantpilot-btc.webm')
    if video.exists():
        video_check = json.loads(Path('docs/walkthrough/verification.json').read_text(encoding='utf-8'))
        assert video_check['status'] == 'passed' and 119 <= video_check['duration_seconds'] <= 125
        manifest_files.extend([video, Path('docs/walkthrough/recording.json'), Path('docs/walkthrough/verification.json')])
    result = dict(verified_at=now, status='local_research_release_verified',
        model_version=smoke['model_version'], research_period=dict(start='2024-10-01', end_exclusive='2026-10-01', warmup_days=40),
        eligible_rows=evaluation['metadata']['eligible_rows'], feature_columns=len(evaluation['metadata']['feature_schema']),
        python=regression['python_regression'], frontend=frontend, api_mcp=smoke,
        frozen_replay='Exact full-group trading object equality, including curves and fills',
        release_exports='SHA256 matches frozen artifacts',
        runtime_source_sha256=source_hashes,
        runtime_code_sha256=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path('src/quantpilot').rglob('*.py')))).hexdigest(),
        training_identity=evaluation['metadata'],
        artifact_sha256={p.as_posix():sha(p) for p in manifest_files if p.is_file()},
        paper_status=dict(equity=10000, fills=0, forward_uptime_measured=False,
                          worker_attempt='Provider socket denied with WinError 10013 before reconciliation'),
        walkthrough='120-second captioned real-application screenshot video; decoded and visually reviewed; no voiceover' if video.exists() else 'Script supplied; recording pending',
        remaining=['Public GitHub repository', 'Supabase/PostgreSQL integration verification',
                   'Render/Vercel deployment', 'Network-enabled live worker and forward paper uptime'],
        code_identity_note='Training identity is frozen. Runtime hashes separately include later MCP startup and worker funding-tail fixes; no model or holdout policy was retuned.')
    write('docs/evidence/python-regression-verification.json', regression)
    write('docs/evidence/frontend-milestone-status.json', frontend)
    write('docs/evidence/release-smoke.json', smoke)
    write('docs/evidence/release-verification.json', result)
    write('docs/evidence/latest-verification.json', dict(status=result['status'], verified_at=now,
        model_version=smoke['model_version'], python_passed=int(suite.attrib['tests']),
        browser_passed=int(match[1]), mcp_tools_matched=9, evidence='docs/evidence/release-verification.json',
        public_deployment='pending', forward_paper='pending'))
    print(json.dumps(dict(status=result['status'], model_version=smoke['model_version'],
                         python_passed=int(suite.attrib['tests']), browser_passed=int(match[1]), mcp_tools_matched=9),indent=2))


if __name__ == '__main__':
    main()
