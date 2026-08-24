"""logo upload marker

Revision ID: 7c2e5a91d3b4
Revises: 9a1f4c72be05
Create Date: 2026-08-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2e5a91d3b4'
down_revision: Union[str, Sequence[str], None] = '9a1f4c72be05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default on purpose, and deliberately not backfilled:
    # NULL means "this service never set a clearlogo on this item", which is
    # the truth for every row that exists when this migration runs. The logo
    # revert clears only items whose marker is set, so inventing a value here
    # would hand it every item in the library on its first run.
    op.add_column(
        'media_items',
        sa.Column('logo_upload_key', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('media_items', 'logo_upload_key')
