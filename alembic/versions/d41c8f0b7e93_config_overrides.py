"""config overrides

Revision ID: d41c8f0b7e93
Revises: 3f9b2ad74c81
Create Date: 2026-08-23 14:02:11.404812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd41c8f0b7e93'
down_revision: Union[str, Sequence[str], None] = '3f9b2ad74c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'config_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('config_overrides')
