"""add retry_rate, blocked_rate, cost_per_1000 stop-loss thresholds"""

from alembic import op
import sqlalchemy as sa

revision = "20260408_add_stoploss_thresholds"
down_revision = "20260326a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "settings",
        sa.Column("stoploss_max_retry_rate", sa.Numeric(5, 4), nullable=False, server_default="0.0500"),
    )
    op.add_column(
        "settings",
        sa.Column("stoploss_max_blocked_rate", sa.Numeric(5, 4), nullable=False, server_default="0.1000"),
    )
    op.add_column(
        "settings",
        sa.Column("stoploss_max_cost_per_1000", sa.Numeric(8, 2), nullable=False, server_default="10.00"),
    )

    # Add stopped_by_guardrail to scrapestatus enum
    op.execute("ALTER TYPE scrapestatus ADD VALUE IF NOT EXISTS 'stopped_by_guardrail'")


def downgrade():
    op.drop_column("settings", "stoploss_max_cost_per_1000")
    op.drop_column("settings", "stoploss_max_blocked_rate")
    op.drop_column("settings", "stoploss_max_retry_rate")
    # NOTE: PostgreSQL does not support removing enum values.
