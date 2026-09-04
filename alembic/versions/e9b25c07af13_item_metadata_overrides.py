"""item metadata overrides: the per-item operator-declared field values

Revision ID: e9b25c07af13
Revises: d7a45f0c9b21
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e9b25c07af13'
down_revision: Union[str, Sequence[str], None] = 'd7a45f0c9b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roadmap row 99. One new table, no column added anywhere else and no
    # data rewrite: an empty table on upgrade is the correct starting state,
    # because an override is by definition something an operator typed and
    # there is nothing to derive one from. (Deriving one WOULD be the
    # freezing hazard -- see the model's docstring.)
    #
    # The FK is media_items.id with ON DELETE CASCADE, matching every other
    # per-item child of that table: scheduler/prune.py hard-deletes item
    # rows. It is deliberately NOT rating_key, which render/pipeline.py
    # mutates in place on a re-key.
    #
    # UNIQUE(item_id, field) is load-bearing: it is what makes the PUT
    # endpoint an upsert rather than an append, and what lets "the override
    # for this field" stay a single answerable question.
    op.create_table(
        'item_metadata_overrides',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('field', sa.String(length=32), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'item_id', 'field', name='uq_item_metadata_override_item_field'
        ),
    )
    op.create_index(
        op.f('ix_item_metadata_overrides_item_id'),
        'item_metadata_overrides', ['item_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order, so this file reads as the mirror of `upgrade` above.
    # Dropping is lossless in the only sense that matters: these rows are the
    # operator's own declarations and nothing else references them, so a
    # downgrade loses exactly the overrides and nothing derived from them.
    # The Plex-side effect of an override -- a locked field holding the
    # operator's value -- is not undone by this and is not meant to be; that
    # is the DELETE endpoint's job (roadmap row 99's C9).
    op.drop_index(
        op.f('ix_item_metadata_overrides_item_id'),
        table_name='item_metadata_overrides',
    )
    op.drop_table('item_metadata_overrides')
