"""Add currency rates, purchase price metadata, and scraping timestamps

Revision ID: 20241213_currency_and_scrape_meta
Revises: 20241206_add_created_at_to_analysis_runs
Create Date: 2025-12-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20241213_currency_and_scrape_meta"
down_revision: Union[str, Sequence[str], None] = "20241206_add_created_at_to_analysis_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "currency_rates",
        sa.Column("currency", sa.String(length=8), primary_key=True),
        sa.Column("rate_to_pln", sa.Numeric(12, 6), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
    )

    # seed default currencies
    op.execute(
        """
        INSERT INTO currency_rates (currency, rate_to_pln, is_default) VALUES
        ('PLN', 1.0, true),
        ('EUR', 4.5, false),
        ('USD', 4.2, false),
        ('CAD', 3.1, false)
        ON CONFLICT (currency) DO NOTHING
        """
    )

    op.add_column("analysis_run_items", sa.Column("original_purchase_price", sa.Numeric(12, 4), nullable=True))
    op.add_column("analysis_run_items", sa.Column("original_currency", sa.String(length=8), nullable=True))
    op.add_column("analysis_run_items", sa.Column("purchase_price_pln", sa.Numeric(12, 4), nullable=True))

    op.add_column(
        "product_market_data",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.add_column(
        "product_effective_state",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )

    bind = op.get_bind()
    # extend enum in-place to avoid dropping dependent columns
    op.execute("ALTER TYPE marketdatasource ADD VALUE IF NOT EXISTS 'cloud_http'")
    op.execute("ALTER TYPE marketdatasource ADD VALUE IF NOT EXISTS 'local'")

    # backfill last_checked_at on effective state with last_fetched_at where available
    op.execute(
        """
        UPDATE product_effective_state
        SET last_checked_at = COALESCE(last_fetched_at, updated_at)
        WHERE last_checked_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("product_effective_state", "last_checked_at")
    op.drop_column("product_market_data", "last_checked_at")

    op.drop_column("analysis_run_items", "purchase_price_pln")
    op.drop_column("analysis_run_items", "original_currency")
    op.drop_column("analysis_run_items", "original_purchase_price")

    op.drop_table("currency_rates")
