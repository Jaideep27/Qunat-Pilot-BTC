"""Persistent worker-run monitoring, separate from accounting events.

Self-contained since 2026-11-02: the paper worker and its model were removed, so the table is described here as it
was when this migration was written. Existing databases keep the table; nothing reads it any more."""
from alembic import op
import sqlalchemy as sa

revision='0004_worker_runs'
down_revision='0003_market_history'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('worker_runs',
        sa.Column('id',sa.String(32),primary_key=True),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=False,index=True),
        sa.Column('finished_at',sa.DateTime(timezone=True)),
        sa.Column('status',sa.String(24),nullable=False,index=True),
        sa.Column('stage',sa.String(32),nullable=False),
        sa.Column('duration_seconds',sa.Float),
        sa.Column('error_type',sa.String(120)),
        sa.Column('details',sa.JSON,nullable=False),
        if_not_exists=True)
    if op.get_bind().dialect.name=='postgresql':op.execute('ALTER TABLE worker_runs ENABLE ROW LEVEL SECURITY')


def downgrade():op.drop_table('worker_runs',if_exists=True)
