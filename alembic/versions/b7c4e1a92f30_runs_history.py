"""runs: one history row per scheduled pass and per full pass

Revision ID: b7c4e1a92f30
Revises: a3f81c05d6e2
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7c4e1a92f30'
down_revision: Union[str, Sequence[str], None] = 'a3f81c05d6e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roadmap row 53. A new table, not a widening of `scheduled_runs`: that
    # table's UNIQUE `name` is what claim_due's ON CONFLICT DO NOTHING and the
    # one-replica-claims guarantee rest on (scheduler/core.py), so it holds the
    # LAST run of each job and this holds the history beside it. No data
    # migration and no backfill -- history begins at this revision, and there
    # is nowhere to recover earlier runs from.
    #
    # `started_at` NOT NULL with a server_default so a row can be inserted
    # naming only kind and name, and so the stamp is the DATABASE clock (the
    # rule every timestamp in this schema follows). `status` likewise: a row
    # exists to say a run is in flight, so 'running' is the only correct
    # value at insert.
    #
    # The seven count columns are nullable with no default. NULL means "this
    # run's window was not attributed" -- which is every scheduled run, by
    # design -- and it is the renders.size_bytes rule: an absent measurement
    # must stay visible rather than be reported as a zero.
    #
    # One index, on `started_at`, for the endpoint's ORDER BY ... DESC LIMIT.
    # `name` and `kind` are deliberately unindexed: the cleanup pass's
    # retention clause holds this table to 500 rows per name (~5.5k rows in
    # all), which is not a size that pays for two more indexes to maintain.
    op.create_table(
        'runs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'status',
            sa.String(length=16),
            server_default='running',
            nullable=False,
        ),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('rendered_poster', sa.Integer(), nullable=True),
        sa.Column('rendered_season_poster', sa.Integer(), nullable=True),
        sa.Column('rendered_background', sa.Integer(), nullable=True),
        sa.Column('rendered_title_card', sa.Integer(), nullable=True),
        sa.Column('processed', sa.Integer(), nullable=True),
        sa.Column('failed', sa.Integer(), nullable=True),
        sa.Column('deferred', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_runs_started_at'), 'runs', ['started_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # Lossy, and there is no honest way to make it otherwise: run history is
    # not derivable from anything else in this schema -- that absence is the
    # whole reason row 53 exists. A downgrade discards every recorded run and
    # a re-upgrade starts an empty table. Nothing else in the schema
    # references `runs`, so the drop is otherwise unremarkable.
    op.drop_index(op.f('ix_runs_started_at'), table_name='runs')
    op.drop_table('runs')
