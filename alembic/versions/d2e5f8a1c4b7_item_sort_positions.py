"""item sort positions

Revision ID: d2e5f8a1c4b7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e5f8a1c4b7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roadmap row 269. One row per item at most: the primary key IS the
    # ownership rule. ``released_at`` nullable with no default -- NULL is the
    # held state, set is the tombstone the pipeline acts on once.
    op.create_table(
        'item_sort_positions',
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('library', sa.String(length=255), nullable=False),
        sa.Column('definition_title', sa.String(length=255), nullable=False),
        sa.Column('base', sa.String(length=255), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id'),
    )
    op.create_index('ix_item_sort_positions_library', 'item_sort_positions', ['library'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_item_sort_positions_library', table_name='item_sort_positions')
    op.drop_table('item_sort_positions')
