"""add network_proxies table"""

from alembic import op
import sqlalchemy as sa

revision = "20260318_add_network_proxies"
down_revision = "20260318_add_stoploss"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "network_proxies",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fail_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health_score", sa.Numeric(5, 4), nullable=False, server_default="1.0000"),
        sa.Column("quarantine_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quarantine_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("network_proxies")
