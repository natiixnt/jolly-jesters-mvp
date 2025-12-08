"""Add created_at column to analysis_runs

Revision ID: 20241206_add_created_at_to_analysis_runs
Revises: 20241204_add_mode_and_settings_defaults
Create Date: 2025-12-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20241206_add_created_at_to_analysis_runs"
down_revision: Union[str, Sequence[str], None] = "20241204_add_mode_and_settings_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.execute("UPDATE analysis_runs SET created_at = COALESCE(started_at, NOW()) WHERE created_at IS NULL")
    op.alter_column("analysis_runs", "created_at", nullable=False, server_default=sa.func.now())


def downgrade() -> None:
    op.drop_column("analysis_runs", "created_at")
