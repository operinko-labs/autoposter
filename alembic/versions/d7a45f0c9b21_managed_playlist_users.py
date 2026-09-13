"""managed playlist users

Revision ID: d7a45f0c9b21
Revises: c4f2a19b6d38
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd7a45f0c9b21'
# Re-derived when this branch cut: main had already merged the status_facts
# migration, so this chains from c4f2a19b6d38 rather than b8c31f4a7e26.
down_revision: Union[str, Sequence[str], None] = 'c4f2a19b6d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'managed_playlist_users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        # The definition's title. NOT a foreign key to managed_playlists: a
        # user copy can exist while no admin playlist does (apply_to_plex off,
        # sync_to_users_apply on), and a cascade would delete the only handle
        # this service holds on a live playlist in somebody else's account.
        sa.Column('definition_key', sa.String(length=255), nullable=False),
        sa.Column('plex_user_id', sa.Integer(), nullable=False),
        sa.Column('plex_user_title', sa.String(length=255), nullable=False),
        # NOT NULL, like managed_playlists.plex_rating_key and for the same
        # reason: for a playlist this column IS the ownership predicate.
        sa.Column('plex_rating_key', sa.String(length=32), nullable=False),
        sa.Column('definition_hash', sa.String(length=64), nullable=False),
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
        sa.UniqueConstraint(
            'definition_key', 'plex_user_id', name='uq_managed_playlist_user'
        ),
    )
    op.create_index(
        op.f('ix_managed_playlist_users_definition_key'),
        'managed_playlist_users', ['definition_key'], unique=False,
    )
    op.create_index(
        op.f('ix_managed_playlist_users_plex_rating_key'),
        'managed_playlist_users', ['plex_rating_key'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_managed_playlist_users_plex_rating_key'),
        table_name='managed_playlist_users',
    )
    op.drop_index(
        op.f('ix_managed_playlist_users_definition_key'),
        table_name='managed_playlist_users',
    )
    op.drop_table('managed_playlist_users')
