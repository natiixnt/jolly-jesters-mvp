"""add scopes column to api_keys table"""

from alembic import op
import sqlalchemy as sa

revision = "20260408_add_api_key_scopes"
down_revision = "20260408_add_stoploss_thresholds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "api_keys",
        sa.Column("scopes", sa.Text(), nullable=False, server_default='["read"]'),
    )


def downgrade():
    op.drop_column("api_keys", "scopes")
