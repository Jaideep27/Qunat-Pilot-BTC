"""Outlook answers and in-app chat."""
from alembic import op
from quantpilot.answers import OutlookAnswer

revision = '0008_outlook_chat'
down_revision = '0007_shadow_forecasts'
branch_labels = None
depends_on = None


def upgrade():
    for table in (OutlookAnswer.__table__,):
        table.create(op.get_bind(), checkfirst=True)
        if op.get_bind().dialect.name == 'postgresql':
            op.execute(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY')


def downgrade():
    OutlookAnswer.__table__.drop(op.get_bind(), checkfirst=True)
