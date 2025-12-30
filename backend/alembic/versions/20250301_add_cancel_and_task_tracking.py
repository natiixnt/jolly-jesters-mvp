"""Add cancel status, task tracking, and cancel metadata for analysis runs.

Revision ID: 20250301_add_cancel_and_task_tracking
Revises: 20250219_drop_use_api
Create Date: 2025-03-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20250301_add_cancel_and_task_tracking"
down_revision: Union[str, Sequence[str], None] = "20250219_drop_use_api"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE analysisstatus ADD VALUE IF NOT EXISTS 'canceled'")

    op.add_column("analysis_runs", sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analysis_runs", sa.Column("root_task_id", sa.String(), nullable=True))

    op.create_table(
        "analysis_run_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("analysis_run_item_id", sa.Integer(), sa.ForeignKey("analysis_run_items.id"), nullable=True),
        sa.Column("celery_task_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ean", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_analysis_run_tasks_run", "analysis_run_tasks", ["analysis_run_id"])
    op.create_index("ix_analysis_run_tasks_item", "analysis_run_tasks", ["analysis_run_item_id"])
    op.create_index("ix_analysis_run_tasks_celery", "analysis_run_tasks", ["celery_task_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_run_tasks_celery", table_name="analysis_run_tasks")
    op.drop_index("ix_analysis_run_tasks_item", table_name="analysis_run_tasks")
    op.drop_index("ix_analysis_run_tasks_run", table_name="analysis_run_tasks")
    op.drop_table("analysis_run_tasks")

    op.drop_column("analysis_runs", "root_task_id")
    op.drop_column("analysis_runs", "canceled_at")
