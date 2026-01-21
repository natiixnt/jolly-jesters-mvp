"""disable cloud scraper defaults (local-only)

Revision ID: 20250315_disable_cloud_scraper
Revises: 20250308_add_analysis_run_item_updated_at
Create Date: 2025-03-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20250315_disable_cloud_scraper"
down_revision = "20250308_add_analysis_run_item_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE analysis_runs SET use_cloud_http = false WHERE use_cloud_http IS DISTINCT FROM FALSE")
    op.alter_column(
        "analysis_runs",
        "use_cloud_http",
        server_default=sa.text("false"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "analysis_runs",
        "use_cloud_http",
        server_default=sa.text("true"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
