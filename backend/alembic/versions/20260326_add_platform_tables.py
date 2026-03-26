"""Add monitoring, alerts, notifications, API keys tables.

Revision ID: 20260326a
Revises: 20260318_add_saas_tables
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260326a"
down_revision = "20260318_add_saas_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_eans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("ean", sa.String(64), nullable=False, index=True),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refresh_interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scrape_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ean", sa.String(64), nullable=True),
        sa.Column("category_id", UUID(as_uuid=True), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("condition_type", sa.String(64), nullable=False),
        sa.Column("threshold_value", sa.Numeric(12, 4), nullable=True),
        sa.Column("notify_email", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notify_webhook", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("webhook_url", sa.Text(), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("alert_rule_id", sa.Integer(), sa.ForeignKey("alert_rules.id"), nullable=False, index=True),
        sa.Column("ean", sa.String(64), nullable=True),
        sa.Column("condition_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("notification_type", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("channel", sa.String(32), nullable=False, server_default="in_app"),
        sa.Column("delivered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(128), unique=True, nullable=False, index=True),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # extend tenants with SLA fields
    op.add_column("tenants", sa.Column("refresh_interval_minutes", sa.Integer(), nullable=True, server_default="60"))
    op.add_column("tenants", sa.Column("max_monitored_eans", sa.Integer(), nullable=True, server_default="100"))
    op.add_column("tenants", sa.Column("max_alert_rules", sa.Integer(), nullable=True, server_default="10"))
    op.add_column("tenants", sa.Column("api_access", sa.Boolean(), nullable=True, server_default="false"))


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("notifications")
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
    op.drop_table("monitored_eans")
    op.drop_column("tenants", "refresh_interval_minutes")
    op.drop_column("tenants", "max_monitored_eans")
    op.drop_column("tenants", "max_alert_rules")
    op.drop_column("tenants", "api_access")
