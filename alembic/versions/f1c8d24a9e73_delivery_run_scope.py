"""scope outcome rows to the catch-up run that armed them

Revision ID: f1c8d24a9e73
Revises: e7b3a5c14d90
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1c8d24a9e73'
down_revision: Union[str, Sequence[str], None] = 'e7b3a5c14d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ('render_deliveries', 'metadata_writes'):
        op.add_column(table, sa.Column('run_id', sa.BigInteger(), nullable=True))
        op.add_column(table, sa.Column('previous_status', sa.String(length=24), nullable=True))
        # PARTIAL, the same argument this migration makes four lines below
        # for ix_metadata_writes_next_attempt_at: `run_id` is NULL for every
        # row the ordinary pipeline arms (~32k+ per index on the target
        # deployment), maintained on every write, and the only query that
        # reads it asks `run_id = <id>`.
        op.create_index(
            f'ix_{table}_run_id', table, ['run_id'],
            postgresql_where=sa.text('run_id IS NOT NULL'),
        )
        op.create_foreign_key(
            f'fk_{table}_run_id', table, 'runs', ['run_id'], ['id'], ondelete='SET NULL',
        )
    # The due index becomes PARTIAL (Phase A review ruling 2): the retry pass
    # is this column's only reader and it asks one question -- which `pending`
    # rows are due. Every other status leaves next_attempt_at NULL, so the
    # full index e7b3a5c14d90 created is mostly NULLs maintained on every
    # write for a query that can never want them.
    op.drop_index('ix_metadata_writes_next_attempt_at', table_name='metadata_writes')
    op.create_index(
        'ix_metadata_writes_next_attempt_at', 'metadata_writes', ['next_attempt_at'],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_metadata_writes_next_attempt_at', table_name='metadata_writes')
    op.create_index('ix_metadata_writes_next_attempt_at', 'metadata_writes', ['next_attempt_at'])
    for table in ('render_deliveries', 'metadata_writes'):
        op.drop_constraint(f'fk_{table}_run_id', table, type_='foreignkey')
        op.drop_index(f'ix_{table}_run_id', table_name=table)
        op.drop_column(table, 'previous_status')
        op.drop_column(table, 'run_id')
