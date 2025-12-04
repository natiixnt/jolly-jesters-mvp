"""Add mode column to analysis_runs

Revision ID: 20241204_add_mode_and_settings_defaults
Revises: 20241204_settings
Create Date: 2025-12-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20241204_add_mode_and_settings_defaults"
down_revision: Union[str, Sequence[str], None] = "20241204_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("mode", sa.String(), nullable=False, server_default="mixed"),
    )
    op.execute("UPDATE analysis_runs SET mode='mixed' WHERE mode IS NULL")
    op.alter_column("analysis_runs", "mode", server_default=None)


def downgrade() -> None:
    op.drop_column("analysis_runs", "mode")
