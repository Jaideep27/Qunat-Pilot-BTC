"""Operational read projections for ephemeral hosts."""
from alembic import op
import sqlalchemy as sa
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("read_snapshots", sa.Column("name", sa.String(40), primary_key=True),
                    sa.Column("payload", sa.JSON, nullable=False))

def downgrade():
    op.drop_table("read_snapshots")

