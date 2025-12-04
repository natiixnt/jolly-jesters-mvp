"""Add settings table for cache TTL and local scraper windows

Revision ID: 20241204_settings
Revises: 20241201_mvp_profitability
Create Date: 2025-12-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20241204_settings"
down_revision: Union[str, Sequence[str], None] = "20241201_mvp_profitability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_ttl_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("local_scraper_windows", sa.Integer(), nullable=False, server_default="1"),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO settings (id, cache_ttl_days, local_scraper_windows) VALUES (1, 30, 1)"
        )
    )


def downgrade() -> None:
    op.drop_table("settings")
