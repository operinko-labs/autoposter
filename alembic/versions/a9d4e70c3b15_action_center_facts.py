"""action center facts

Revision ID: a9d4e70c3b15
Revises: c7e1b93a4d20
Create Date: 2026-09-03 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a9d4e70c3b15'
down_revision: Union[str, Sequence[str], None] = 'c7e1b93a4d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('renders', sa.Column('selected_language', sa.String(length=16), nullable=True))
    op.add_column('renders', sa.Column('language_rank', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('provider_rank', sa.SmallInteger(), nullable=True))
    # server_default, not just default: `default` is Python-side only, so
    # ADD COLUMN NOT NULL would fail against the populated renders table a
    # deployed instance already has. renders.adopted and renders.upload_status
    # already carry the same reasoning.
    op.add_column(
        'renders',
        sa.Column(
            'textless_fallback', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
    )
    op.add_column(
        'renders',
        sa.Column(
            'logo_text_fallback', sa.Boolean(), server_default=sa.text('false'), nullable=False
        ),
    )
    op.add_column('renders', sa.Column('base_width', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('base_height', sa.SmallInteger(), nullable=True))
    op.add_column('renders', sa.Column('text_point_size', sa.SmallInteger(), nullable=True))
    op.add_column(
        'renders', sa.Column('quality_scored_at', sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        'action_dismissals',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('art_kind', sa.String(length=24), nullable=False),
        sa.Column('flag', sa.String(length=32), nullable=True),
        sa.Column('evidence', sa.String(length=64), nullable=False),
        sa.Column(
            'dismissed_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('note', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'art_kind', name='uq_action_dismissal_item_kind'),
    )
    op.create_index(
        op.f('ix_action_dismissals_item_id'), 'action_dismissals', ['item_id'], unique=False
    )
    # No index on any of the nine new renders columns, and none added to
    # status or adopted either. The library is ~16,000 items and renders is
    # per (item, art_kind), so tens of thousands of rows: a sequential scan is
    # the right answer at this size, and a speculative index would cost every
    # render write for a page an operator opens occasionally. The absence is
    # deliberate; revisit if it ever measures slow.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_action_dismissals_item_id'), table_name='action_dismissals')
    op.drop_table('action_dismissals')
    op.drop_column('renders', 'quality_scored_at')
    op.drop_column('renders', 'text_point_size')
    op.drop_column('renders', 'base_height')
    op.drop_column('renders', 'base_width')
    op.drop_column('renders', 'logo_text_fallback')
    op.drop_column('renders', 'textless_fallback')
    op.drop_column('renders', 'provider_rank')
    op.drop_column('renders', 'language_rank')
    op.drop_column('renders', 'selected_language')
