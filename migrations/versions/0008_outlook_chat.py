"""Outlook answers and in-app chat."""
from alembic import op
from quantpilot.outlook.answers import OutlookAnswer
from quantpilot.ai.chat import ChatMessage, ChatThread

revision = '0008_outlook_chat'
down_revision = '0007_shadow_forecasts'
branch_labels = None
depends_on = None


def upgrade():
    for table in (OutlookAnswer.__table__, ChatThread.__table__, ChatMessage.__table__):
        table.create(op.get_bind(), checkfirst=True)
        if op.get_bind().dialect.name == 'postgresql':
            op.execute(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY')


def downgrade():
    for table in (ChatMessage.__table__, ChatThread.__table__, OutlookAnswer.__table__):
        table.drop(op.get_bind(), checkfirst=True)
