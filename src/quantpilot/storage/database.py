"""Transactional state, immutable event IDs, and cross-process serialization."""
from contextlib import contextmanager
from functools import lru_cache
from filelock import FileLock
from sqlalchemy import JSON, Column, Integer, String, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session
from quantpilot.config import settings
from quantpilot.simulation.engine import initial, reduce_event
from quantpilot.data.archives import utc

class Base(DeclarativeBase):
    pass

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    state = Column(JSON, nullable=False)

class LedgerEvent(Base):
    __tablename__ = "ledger_events"
    id = Column(String(160), primary_key=True)
    event_time = Column(String(40), nullable=False, index=True)
    payload = Column(JSON, nullable=False)

def database(url=None):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = url or settings.database_url
    return _database(url)

@lru_cache(maxsize=8)
def _database(url):
    return create_engine(url, connect_args={"check_same_thread":False} if url.startswith("sqlite") else {},
                         pool_pre_ping=True)

def initialize(engine):
    from quantpilot.storage import snapshots  # noqa: F401  register all operational tables
    from quantpilot.storage import market as market_store  # noqa: F401  register durable historical/live market tables
    from quantpilot.monitoring import operations  # noqa: F401  register persistent worker-run evidence
    from quantpilot.ai import news  # noqa: F401  register versioned news and reasoning evidence
    from quantpilot.data import macro  # noqa: F401  register the official macro release calendar
    from quantpilot.research import forecaster  # noqa: F401  register shadow forecasts and their scored outcomes
    from quantpilot.outlook import answers  # noqa: F401  register outlook answers and scores
    from quantpilot.ai import chat  # noqa: F401  register in-app chat threads and messages
    from quantpilot.rules import journal  # noqa: F401  register the rule engine journal
    from quantpilot.accounts import journal as account_journal  # noqa: F401  register the four paper accounts (A8)
    from quantpilot.ai import news_events  # noqa: F401  register crawled articles and news classifications
    with serialized(engine) as session:
        Base.metadata.create_all(session.connection())
        if engine.dialect.name == "postgresql":
            for table in Base.metadata.tables:
                session.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        if not session.get(Account, 1): session.add(Account(id=1, state=initial()))

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

def apply_events(engine, events):
    with serialized(engine) as session:
        account = session.execute(select(Account).where(Account.id == 1).with_for_update()).scalar_one()
        state = account.state
        for event in events:
            if session.get(LedgerEvent, event["id"]): continue
            # Historical reconciliations may not be inserted behind a checkpoint.
            if event["kind"] != "control" and state["last_event"] and utc(event["time"]) < utc(state["last_event"]):
                raise ValueError("out-of-order event would corrupt accounting")
            counts = {name: len(state[name]) for name in ("fills", "funding", "decisions")}
            state = reduce_event(state, event)
            payload = {**event, "effects": {name: state[name][count:] for name, count in counts.items()},
                       "balance_after": state["cash"], "equity_after": state["equity"], "position_after": state["position"]}
            session.add(LedgerEvent(id=event["id"], event_time=event["time"], payload=payload))
            session.flush()
        for name in ("fills", "funding", "decisions", "curve"):
            state[name] = state[name][-2000:]
        account.state = state
    return state

def portfolio(engine):
    with Session(engine) as session:
        account = session.get(Account, 1)
        return account.state if account else initial()

