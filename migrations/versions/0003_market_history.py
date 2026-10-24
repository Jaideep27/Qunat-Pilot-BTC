"""Durable historical and live market data.

Revision ID: 0003_market_history
Revises: 0002
"""
from alembic import op
from quantpilot.storage.market import MarketCandle, MarketObservation, MarketFeed
revision='0003_market_history'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    for model in (MarketCandle,MarketObservation,MarketFeed):
        model.__table__.create(op.get_bind(),checkfirst=True)
        if op.get_bind().dialect.name=='postgresql':
            op.execute(f'ALTER TABLE {model.__tablename__} ENABLE ROW LEVEL SECURITY')

def downgrade():
    for model in (MarketFeed,MarketObservation,MarketCandle):
        model.__table__.drop(op.get_bind(),checkfirst=True)
