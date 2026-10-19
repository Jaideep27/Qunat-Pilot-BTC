# Deployment runbook

Public deployment has **not** been performed. No hosting credentials, configured Supabase project or remote repository URL were supplied to this workspace. The checked-in files prepare deployment; they are not evidence of a running service.

1. Create a GitHub repository from these files, preserving the requirements report and excluding `.env`, raw data, generated runtimes, caches and local database. Include the generated dependency lockfiles and trusted `release/` exports. Run the quality workflow to green before public release. Local verification already passed 55 Python cases, five browser cases and all nine real-data MCP tool comparisons.
2. Create a Supabase PostgreSQL project. Put its SSL-required session-pooler connection string in `QUANTPILOT_DATABASE_URL` locally and in hosting/GitHub secrets. Use the `postgresql+psycopg://` scheme, URL-encode credential characters, and include `sslmode=require`. The browser gets no database credentials.
3. Run `uv run alembic upgrade head`, then `uv run quantpilot init-db`. Application initialization also creates missing operational tables for SQLite development. PostgreSQL tables use RLS with no public policies; the backend/worker connect through the owner's protected database role.
4. Complete the historical audit and measured evaluation on the training laptop. Run `uv run quantpilot publish-research` to store coverage and compact evaluation projections in PostgreSQL. Run `uv run python scripts/export_release.py`; commit the resulting trusted `release/` model/evaluation files for the worker. Never commit historical Parquet or secrets. Do not export if coverage fails.
5. Import `render.yaml` into Render. Set `QUANTPILOT_DATABASE_URL` and `QUANTPILOT_CORS_ORIGINS` (a JSON array containing the exact HTTPS frontend origin). Confirm `/api/v1/health`. Render reads recent worker snapshots from PostgreSQL, so ephemeral local storage does not erase market/portfolio views.
6. Import the repository into Vercel with root directory `frontend`. Set `VITE_API_URL` to the Render HTTPS origin, with no trailing slash. Build using `npm run build`. Update Render CORS to the final frontend origin.
7. In GitHub repository secrets set `QUANTPILOT_DATABASE_URL`. Manually run the paper workflow once and inspect actual database checkpoints. Its cron is UTC minutes 2, 17, 32 and 47. GitHub concurrency plus PostgreSQL advisory locks prevent concurrent ledger writers.
8. Verify all four views against the real API, the MCP client, stale/disconnected behavior, worker retries and the paper ledger. Publish a demo URL and screenshots only after this passes.

Before any model exists, the worker may still ingest live market data and persist explicit no-trade decisions. It will not create entries using a preview or fabricated model. PostgreSQL read snapshots allow the API to serve this honest blocked state.

Historical data downloads and training run directly through services. MCP tools are interactive reads; they neither schedule ingestion nor expand provider limits.

Free hosting remains conditional on usage and provider terms: [Vercel Hobby personal/non-commercial restrictions](https://vercel.com/docs/plans/hobby), [Render free service spin-down and storage limits](https://render.com/docs/free), [Supabase free allowances](https://supabase.com/pricing), and [GitHub scheduled workflow delays/drops](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). No paid services are selected by this repository.

Monitor database size and archive the immutable ledger to local durable storage before pruning operational history. The app bounds recent read arrays to 2,000 rows, but ledger rows are retained for accounting audit. Free-tier uptime and storage longevity have not been measured.


Dependency reproducibility: `uv.lock`, `requirements.lock`, `requirements-dev.lock` and `frontend/package-lock.json` are now generated. Docker and Python workflows install exported versions with `--require-hashes`, then install this project with `--no-deps`; frontend CI uses `npm ci`. After an intentional dependency change, update the uv lock, re-export both requirements files, and rerun verification. Local verification uses `uv sync --locked --extra dev`.
