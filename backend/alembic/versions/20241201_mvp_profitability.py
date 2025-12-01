"""Add profitability analysis schema with categories and analysis runs

Revision ID: 20241201_mvp_profitability
Revises: 15afad0d971b
Create Date: 2025-12-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20241201_mvp_profitability"
down_revision: Union[str, Sequence[str], None] = "15afad0d971b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


profitability_label = postgresql.ENUM(
    "oplacalny",
    "nieoplacalny",
    "nieokreslony",
    name="profitabilitylabel",
)
market_data_source = postgresql.ENUM(
    "scraping",
    "api",
    name="marketdatasource",
)
analysis_status = postgresql.ENUM(
    "pending",
    "running",
    "completed",
    "failed",
    name="analysisstatus",
)
analysis_item_source = postgresql.ENUM(
    "baza",
    "scraping",
    "not_found",
    "error",
    name="analysisitemsource",
)


def upgrade() -> None:
    profitability_label.create(op.get_bind(), checkfirst=True)
    market_data_source.create(op.get_bind(), checkfirst=True)
    analysis_status.create(op.get_bind(), checkfirst=True)
    analysis_item_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("profitability_multiplier", sa.Numeric(12, 4), nullable=False),
        sa.Column("commission_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("ean", sa.String(length=64), nullable=False, index=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purchase_price", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("category_id", "ean", name="uq_product_category_ean"),
    )
    op.create_index("ix_products_category", "products", ["category_id"])
    op.create_index("ix_products_ean", "products", ["ean"])

    op.create_table(
        "product_market_data",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("allegro_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("allegro_sold_count", sa.Integer(), nullable=True),
        sa.Column("source", sa.Enum(name="marketdatasource"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("is_not_found", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_product_market_data_product_fetched",
        "product_market_data",
        ["product_id", sa.text("fetched_at DESC")],
    )

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("categories.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("input_file_name", sa.String(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(name="analysisstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("total_products", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "processed_products", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index("ix_analysis_runs_category", "analysis_runs", ["category_id"])

    op.create_table(
        "product_effective_state",
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            primary_key=True,
        ),
        sa.Column(
            "last_market_data_id",
            sa.ForeignKey("product_market_data.id"),
            nullable=True,
        ),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_not_found", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_stale", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("profitability_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("profitability_label", sa.Enum(name="profitabilitylabel"), nullable=True),
        sa.Column("last_analysis_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "analysis_run_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_run_id",
            sa.Integer(),
            sa.ForeignKey("analysis_runs.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("ean", sa.String(length=64), nullable=False),
        sa.Column("input_name", sa.Text(), nullable=True),
        sa.Column("input_purchase_price", sa.Numeric(12, 4), nullable=False),
        sa.Column("source", sa.Enum(name="analysisitemsource"), nullable=False),
        sa.Column("allegro_price", sa.Numeric(12, 4), nullable=True),
        sa.Column("allegro_sold_count", sa.Integer(), nullable=True),
        sa.Column("profitability_score", sa.Numeric(12, 4), nullable=True),
        sa.Column("profitability_label", sa.Enum(name="profitabilitylabel"), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_analysis_run_items_run", "analysis_run_items", ["analysis_run_id"])
    op.create_index("ix_analysis_run_items_product", "analysis_run_items", ["product_id"])


def downgrade() -> None:
    op.drop_index("ix_analysis_run_items_product", table_name="analysis_run_items")
    op.drop_index("ix_analysis_run_items_run", table_name="analysis_run_items")
    op.drop_table("analysis_run_items")

    op.drop_table("product_effective_state")
    op.drop_index("ix_analysis_runs_category", table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_index(
        "ix_product_market_data_product_fetched", table_name="product_market_data"
    )
    op.drop_table("product_market_data")
    op.drop_index("ix_products_ean", table_name="products")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_table("products")
    op.drop_table("categories")

    analysis_item_source.drop(op.get_bind(), checkfirst=True)
    analysis_status.drop(op.get_bind(), checkfirst=True)
    market_data_source.drop(op.get_bind(), checkfirst=True)
    profitability_label.drop(op.get_bind(), checkfirst=True)
