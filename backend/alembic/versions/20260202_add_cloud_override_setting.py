"""add cloud_scraper_disabled flag to settings

Revision ID: 20260202_add_cloud_override_setting
Revises: 20250315_disable_cloud_scraper
Create Date: 2026-02-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260202_add_cloud_override_setting"
down_revision = "20250315_disable_cloud_scraper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "cloud_scraper_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.execute("UPDATE settings SET cloud_scraper_disabled = TRUE WHERE cloud_scraper_disabled IS NULL")


def downgrade() -> None:
    op.drop_column("settings", "cloud_scraper_disabled")
