# PostgreSQL backup and local recovery checks

Owner commands now create PostgreSQL custom-format archives and verify restoration into separate temporary databases. [Actual recovery evidence](recovery-verification.json) records the result; dump files and manifests stay in ignored local storage.

The recorded full backup is 90,880,746 bytes from a 1,427,486,399-byte source database. Restoration recovered seven tables and 3,639,242 rows in about 140 seconds, with all checks passing. These totals include preserved inactive archives as well as the active BTCUSDT market and operational state. The temporary database was removed; the working database was not replaced.

```powershell
uv run quantpilot doctor
uv run quantpilot backup-db
# Use the manifest path printed by backup-db:
uv run quantpilot restore-check 'data/backups/<backup-directory>/manifest.json'
```

`doctor` reads current readiness, five-year candle coverage, local API/dashboard reachability and whether the running API has loaded monitoring. It exits 2 when attention is needed. It does not fetch exchange data, execute a worker, restart a service or write coverage checkpoints.

`backup-db` covers both configured PostgreSQL databases, deduplicating them when operational accounting and market storage share a database. Use `--database operational` or `--database market` to select one. `--output` accepts a new directory and refuses to overwrite an existing one. Credentials are passed through subprocess environment variables, not command-line arguments or manifests. Matching `pg_dump`/`pg_restore` binaries must be installed; the Windows project uses PostgreSQL 18. `QUANTPILOT_POSTGRES_BIN` can select a tools directory.

Each source is read in a repeatable-read, read-only transaction. The fingerprints and dump share an exported PostgreSQL snapshot, so ongoing collector/worker writes do not move the verification baseline. Dumps are compressed custom archives, accompanied by SHA256, byte counts, table counts, market timestamp ranges, row-security flags and accounting/checkpoint hashes. PostgreSQL documents [concurrent consistent exports and custom archives](https://www.postgresql.org/docs/18/app-pgdump.html), including its `--snapshot` option.

`restore-check` first checks every archive checksum and filename. It creates a randomly named `quantpilot_restore_*` database, restores using [a single transaction](https://www.postgresql.org/docs/18/app-pgrestore.html), compares all public table counts and market timestamp ranges, verifies exact accounting/projection/checkpoint hashes and RLS flags, then removes that isolated database. It accepts no destination database name and never uses a destructive restore against the working database. Restore verification requires permission to create a database on the configured server. Only use trusted local archives: restoration executes their database definitions.

Market values are checked by archive checksum, table counts and grouped timestamp ranges; the drill does not independently compare every historical price cell. Operational account state, immutable ledger events, read projections, worker runs and ingestion checkpoints receive ordered row hashes. A successful drill proves recovery of that snapshot, not future uptime or trading performance.

The backup covers database contents rather than the entire application. Keep the frozen model/export artifacts, audited Parquet inputs and source/lockfiles separately. Environment secrets and PostgreSQL cluster roles are not exported. A backup under `data/backups` is a local recovery copy; copy its complete directory to separate storage for protection from loss of this drive. Running the drill requires free disk space for the restored database as well as the archive.

The regular-terminal reload remains `node scripts/start.cjs --restart-services`; the [operations guide](operations.md) describes activation and the agent's process-control restriction. Public deployment stays deferred.
