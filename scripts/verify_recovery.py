"""Back up actual configured PostgreSQL data and verify it in isolated restore DBs."""
import json
from pathlib import Path
from quantpilot.recovery import backup,restore_check,sha256


def main():
    print('Creating consistent backup of configured PostgreSQL databases...',flush=True)
    result=backup()
    manifest=Path(result['manifest'])
    print(f'Backup complete: {sum(item["bytes"] for item in result["databases"]):,} bytes. Starting isolated restore drill...',flush=True)
    drill=restore_check(manifest)
    evidence={**drill,'backup_created_at':json.loads(manifest.read_text(encoding='utf-8'))['created_at'],
        'manifest_sha256':sha256(manifest),'archives':[{'role':item['role'],'bytes':item['bytes'],
            'sha256':item['sha256'],'source_bytes':item['source_bytes'],'backup_seconds':item['seconds']}
            for item in result['databases']],
        'note':'Actual local databases, not fixtures. Source metrics and dump share one exported PostgreSQL snapshot. '
            'Market rows verified by counts and timestamp ranges; accounting and checkpoint rows by SHA256. '
            'Database files remain local and ignored; this is not an off-device disaster backup.'}
    Path('docs/recovery-verification.json').write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
    Path('.verification/latest-backup.json').write_text(json.dumps({'manifest':str(manifest)},indent=2)+'\n',encoding='utf-8')
    print(json.dumps(evidence,indent=2),flush=True)


if __name__=='__main__':main()
