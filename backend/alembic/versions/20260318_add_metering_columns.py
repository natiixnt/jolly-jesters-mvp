"""add metering columns to analysis_run_items"""

from alembic import op
import sqlalchemy as sa

revision = "20260318_add_metering_columns"
down_revision = "20260209_single_scraper_cleanup"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_run_items", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("analysis_run_items", sa.Column("captcha_solves", sa.Integer(), nullable=True))
    op.add_column("analysis_run_items", sa.Column("retries", sa.Integer(), nullable=True))
    op.add_column("analysis_run_items", sa.Column("attempts", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("analysis_run_items", "attempts")
    op.drop_column("analysis_run_items", "retries")
    op.drop_column("analysis_run_items", "captcha_solves")
    op.drop_column("analysis_run_items", "latency_ms")
