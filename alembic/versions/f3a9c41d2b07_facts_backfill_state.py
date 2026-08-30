"""facts backfill cursor state

Revision ID: f3a9c41d2b07
Revises: a4db89c94eaa
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c41d2b07'
down_revision: Union[str, Sequence[str], None] = 'a4db89c94eaa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'facts_backfill_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cursor_item_id', sa.Integer(), nullable=True),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('facts_backfill_state')
