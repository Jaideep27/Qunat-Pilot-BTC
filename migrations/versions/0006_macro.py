"""Official macro release calendar and first-release values."""
from alembic import op
from quantpilot.macro import MacroEvent

revision = '0006_macro'
down_revision = '0005_news'
branch_labels = None
depends_on = None


def upgrade():
    MacroEvent.__table__.create(op.get_bind(), checkfirst=True)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE macro_events ENABLE ROW LEVEL SECURITY')


def downgrade():
    MacroEvent.__table__.drop(op.get_bind(), checkfirst=True)
