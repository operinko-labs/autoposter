"""catch-up runs: the server, the per-run cadence and the two drain markers

Revision ID: a3f7e15c92b8
Revises: f1c8d24a9e73
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f7e15c92b8'
down_revision: Union[str, Sequence[str], None] = 'f1c8d24a9e73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('runs', sa.Column('server', sa.String(length=16), nullable=True))
    op.add_column('runs', sa.Column('cadence_seconds', sa.Integer(), nullable=True))
    op.add_column('runs', sa.Column('last_drained_at', sa.DateTime(timezone=True), nullable=True))
    # NOT NULL with a server default rather than nullable: every existing row
    # -- the runs of every other kind included, which never read it -- gets a
    # real number, so the drain never has to defend against a NULL counter.
    op.add_column(
        'runs',
        sa.Column(
            'idle_drains', sa.Integer(), nullable=False, server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('runs', 'idle_drains')
    op.drop_column('runs', 'last_drained_at')
    op.drop_column('runs', 'cadence_seconds')
    op.drop_column('runs', 'server')
