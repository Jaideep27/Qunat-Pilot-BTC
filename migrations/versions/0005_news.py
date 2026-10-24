"""Versioned web-news observations and bounded reasoning jobs."""
from alembic import op
from quantpilot.ai.news import NewsItem, NewsRun, NewsFeed

revision = '0005_news'
down_revision = '0004_worker_runs'
branch_labels = None
depends_on = None


def upgrade():
    for table in (NewsItem.__table__, NewsRun.__table__, NewsFeed.__table__):
        table.create(op.get_bind(), checkfirst=True)
        if op.get_bind().dialect.name == 'postgresql':
            op.execute(f'ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY')


def downgrade():
    for table in (NewsFeed.__table__, NewsRun.__table__, NewsItem.__table__):
        table.drop(op.get_bind(), checkfirst=True)
