"""add updated_at to analysis run items

Revision ID: 20250308_add_analysis_run_item_updated_at
Revises: 20250302_add_analysis_run_metadata
Create Date: 2025-03-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20250308_add_analysis_run_item_updated_at"
down_revision = "20250302_add_analysis_run_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_run_items",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("analysis_run_items", "updated_at")
