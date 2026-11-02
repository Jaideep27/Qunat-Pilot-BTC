"""Database engines, table creation and cross-process serialization."""
from contextlib import contextmanager
from functools import lru_cache
from filelock import FileLock
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session
from quantpilot.config import settings

class Base(DeclarativeBase):
    pass

def database(url=None):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = url or settings.database_url
    return _database(url)

@lru_cache(maxsize=8)
def _database(url):
    return create_engine(url, connect_args={"check_same_thread":False} if url.startswith("sqlite") else {},
                         pool_pre_ping=True)

def initialize(engine):
    from quantpilot.storage import market as market_store  # noqa: F401  durable historical/live market tables
    from quantpilot.ai import news, chat  # noqa: F401  news with its AI analysis, and in-app chat
    from quantpilot.data import macro  # noqa: F401  the official macro release calendar
    from quantpilot.research import forecaster  # noqa: F401  shadow forecasts and their scored outcomes
    from quantpilot.outlook import answers  # noqa: F401  outlook answers and scores
    from quantpilot.rules import journal  # noqa: F401  the archived rule engine journal (read-only now)
    from quantpilot.analysis import snapshot, ai  # noqa: F401  analysis snapshots, briefs and explanations
    with serialized(engine) as session:
        Base.metadata.create_all(session.connection())
        if engine.dialect.name == "postgresql":
            # Only tables without row-level security: ALTER TABLE takes an exclusive lock, and every service start
            # runs this, so re-applying it to every table blocked readers of the big candle table.
            missing = session.execute(text("SELECT relname FROM pg_class WHERE relkind='r' AND NOT relrowsecurity "
                                           "AND relname = ANY(:names)"), dict(names=list(Base.metadata.tables))).scalars()
            for table in missing:
                session.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))

@contextmanager
def serialized(engine):
    if engine.dialect.name == "postgresql":
        with Session(engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(71725001)"))
            yield session
    else:
        with FileLock(str(settings.data_dir / "worker.lock"), timeout=1):
            with Session(engine) as session, session.begin():
                yield session
