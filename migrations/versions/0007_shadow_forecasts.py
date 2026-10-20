"""Shadow research forecasts and their forward-scored outcomes."""
from alembic import op
from quantpilot.forecaster import ShadowForecast

revision = '0007_shadow_forecasts'
down_revision = '0006_macro'
branch_labels = None
depends_on = None


def upgrade():
    ShadowForecast.__table__.create(op.get_bind(), checkfirst=True)
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE shadow_forecasts ENABLE ROW LEVEL SECURITY')


def downgrade():
    ShadowForecast.__table__.drop(op.get_bind(), checkfirst=True)
