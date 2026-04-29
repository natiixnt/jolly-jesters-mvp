"""add vat_rate to categories

Revision ID: 20260429_vat
Revises: 20260416_robust
Create Date: 2026-04-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260429_vat'
down_revision: Union[str, None] = '20260416_robust'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'categories',
        sa.Column('vat_rate', sa.Numeric(5, 4), nullable=True, server_default='0.23'),
    )


def downgrade() -> None:
    op.drop_column('categories', 'vat_rate')
