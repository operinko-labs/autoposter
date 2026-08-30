"""item credits

Revision ID: a4db89c94eaa
Revises: 80f7d7e25a0c
Create Date: 2026-08-30 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4db89c94eaa'
down_revision: Union[str, Sequence[str], None] = '80f7d7e25a0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'item_credits',
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('person', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id', 'kind', 'person'),
    )
    op.create_index('ix_item_credits_kind_person', 'item_credits', ['kind', 'person'])
    # Nullable, no default, no backfill -- NULL means "never scanned", which
    # is true of every existing row (c1a7f30b9e42's reasoning verbatim).
    op.add_column(
        'media_items',
        sa.Column('credits_attempted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('media_items', 'credits_attempted_at')
    op.drop_index('ix_item_credits_kind_person', table_name='item_credits')
    op.drop_table('item_credits')
