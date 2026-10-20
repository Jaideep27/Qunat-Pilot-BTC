import argparse
import json
from .config import settings
from .data import acquire, audit_cached, manifest
from .store import initialize, database

def main():
    parser = argparse.ArgumentParser(prog="quantpilot")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Download/check archives and write coverage.json; nonzero exit if blocked")
    audit.add_argument("--as-of", default=None, help="UTC anchor; latest 24 complete months")
    audit.add_argument("--workers", type=int, choices=range(1, 13), default=6)
    audit.add_argument("--offline", action="store_true", help="Re-audit cached archives and Parquet without network access")
    audit.add_argument("--quarantine-positioning", action="store_true", help="Permit bounded positioning gaps by excluding affected windows; requires offline cached audit")
    recovery = sub.add_parser("repair-data", help="Try corrected and daily public archives, then historical mark/index REST")
    recovery.add_argument("--as-of", default=None)
    listing = sub.add_parser("manifest")
    listing.add_argument("--as-of", default=None)
    research = sub.add_parser("train")
    research.add_argument("--as-of", default=None)
    replay = sub.add_parser("backtest", help="Replay an already frozen holdout policy without tuning")
    replay.add_argument("--group", choices=["price","technical","derivatives","full"], default="full")
    sub.add_parser("init-db")
    sub.add_parser('import-market',help='Import existing audited Parquet into the durable market database')
    history=sub.add_parser('backfill-history',help='Resume the latest five years of BTCUSDT perpetual history')
    history.add_argument('--years',type=int,choices=range(1,16),default=None)
    history.add_argument('--all-history',action='store_true',help='Explicitly request all source history instead')
    sub.add_parser('market-coverage',help='Report exact five-year candle coverage from PostgreSQL')
    status=sub.add_parser('status',help='Read worker-run evidence, forward-paper totals and readiness')
    status.add_argument('--limit',type=int,choices=range(1,101),default=20)
    sub.add_parser('doctor',help='Read-only local database, service-version and readiness checks')
    news_collect = sub.add_parser('collect-news', help='Persist publisher feed excerpts independently of market ingestion')
    news_collect.add_argument('--once', action='store_true')
    news_collect.add_argument('--reasoning', action='store_true', help='Analyze new batches with saved ChatGPT Codex login')
    sub.add_parser('analyze-news', help='Analyze at most one bounded news batch; no trading-policy changes')
    sub.add_parser('news-status', help='News coverage, feed health and local Codex login readiness')
    sub.add_parser('shadow-forecast', help='Refit if due, record research forecasts and score finished ones')
    sub.add_parser('macro-calendar', help='Refresh official CPI, jobs and FOMC schedules and first-release values')
    study=sub.add_parser('event-study', help='Hourly 5%% move event study with matched controls (descriptive)')
    study.add_argument('--output', default=None)
    study.add_argument('--years', type=int, choices=range(1, 16), default=None)
    risk=sub.add_parser('research-risk', help='Ranges, large-drop risk and volatility scored on validation and test')
    risk.add_argument('--output', required=True, help='New directory, or an interrupted run to resume')
    risk.add_argument('--folds', type=int, choices=range(1, 27), default=4)
    experiment=sub.add_parser('research-events', help='Multi-horizon research experiment; never releases a model')
    experiment.add_argument('--output', required=True, help='New directory; existing directories are refused')
    experiment.add_argument('--folds', type=int, choices=range(1, 27), default=6)
    backup=sub.add_parser('backup-db',help='Consistent PostgreSQL custom archive with checksummed manifest')
    backup.add_argument('--database',choices=['both','operational','market'],default='both')
    backup.add_argument('--output',help='New backup directory; existing directories are never overwritten')
    restore=sub.add_parser('restore-check',help='Verify a trusted backup in a new isolated database, then remove it')
    restore.add_argument('manifest',help='Path to a locally trusted backup manifest.json')
    collector=sub.add_parser('collect-live',help='Continuously persist public live candles in PostgreSQL')
    collector.add_argument('--once',action='store_true')
    sub.add_parser("publish-research", help="Publish compact audited research projections to the operational database")
    sub.add_parser("worker")
    sub.add_parser("pause")
    sub.add_parser("resume")
    args = parser.parse_args()
    if args.command == "manifest":
        print(json.dumps(manifest(args.as_of), indent=2))
    elif args.command == "audit":
        if args.quarantine_positioning and not args.offline:
            parser.error("--quarantine-positioning requires --offline after acquisition")
        policy = {"mode":"positioning_quarantine_v1"} if args.quarantine_positioning else None
        r = audit_cached(settings.data_dir, args.as_of, policy) if args.offline else acquire(settings.data_dir, args.as_of, args.workers)
        print(json.dumps({k:v for k,v in r.items() if k != "archives"}, indent=2))
        if not r["release_ready"]: raise SystemExit(2)
    elif args.command == "repair-data":
        from .repair import repair
        result = repair(settings.data_dir, args.as_of)
        print(json.dumps(result, indent=2))
        if not result["release_ready"]: raise SystemExit(2)
    elif args.command == "train":
        from .research import train
        result = train(settings.data_dir, settings.artifact_dir, args.as_of)
        print(json.dumps(result["metadata"], indent=2))
    elif args.command == "backtest":
        from .replay import replay
        result = replay(settings.data_dir, settings.artifact_dir, args.group)
        print(json.dumps({k:v for k,v in result["trading"].items() if k not in ("curve","fills")}, indent=2))
    elif args.command == "publish-research":
        from . import services
        from .snapshots import write
        initialize(database())
        if not services.coverage()["data"].get("release_ready"):
            raise ValueError("Cannot publish research without passing coverage")
        write(database(), {"coverage":services.coverage(), "backtests":services.research()})
    elif args.command == "init-db":
        initialize(database())
        if settings.market_database_url:initialize(database(settings.market_database_url))
    elif args.command == 'import-market':
        from .market_store import import_parquet
        initialize(database(settings.market_database_url))
        import_parquet(settings.data_dir,database(settings.market_database_url))
    elif args.command == 'backfill-history':
        from .history import backfill
        backfill(years=args.years,all_history=args.all_history)
    elif args.command == 'market-coverage':
        from .market_store import window_coverage
        print(json.dumps(window_coverage(),indent=2))
    elif args.command == 'doctor':
        from .diagnostics import doctor
        result=doctor()
        print(json.dumps(result,indent=2,default=str))
        if not result['ready']:raise SystemExit(2)
    elif args.command == 'collect-news':
        from .news import run
        run(once=args.once, reasoning=args.reasoning)
    elif args.command == 'shadow-forecast':
        from .forecaster import run_cycle
        initialize(database())
        print(json.dumps(run_cycle(), indent=2, default=str))
    elif args.command == 'macro-calendar':
        from .macro import run as macro_run
        result = macro_run(once=True)
        print(json.dumps(result, indent=2, default=str))
    elif args.command == 'event-study':
        from .event_study import run as study_run
        report = study_run(args.output, args.years)
        print(json.dumps(dict(hours=report['hours'], episodes=report['episodes'], controls=report['controls'],
                              start=report['start'], end=report['end']), indent=2))
    elif args.command == 'research-risk':
        from .event_research import risk_experiment
        report = risk_experiment(args.output, folds=args.folds)
        print(json.dumps(report['summary'], indent=2, default=str))
    elif args.command == 'research-events':
        from .event_research import train_experiment
        report = train_experiment(args.output, folds=args.folds)
        print(json.dumps(report['summary'], indent=2, default=str))
    elif args.command in ('analyze-news', 'news-status'):
        from .news import analyze, snapshot, codex_status, claude_status
        initialize(database())
        result = ({p: analyze(provider=p) for p in settings.news_reasoners} if args.command == 'analyze-news'
                  else {**snapshot(), 'codex': codex_status(), 'claude': claude_status()})
        print(json.dumps(result, indent=2, default=str))
    elif args.command in ('backup-db','restore-check'):
        from .recovery import backup,restore_check,RecoveryError
        try:
            result=backup(args.database,args.output) if args.command=='backup-db' else restore_check(args.manifest)
        except Exception as exc:
            print(json.dumps({'status':'failed','error_type':type(exc).__name__,
                'reason':str(exc) if isinstance(exc,RecoveryError) else 'Recovery operation failed; inspect local database/tool availability'}))
            raise SystemExit(2) from None
        print(json.dumps(result,indent=2,default=str))
    elif args.command == 'status':
        from . import services
        result=services.operations(args.limit)
        print(json.dumps(result,indent=2,default=str))
        if not result['data']['ready']:raise SystemExit(2)
    elif args.command == 'collect-live':
        from .collector import run
        run(once=args.once)
    else:
        from .worker import run_worker, control
        r = run_worker() if args.command == "worker" else control(args.command == "pause")
        print(json.dumps(dict(equity=r["equity"], position=r["position"], paused=r["paused"],
                              last_worker=r["last_worker"]), indent=2))

if __name__ == "__main__": main()

