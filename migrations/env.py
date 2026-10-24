from alembic import context
from quantpilot.config import settings
from quantpilot.storage.database import Base, database
from quantpilot.storage import market as market_store  # register all migration metadata
from quantpilot.storage import snapshots
from quantpilot.monitoring import operations
from quantpilot.ai import news

target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    with database().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()

