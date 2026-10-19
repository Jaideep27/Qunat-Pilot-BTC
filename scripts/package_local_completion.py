"""Package current local completion evidence without rewriting frozen research results."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def save(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')


def main():
    baseline=read('docs/local-readiness-verification.json')
    # The application, Python tests and dashboard did not change in this stage.
    # Reuse their recorded executions only when the exact source still matches.
    for path,value in baseline['source_sha256'].items():
        if path.startswith(('src/','frontend/','tests/','migrations/')):
            assert digest(path)==value,f'Baseline application source changed: {path}'
    node=ET.parse('.verification/service-readiness.xml').getroot()
    assert len(node.findall('.//testcase'))==7
    assert not any(node.findall('.//'+kind) for kind in ('failure','error','skipped'))
    static=read('docs/static-checks.json');assert len(static['passed'])==7
    persistence=read('docs/persistence-runtime.json')
    assert persistence['status']=='periodic_postgresql_updates_verified'
    recording=read('docs/walkthrough/recording.json')
    decoded=read('docs/walkthrough/verification.json')
    video='docs/walkthrough/quantpilot-btc.webm'
    assert decoded['status']=='passed' and 119<=decoded['duration_seconds']<=125
    assert decoded['width']==recording['width']==1440 and decoded['height']==recording['height']==1000
    assert digest(video)==recording['video_sha256'] and not recording['page_errors']
    assert len(recording['scenes'])==len(recording['captures'])==6
    assert sum(scene['seconds'] for scene in recording['scenes'])==120
    for capture in recording['captures']:assert digest('docs/walkthrough/'+capture['file'])==capture['sha256']
    check=subprocess.run(['node','-e',"require('./scripts/service_readiness.cjs').check().then(value=>process.stdout.write(JSON.stringify(value)))"],
        capture_output=True,text=True,check=True,timeout=30)
    runtime=json.loads(check.stdout)
    runtime['checked_at']=datetime.now(timezone.utc).isoformat()
    save('docs/completion-readiness.json',runtime)
    sources=[*Path('src/quantpilot').glob('*.py'),*Path('frontend/src').glob('*.*'),
        *Path('tests').glob('*.py'),*Path('frontend/tests').glob('*.ts'),*Path('migrations').rglob('*.py'),
        *map(Path,['scripts/start.cjs','scripts/service_readiness.cjs','scripts/record_walkthrough.cjs',
            'scripts/verify_walkthrough.cjs','scripts/verify_persistence.cjs','scripts/verify.cjs',
            'scripts/package_local_completion.py','tests/service-readiness.test.cjs','.github/workflows/ci.yml'])]
    evidence=['docs/local-readiness-verification.json','.verification/service-readiness.xml','docs/static-checks.json',
        'docs/persistence-runtime.json','docs/walkthrough/recording.json','docs/walkthrough/verification.json',
        'docs/walkthrough/runtime-snapshot.json','docs/completion-readiness.json']
    result=dict(verified_at=datetime.now(timezone.utc).isoformat(),
        status='local_ready_stream_latency_unverified' if runtime['ready'] else 'local_checks_verified_service_reload_pending',
        public_deployment='deferred_by_owner',baseline_evidence='docs/local-readiness-verification.json',
        baseline_verified_at=baseline['verified_at'],
        application_regression=dict(python_passed=baseline['python']['passed'],browser_passed=baseline['browser']['passed'],
            postgres_checks_passed=baseline['postgres']['passed'],build='passed',
            note='Recorded baseline executions reused after verifying unchanged application/test source hashes'),
        startup_readiness_tests=dict(passed=7,failures=0,evidence='.verification/service-readiness.xml'),
        static_checks_passed=7,runtime=runtime,persistence=persistence,
        walkthrough=dict(path=video,seconds=decoded['duration_seconds'],bytes=Path(video).stat().st_size,
            sha256=digest(video),scenes=6,actual_database_snapshots=True,continuous_stream_demonstrated=False,
            metadata='docs/walkthrough/recording.json'),
        remaining=['Reload the existing API/supervisor from the regular terminal and observe instrumented runs',
            'Measure actual browser WebSocket behavior in a provider-enabled environment',
            'Accumulate sustained forward-operation evidence'],
        activation_command='node scripts/start.cjs --restart-services',
        process_control_blocker='Prior automatic approval review rejected stopping the existing API because sandbox approval is disabled; no repeat stop attempt',
        provider_blocker='Actual exchange WebSocket latency remains unverified under the agent browser network policy',
        source_sha256={path.as_posix():digest(path) for path in sorted(set(sources))},
        evidence_sha256={path:digest(path) for path in evidence})
    save('docs/local-completion-verification.json',result)
    latest=read('docs/latest-verification.json')
    latest.update(verified_at=result['verified_at'],status=result['status'],evidence='docs/local-completion-verification.json',
        previous_evidence='docs/local-readiness-verification.json',ready=runtime['ready'],
        running_api_loaded_monitoring=runtime['status'] in ('ready','needs_attention'),
        readiness_evidence='docs/completion-readiness.json',startup_readiness_tests_passed=7,
        persistence_evidence='docs/persistence-runtime.json',walkthrough='docs/walkthrough/quantpilot-btc.webm',
        walkthrough_duration_seconds=decoded['duration_seconds'])
    save('docs/latest-verification.json',latest)
    print(json.dumps(dict(status=result['status'],startup_checks_passed=7,median_saved_snapshot_interval_ms=persistence['median_saved_snapshot_interval_ms'],
        walkthrough_seconds=decoded['duration_seconds'],service_readiness=runtime['status']),indent=2))


if __name__=='__main__':main()
