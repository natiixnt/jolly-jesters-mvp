"""merge api_key_scopes and metering_fields

Revision ID: b6e6a7cbcc70
Revises: 20260408_add_api_key_scopes, 20260408_add_metering_fields
Create Date: 2026-04-08 18:12:18.693816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6e6a7cbcc70'
down_revision: Union[str, Sequence[str], None] = ('20260408_add_api_key_scopes', '20260408_add_metering_fields')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
