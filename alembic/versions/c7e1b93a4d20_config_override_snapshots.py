"""config override snapshots

Revision ID: c7e1b93a4d20
Revises: f3a9c41d2b07
Create Date: 2026-09-03 09:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7e1b93a4d20'
down_revision: Union[str, Sequence[str], None] = 'f3a9c41d2b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'config_override_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('path_count', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=16), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_config_override_snapshots_created_at'),
        'config_override_snapshots',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_config_override_snapshots_created_at'),
        table_name='config_override_snapshots',
    )
    op.drop_table('config_override_snapshots')
