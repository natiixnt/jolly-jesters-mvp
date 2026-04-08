"""add network_node_id and provider_status to analysis_run_items"""

from alembic import op
import sqlalchemy as sa

revision = "20260408_add_metering_fields"
down_revision = "20260326a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_run_items", sa.Column("network_node_id", sa.String(64), nullable=True))
    op.add_column("analysis_run_items", sa.Column("provider_status", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("analysis_run_items", "provider_status")
    op.drop_column("analysis_run_items", "network_node_id")
