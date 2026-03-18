"""add SaaS tables: tenants, users, usage_records + tenant_id columns"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260318_add_saas_tables"
down_revision = "20260318_add_network_proxies"
branch_labels = None
depends_on = None


def upgrade():
    # tenants
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), unique=True, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("plan", sa.String(64), nullable=False, server_default="free"),
        sa.Column("monthly_ean_quota", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    # users
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(320), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # usage_records
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("analysis_run_id", sa.Integer(), sa.ForeignKey("analysis_runs.id"), nullable=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("ean_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("captcha_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_usage_records_tenant_id", "usage_records", ["tenant_id"])
    op.create_index("ix_usage_records_period", "usage_records", ["period"])

    # add tenant_id + user_id to existing tables (nullable for backward compat)
    op.add_column("categories", sa.Column("tenant_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_categories_tenant_id", "categories", ["tenant_id"])

    op.add_column("analysis_runs", sa.Column("tenant_id", UUID(as_uuid=True), nullable=True))
    op.add_column("analysis_runs", sa.Column("user_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_analysis_runs_tenant_id", "analysis_runs", ["tenant_id"])
    op.create_index("ix_analysis_runs_user_id", "analysis_runs", ["user_id"])


def downgrade():
    op.drop_index("ix_analysis_runs_user_id")
    op.drop_index("ix_analysis_runs_tenant_id")
    op.drop_column("analysis_runs", "user_id")
    op.drop_column("analysis_runs", "tenant_id")

    op.drop_index("ix_categories_tenant_id")
    op.drop_column("categories", "tenant_id")

    op.drop_table("usage_records")
    op.drop_table("users")
    op.drop_table("tenants")
