"""add robust fallback strategy fields to analysis_run_items

Revision ID: 20260416_robust
Revises: b6e6a7cbcc70
Create Date: 2026-04-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = '20260416_robust'
down_revision: Union[str, None] = 'b6e6a7cbcc70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('analysis_run_items', sa.Column('strategy', sa.String(32), nullable=True))
    op.add_column('analysis_run_items', sa.Column('fallback_level', sa.Integer(), nullable=True))
    op.add_column('analysis_run_items', sa.Column('proxy_type', sa.String(16), nullable=True))
    op.add_column('analysis_run_items', sa.Column('antidetect_tool', sa.String(16), nullable=True))
    op.add_column('analysis_run_items', sa.Column('session_id', sa.String(64), nullable=True))
    op.add_column('analysis_run_items', sa.Column('cost_breakdown', JSON, nullable=True))
    op.add_column('analysis_run_items', sa.Column('total_cost_usd', sa.Float(), nullable=True))
    op.add_column('analysis_run_items', sa.Column('browser_runtime_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('analysis_run_items', 'browser_runtime_ms')
    op.drop_column('analysis_run_items', 'total_cost_usd')
    op.drop_column('analysis_run_items', 'cost_breakdown')
    op.drop_column('analysis_run_items', 'session_id')
    op.drop_column('analysis_run_items', 'antidetect_tool')
    op.drop_column('analysis_run_items', 'proxy_type')
    op.drop_column('analysis_run_items', 'fallback_level')
    op.drop_column('analysis_run_items', 'strategy')
