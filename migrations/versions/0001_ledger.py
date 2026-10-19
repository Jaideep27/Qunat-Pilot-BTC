"""Initial transactional account and event ledger."""
from alembic import op
import sqlalchemy as sa
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("accounts", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("state", sa.JSON, nullable=False))
    op.create_table("ledger_events", sa.Column("id", sa.String(160), primary_key=True),
                    sa.Column("event_time", sa.String(40), nullable=False), sa.Column("payload", sa.JSON, nullable=False))
    op.create_index("ix_ledger_events_event_time", "ledger_events", ["event_time"])

def downgrade():
    op.drop_table("ledger_events")
    op.drop_table("accounts")

