"""Owner-only PostgreSQL backups and restore drills; never restore over a live DB."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from sqlalchemy import create_engine,inspect,text
from .config import settings
from .store import database


class RecoveryError(RuntimeError):pass


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def pg_environment(url,dbname=None):
    """Keep passwords out of subprocess arguments, reports and dump manifests."""
    env=os.environ.copy()
    for name in ('PGSERVICE','PGSERVICEFILE','PGOPTIONS','PGHOSTADDR'):
        env.pop(name,None)
    env.update(PGHOST=url.host or 'localhost',PGPORT=str(url.port or 5432),
        PGUSER=url.username or '',PGDATABASE=dbname or url.database or '',
        PGPASSWORD=url.password or '',PGCONNECT_TIMEOUT='10',PGAPPNAME='quantpilot-recovery')
    for key,name in [('sslmode','PGSSLMODE'),('sslrootcert','PGSSLROOTCERT'),
        ('sslcert','PGSSLCERT'),('sslkey','PGSSLKEY'),('channel_binding','PGCHANNELBINDING')]:
        env.pop(name,None)
        if key in url.query:env[name]=str(url.query[key])
    return env


def tool(name,major):
    supplied=os.environ.get('QUANTPILOT_POSTGRES_BIN')
    candidates=[Path(supplied)/name] if supplied else []
    if os.name=='nt':candidates.append(Path(f'C:/Program Files/PostgreSQL/{major}/bin')/(name+'.exe'))
    found=shutil.which(name)
    if found:candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():return str(candidate)
    raise RecoveryError(f'{name} is unavailable; set QUANTPILOT_POSTGRES_BIN to matching PostgreSQL tools')


def invoke(arguments,env):
    try:
        result=subprocess.run(arguments,env=env,capture_output=True,timeout=1800,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RecoveryError(f'PostgreSQL tool failed: {type(exc).__name__}') from None
    if result.returncode:
        raise RecoveryError(f'PostgreSQL tool returned exit code {result.returncode}; check tool version and database permissions')
    return result


def _canonical(value):
    if isinstance(value,datetime):return value.astimezone(timezone.utc).isoformat()
    raise TypeError(f'Unsupported recovery fingerprint value: {type(value).__name__}')


def fingerprint(connection):
    """All public table counts/ranges, RLS flags, and exact accounting/projection hashes."""
    names=inspect(connection).get_table_names(schema='public')
    result={}
    for name in sorted(names):
        quoted=connection.dialect.identifier_preparer.quote(name)
        row={'rows':connection.execute(text(f'SELECT COUNT(*) FROM public.{quoted}')).scalar_one()}
        row['row_security']=connection.execute(text("SELECT relrowsecurity FROM pg_class WHERE oid=to_regclass(:name)"),{'name':'public.'+quoted}).scalar_one()
        if name in ('market_candles','market_observations'):
            kind='interval' if name=='market_candles' else 'family'
            row['ranges']=[dict(item) for item in connection.execute(text(
                f'SELECT market,{kind},COUNT(*) AS rows,MIN(event_time) AS first,MAX(event_time) AS last '
                f'FROM public.{quoted} GROUP BY market,{kind} ORDER BY market,{kind}')).mappings()]
            for item in row['ranges']:
                for key in ('first','last'):item[key]=_canonical(item[key])
        else:
            # Ordered complete rows for accounts, immutable ledger, run history,
            # projections, ingestion checkpoints and migration state.
            digest=hashlib.sha256()
            for item in connection.execution_options(stream_results=True).execute(text(f'SELECT * FROM public.{quoted} ORDER BY 1')).mappings():
                digest.update(json.dumps(dict(item),sort_keys=True,separators=(',',':'),default=_canonical).encode('utf-8'))
                digest.update(b'\n')
            row['sha256']=digest.hexdigest()
        result[name]=row
    return result


def targets(selection='both'):
    if selection not in ('both','operational','market'):raise RecoveryError('Unknown database selection')
    operational=database();market=database(settings.market_database_url)
    if selection=='operational':return {'operational':operational}
    if selection=='market':return {'market':market}
    return {'combined':operational} if operational.url==market.url else {'operational':operational,'market':market}


def backup(selection='both',output=None):
    engines=targets(selection)
    if any(engine.dialect.name!='postgresql' for engine in engines.values()):
        raise RecoveryError('Recovery commands require PostgreSQL; SQLite is not a PostgreSQL backup')
    directory=Path(output) if output else settings.data_dir/'backups'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    directory=directory.resolve();directory.mkdir(parents=True,exist_ok=False)
    result={'format':'quantpilot-postgresql-backup-v1','created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Configured PostgreSQL databases; Parquet, models, roles and environment secrets are separate',
        'databases':[]}
    for role,engine in engines.items():
        started=time.monotonic()
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection,connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            major=int(connection.execute(text('SHOW server_version_num')).scalar_one())//10000
            size=connection.execute(text('SELECT pg_database_size(current_database())')).scalar_one()
            if shutil.disk_usage(directory).free<size*2+128*1024*1024:
                raise RecoveryError('Insufficient free disk space for backup and an isolated restore drill')
            snapshot=connection.execute(text('SELECT pg_export_snapshot()')).scalar_one()
            expected=fingerprint(connection)
            archive=directory/(role+'.dump');partial=directory/(role+'.dump.partial')
            invoke([tool('pg_dump',major),'--format=custom','--no-password','--snapshot='+snapshot,
                '--lock-wait-timeout=10000','--file='+str(partial)],pg_environment(engine.url))
            partial.replace(archive)
        result['databases'].append(dict(role=role,archive=archive.name,sha256=sha256(archive),
            bytes=archive.stat().st_size,source_bytes=size,postgres_major=major,
            seconds=time.monotonic()-started,tables=expected))
    manifest=directory/'manifest.json'
    manifest.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return {'status':'backed_up','manifest':str(manifest),'databases':result['databases']}


def checked_manifest(path):
    path=Path(path).resolve()
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('format')!='quantpilot-postgresql-backup-v1' or not value.get('databases'):
        raise RecoveryError('Unrecognized backup manifest')
    for item in value['databases']:
        if item['role'] not in ('combined','operational','market'):raise RecoveryError('Unknown backup role')
        filename=item['archive']
        if not re.fullmatch(r'(combined|operational|market)\.dump',filename):raise RecoveryError('Invalid archive filename')
        archive=path.parent/filename
        if archive.resolve().parent!=path.parent or not archive.is_file():raise RecoveryError('Archive must be beside its manifest')
        if sha256(archive)!=item['sha256']:raise RecoveryError('Backup checksum mismatch; no restore attempted')
    return path,value


def restore_check(manifest):
    path,value=checked_manifest(manifest)
    results=[]
    for item in value['databases']:
        owner=database(settings.market_database_url) if item['role']=='market' else database()
        if owner.dialect.name!='postgresql':raise RecoveryError('Restore drill requires PostgreSQL')
        # No caller-controlled destination: only this newly generated database
        # can be created, restored or removed. Never use --clean on the live DB.
        name='quantpilot_restore_'+uuid.uuid4().hex[:16]
        admin=create_engine(owner.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
        restored=None;created=False;started=time.monotonic()
        try:
            with admin.connect() as connection:
                major=int(connection.execute(text('SHOW server_version_num')).scalar_one())//10000
                connection.execute(text(f'CREATE DATABASE "{name}"'))
                created=True
            invoke([tool('pg_restore',major),'--no-password','--no-owner','--no-acl',
                '--single-transaction','--dbname='+name,str(path.parent/item['archive'])],pg_environment(owner.url,dbname=name))
            restored=create_engine(owner.url.set(database=name))
            with restored.connect() as connection:actual=fingerprint(connection)
            if actual!=item['tables']:raise RecoveryError('Restored table counts, ranges, RLS flags or accounting fingerprints differ')
            results.append(dict(role=item['role'],status='passed',tables=len(actual),
                rows=sum(table['rows'] for table in actual.values()),seconds=time.monotonic()-started))
        finally:
            if restored is not None:restored.dispose()
            if created:
                with admin.connect() as connection:connection.execute(text(f'DROP DATABASE "{name}"'))
            admin.dispose()
    report={'status':'passed','checked_at':datetime.now(timezone.utc).isoformat(),'databases':results,
        'checks':['Archive SHA256','All public table row counts','Market timestamp ranges',
            'Accounting/projection/checkpoint hashes','Row-level security flags'],
        'destination':'New isolated database per archive; removed after verification',
        'live_database_replaced':False}
    (path.parent/'restore-check.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report
