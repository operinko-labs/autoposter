"""managed playlists

Revision ID: b8c31f4a7e26
Revises: ae7e4aa59f8a
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8c31f4a7e26'
down_revision: Union[str, Sequence[str], None] = 'ae7e4aa59f8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'managed_playlists',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        # NOT NULL, unlike managed_collections.plex_rating_key: for a playlist
        # this column IS the ownership predicate, not a convenience.
        sa.Column('plex_rating_key', sa.String(length=32), nullable=False),
        sa.Column('definition_hash', sa.String(length=64), nullable=False),
        # server_default as well as the model-side default: the column is NOT
        # NULL and a row inserted by anything that is not this application's
        # ORM would otherwise fail.
        sa.Column(
            'libraries', postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column('member_count', sa.Integer(), nullable=True),
        sa.Column('last_added', sa.Integer(), nullable=True),
        sa.Column('last_removed', sa.Integer(), nullable=True),
        sa.Column('last_reconciled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('title', name='uq_managed_playlist_title'),
    )
    op.create_index(
        op.f('ix_managed_playlists_plex_rating_key'),
        'managed_playlists', ['plex_rating_key'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_managed_playlists_plex_rating_key'), table_name='managed_playlists'
    )
    op.drop_table('managed_playlists')
