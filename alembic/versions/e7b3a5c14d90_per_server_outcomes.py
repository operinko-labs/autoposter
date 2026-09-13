"""per-server metadata outcomes, delivery attempts and delivered fingerprint

Revision ID: e7b3a5c14d90
Revises: d2e5f8a1c4b7
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7b3a5c14d90'
down_revision: Union[str, Sequence[str], None] = 'd2e5f8a1c4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'metadata_writes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('server', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=24), server_default='pending', nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('attempted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('written_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_id', 'server', name='uq_metadata_write_item_server'),
    )
    op.create_index('ix_metadata_writes_item_id', 'metadata_writes', ['item_id'])
    op.create_index('ix_metadata_writes_next_attempt_at', 'metadata_writes', ['next_attempt_at'])
    # server_default, not just default: ADD COLUMN NOT NULL would otherwise
    # fail against the populated render_deliveries a deployed instance has.
    op.add_column('render_deliveries', sa.Column(
        'attempts', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('render_deliveries', sa.Column(
        'fingerprint', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('render_deliveries', 'fingerprint')
    op.drop_column('render_deliveries', 'attempts')
    op.drop_index('ix_metadata_writes_next_attempt_at', table_name='metadata_writes')
    op.drop_index('ix_metadata_writes_item_id', table_name='metadata_writes')
    op.drop_table('metadata_writes')
