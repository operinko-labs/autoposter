"""drift sweep attempt timestamp

Revision ID: c1a7f30b9e42
Revises: 884f8a8a5bc8
Create Date: 2026-08-21 10:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a7f30b9e42'
down_revision: Union[str, Sequence[str], None] = '884f8a8a5bc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with no default on purpose: NULL means "never attempted", which
    # sorts first in the drift sweep. Backfilling every existing row with now()
    # would push the whole library to the back of the queue at once.
    op.add_column(
        'media_items',
        sa.Column('facts_attempted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('media_items', 'facts_attempted_at')
