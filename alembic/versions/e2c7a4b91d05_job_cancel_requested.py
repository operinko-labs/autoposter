"""job cancel requested

Revision ID: e2c7a4b91d05
Revises: 7c2e5a91d3b4
Create Date: 2026-08-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2c7a4b91d05'
down_revision: Union[str, Sequence[str], None] = '7c2e5a91d3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOT NULL with a server_default rather than nullable: every existing row
    # means "nobody asked to cancel this", which is exactly false, and the
    # worker's check would have to special-case NULL otherwise. The
    # server_default is what lets ADD COLUMN NOT NULL run against a populated
    # jobs table without a separate backfill.
    op.add_column(
        'jobs',
        sa.Column(
            'cancel_requested',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'cancel_requested')
