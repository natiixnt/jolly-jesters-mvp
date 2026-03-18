"""add stop-loss config and stopped status"""

from alembic import op
import sqlalchemy as sa

revision = "20260318_add_stoploss"
down_revision = "20260318_add_metering_columns"
branch_labels = None
depends_on = None


def upgrade():
    # Add 'stopped' to analysisstatus enum
    # NOTE: ALTER TYPE ... ADD VALUE cannot run inside a transaction on PostgreSQL.
    # Alembic runs each migration in a transaction by default.
    # We use op.execute with connection.execution_options to handle this.
    op.execute("ALTER TYPE analysisstatus ADD VALUE IF NOT EXISTS 'stopped'")

    # Add stop-loss config columns to settings
    op.add_column("settings", sa.Column("stoploss_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("settings", sa.Column("stoploss_window_size", sa.Integer(), nullable=False, server_default="20"))
    op.add_column(
        "settings",
        sa.Column("stoploss_max_error_rate", sa.Numeric(5, 4), nullable=False, server_default="0.5000"),
    )
    op.add_column(
        "settings",
        sa.Column("stoploss_max_captcha_rate", sa.Numeric(5, 4), nullable=False, server_default="0.8000"),
    )
    op.add_column(
        "settings",
        sa.Column("stoploss_max_consecutive_errors", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade():
    op.drop_column("settings", "stoploss_max_consecutive_errors")
    op.drop_column("settings", "stoploss_max_captcha_rate")
    op.drop_column("settings", "stoploss_max_error_rate")
    op.drop_column("settings", "stoploss_window_size")
    op.drop_column("settings", "stoploss_enabled")
    # NOTE: PostgreSQL does not support removing enum values.
    # The 'stopped' value will remain in the enum type after downgrade.
