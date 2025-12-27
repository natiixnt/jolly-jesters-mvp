"""Add scrape_status to analysis_run_items and disable API default.

Revision ID: 20250218_add_scrape_status
Revises: 20241213_currency_and_scrape_meta
Create Date: 2025-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250218_add_scrape_status"
down_revision = "20241213_currency_and_scrape_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    scrapestatus_enum = sa.Enum(
        "pending",
        "in_progress",
        "ok",
        "not_found",
        "blocked",
        "network_error",
        "error",
        name="scrapestatus",
    )
    scrapestatus_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "analysis_run_items",
        sa.Column(
            "scrape_status",
            scrapestatus_enum,
            nullable=False,
            server_default="pending",
        ),
    )

    op.execute("UPDATE analysis_run_items SET scrape_status='pending' WHERE error_message='pending'")
    op.execute("UPDATE analysis_run_items SET error_message=NULL WHERE error_message='pending'")
    op.execute(
        "UPDATE analysis_run_items SET scrape_status='error' "
        "WHERE source='error' AND error_message IS NOT NULL"
    )
    op.execute(
        "UPDATE analysis_run_items SET scrape_status='not_found' "
        "WHERE source='not_found' AND scrape_status='pending'"
    )
    op.execute("UPDATE analysis_run_items SET scrape_status='ok' WHERE scrape_status='pending'")

    op.alter_column("analysis_runs", "use_api", server_default=sa.text("false"))
    op.execute("UPDATE analysis_runs SET use_api = FALSE")


def downgrade() -> None:
    op.alter_column("analysis_runs", "use_api", server_default=sa.text("true"))
    op.drop_column("analysis_run_items", "scrape_status")
    sa.Enum(name="scrapestatus").drop(op.get_bind(), checkfirst=True)
