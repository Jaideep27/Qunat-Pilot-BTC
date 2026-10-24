"""Small operational projections shared by ephemeral workers and the read API."""
from sqlalchemy import JSON, Column, String
from quantpilot.storage.database import Base, serialized, database
from sqlalchemy.orm import Session

class ReadSnapshot(Base):
    __tablename__ = "read_snapshots"
    name = Column(String(40), primary_key=True)
    payload = Column(JSON, nullable=False)

def read(name):
    with Session(database()) as session:
        row = session.get(ReadSnapshot, name)
        return row.payload if row else None

def write(engine, values):
    with serialized(engine) as session:
        for name, payload in values.items():
            row = session.get(ReadSnapshot, name)
            if row: row.payload = payload
            else: session.add(ReadSnapshot(name=name, payload=payload))

