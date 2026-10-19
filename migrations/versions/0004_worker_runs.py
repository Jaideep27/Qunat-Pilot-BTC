"""Persistent worker-run monitoring, separate from accounting events."""
from alembic import op
from quantpilot.operations import WorkerRun

revision='0004_worker_runs'
down_revision='0003_market_history'
branch_labels=None
depends_on=None


def upgrade():
    WorkerRun.__table__.create(op.get_bind(),checkfirst=True)
    if op.get_bind().dialect.name=='postgresql':op.execute('ALTER TABLE worker_runs ENABLE ROW LEVEL SECURITY')


def downgrade():WorkerRun.__table__.drop(op.get_bind(),checkfirst=True)
