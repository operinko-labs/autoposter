"""the config store's meta, snapshot formats, and the secrets table

Revision ID: a1f4c2d90e73
Revises: b3d91f7c05ea
Create Date: 2026-09-14 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1f4c2d90e73'
down_revision: Union[str, Sequence[str], None] = 'b3d91f7c05ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The empty object is what "format 1" means: a row written before this
    # revision is a DELTA, and Task 3's one-time migration keys on exactly
    # this. A backfilled `{"format": 2}` would claim every existing delta was
    # already a whole document and skip the migration that makes it one.
    op.add_column(
        'config_overrides',
        sa.Column(
            'meta',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        'config_override_snapshots',
        sa.Column('format', sa.Integer(), nullable=False, server_default=sa.text('1')),
    )
    op.create_table(
        'secrets',
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('ciphertext', sa.Text(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('secrets')
    op.drop_column('config_override_snapshots', 'format')
    op.drop_column('config_overrides', 'meta')
