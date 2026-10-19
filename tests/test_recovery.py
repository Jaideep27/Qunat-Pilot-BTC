import json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy.engine import make_url
from quantpilot import recovery,diagnostics,market_store


def test_recovery_credentials_use_environment_and_override_inherited_connection(monkeypatch):
    monkeypatch.setenv('PGHOSTADDR','unrelated-host')
    monkeypatch.setenv('PGSERVICE','unrelated-service')
    monkeypatch.setenv('PGSSLMODE','disable')
    url=make_url('postgresql+psycopg://owner:private-pass@localhost:55432/research?sslmode=require')
    env=recovery.pg_environment(url,dbname='quantpilot_restore_fixture')
    assert env['PGPASSWORD']=='private-pass' and env['PGDATABASE']=='quantpilot_restore_fixture'
    assert env['PGSSLMODE']=='require' and env['PGHOST']=='localhost'
    assert 'PGSERVICE' not in env and 'PGHOSTADDR' not in env


def test_backup_rejects_sqlite_before_creating_output(account,tmp_path):
    with pytest.raises(recovery.RecoveryError,match='require PostgreSQL'):
        recovery.backup(output=tmp_path/'not-a-postgres-backup')
    assert not (tmp_path/'not-a-postgres-backup').exists()


@pytest.mark.parametrize('fault',['tampered','path_escape'])
def test_restore_rejects_corruption_and_path_escape_before_connecting(tmp_path,fault,monkeypatch):
    archive=tmp_path/'combined.dump';archive.write_bytes(b'original trusted archive')
    item={'role':'combined','archive':'combined.dump','sha256':recovery.sha256(archive)}
    if fault=='tampered':archive.write_bytes(b'changed bytes')
    else:item['archive']='../combined.dump'
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'format':'quantpilot-postgresql-backup-v1','databases':[item]}))
    def forbidden(*args):raise AssertionError('No database should be contacted')
    monkeypatch.setattr(recovery,'database',forbidden)
    with pytest.raises(recovery.RecoveryError):recovery.restore_check(manifest)


def test_failed_postgresql_tool_does_not_publish_raw_error_or_secret(monkeypatch):
    monkeypatch.setattr(recovery.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(
        returncode=1,stderr=b'private password or connection string',stdout=b''))
    with pytest.raises(recovery.RecoveryError) as failure:
        recovery.invoke(['pg_dump','--no-password'],{'PGPASSWORD':'private'})
    assert 'private' not in str(failure.value) and 'exit code 1' in str(failure.value)


def test_shared_operational_market_database_is_backed_up_once(account):
    assert list(recovery.targets())==['combined']
    assert list(recovery.targets('market'))==['market']


def test_doctor_detects_old_running_api_even_when_current_code_is_ready(account,monkeypatch):
    monkeypatch.setattr(diagnostics.services,'operations',lambda *args:{'data':{'ready':True}})
    def coverage(*args,**kwargs):
        assert kwargs['persist'] is False
        return {'markets':{'BTCUSDT':{'complete':True}}}
    monkeypatch.setattr(diagnostics,'window_coverage',coverage)
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(
        404 if request.url.path.endswith('operations') else 200,json={}))) as client:
        report=diagnostics.doctor(client)
    assert not report['ready'] and report['checks']['current_code_ready']['ok']
    assert report['checks']['running_monitor']['status']=='reload_required'
    assert report['next_action']=='node scripts/start.cjs --restart-services'


def test_doctor_reports_database_failure_without_credentials(account,monkeypatch):
    def unavailable(*args):raise ConnectionError('postgresql://owner:private@example/db')
    monkeypatch.setattr(diagnostics.services,'operations',unavailable)
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(503))) as client:
        report=diagnostics.doctor(client)
    assert not report['ready'] and 'private' not in str(report)


def test_doctor_has_no_recovery_action_when_all_checks_pass(account,monkeypatch):
    monkeypatch.setattr(diagnostics.services,'operations',lambda *args:{'data':{'ready':True}})
    monkeypatch.setattr(diagnostics,'window_coverage',lambda *args,**kwargs:{'markets':{'BTCUSDT':{'complete':True}}})
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'data':{'ready':True}}))) as client:
        report=diagnostics.doctor(client)
    assert report['ready'] and report['next_action'] is None


def test_read_only_coverage_mode_does_not_write_feed_checkpoints(account,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Read-only coverage must not write')
    monkeypatch.setattr(market_store,'feed_status',forbidden)
    result=market_store.window_coverage(account,as_of='2026-10-19',years=1,persist=False)
    assert result['markets']['BTCUSDT']['missing']>0
