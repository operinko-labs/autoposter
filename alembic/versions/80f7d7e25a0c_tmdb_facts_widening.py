"""tmdb facts widening and the rate-limit state

Revision ID: 80f7d7e25a0c
Revises: e2c7a4b91d05
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '80f7d7e25a0c'
down_revision: Union[str, Sequence[str], None] = 'e2c7a4b91d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default, not just a Python-side default: ADD COLUMN NOT NULL would
    # otherwise fail against the populated item_facts table a deployed instance
    # already has -- the reasoning `renders.upload_status` records.
    op.add_column(
        'item_facts',
        sa.Column(
            'tmdb_origin_country', postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Nullable with no default: NULL means "TMDb has not told us", which is a
    # different statement from any string, and is what the enumeration skips.
    op.add_column(
        'item_facts', sa.Column('tmdb_original_language', sa.String(length=16), nullable=True)
    )
    op.add_column(
        'item_facts', sa.Column('tmdb_collection_id', sa.Integer(), nullable=True)
    )
    # The franchise enumeration groups by this column across the whole table.
    op.create_index(
        'ix_item_facts_tmdb_collection_id', 'item_facts', ['tmdb_collection_id']
    )
    op.create_table(
        'tmdb_rate_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'refused_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('tmdb_rate_state')
    op.drop_index('ix_item_facts_tmdb_collection_id', table_name='item_facts')
    op.drop_column('item_facts', 'tmdb_collection_id')
    op.drop_column('item_facts', 'tmdb_original_language')
    op.drop_column('item_facts', 'tmdb_origin_country')
