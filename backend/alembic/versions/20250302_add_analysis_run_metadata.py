"""add analysis run metadata and source

Revision ID: 20250302_add_analysis_run_metadata
Revises: 20250301_add_cancel_and_task_tracking
Create Date: 2025-03-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20250302_add_analysis_run_metadata"
down_revision = "20250301_add_cancel_and_task_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("input_source", sa.String(), nullable=False, server_default="upload"),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("run_metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "run_metadata")
    op.drop_column("analysis_runs", "input_source")
