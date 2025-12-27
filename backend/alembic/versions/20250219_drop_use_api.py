"""Drop deprecated use_api from analysis_runs.

Revision ID: 20250219_drop_use_api
Revises: 20250218_add_scrape_status
Create Date: 2025-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20250219_drop_use_api"
down_revision = "20250218_add_scrape_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("analysis_runs", "use_api")


def downgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column(
            "use_api",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
