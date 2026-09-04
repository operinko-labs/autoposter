"""status facts: tmdb_status and last_episode_aired

Revision ID: c4f2a19b6d38
Revises: b8c31f4a7e26
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4f2a19b6d38'
down_revision: Union[str, Sequence[str], None] = 'b8c31f4a7e26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roadmap row 100 sub-phase C2c, on the `80f7d7e25a0c_tmdb_facts_widening`
    # model. BOTH nullable with NO server_default, and that is the whole
    # migration: NULL means "TMDb has not told us", which is a different
    # statement from any value -- the distinction that file's own comments
    # record, and the one `persist_facts` relies on when it builds its SET
    # clause from populated fields only.
    #
    # ADDITIVE-NULLABLE, so there is no data rewrite: PostgreSQL's ADD COLUMN
    # with no default is a catalogue-only change and never scans the table,
    # which is what makes this safe against the populated `item_facts` a
    # deployed instance already has. The NOT NULL case is the one that needs
    # a server_default, and neither column here is NOT NULL.
    #
    # NO INDEX, deliberately, and this is where C2c differs from its
    # precedent: `80f7d7e25a0c` indexed `tmdb_collection_id` because the
    # franchise enumeration GROUPS BY it across the whole table. Nothing
    # groups or orders by either column here -- both are read one row at a
    # time, through the `item_facts` row `apply_badges` already loads by
    # `item_id` (which has its own unique constraint). An index would be
    # write cost for no read.
    #
    # NO BACKFILL (adjudication A-4): every existing row is NULL for both
    # columns after this runs, and fills on that item's next facts refresh.
    # The drift sweep converges over weeks at `scheduler.drift_batch_size`
    # per `drift_days`; `api/facts_backfill.py` (roadmap row 206) is the
    # operator's one-time catch-up and already exists. Disclosed in roadmap
    # row 100's C2c cell rather than engineered around.
    op.add_column(
        'item_facts', sa.Column('tmdb_status', sa.String(length=32), nullable=True)
    )
    op.add_column(
        'item_facts', sa.Column('last_episode_aired', sa.Date(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order, so the file reads as the mirror of `upgrade` above.
    # Dropping is lossless in the only direction that matters here: both
    # columns are re-derivable from TMDb by a facts refresh, and neither is
    # referenced by a foreign key, an index or a constraint.
    op.drop_column('item_facts', 'last_episode_aired')
    op.drop_column('item_facts', 'tmdb_status')
