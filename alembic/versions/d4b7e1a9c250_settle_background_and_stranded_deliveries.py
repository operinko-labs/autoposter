"""settle background and stranded delivery rows

Revision ID: d4b7e1a9c250
Revises: 5b9e2c7d4a18
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd4b7e1a9c250'
down_revision: Union[str, Sequence[str], None] = '5b9e2c7d4a18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The 2026-09-23 incident this migration exists to fix. `c1d2e3f4a5b6`
# backfilled one `plex` delivery row per render straight from the old
# `renders.upload_status` -- `pending` for every background, which had never
# been uploaded because backgrounds are never badged or delivered
# (`pipeline.deliver` and `compose_badged_bytes` both return before a server
# is asked). That left 2,249 background rows `pending` with no horizon that
# nothing would ever settle, and `deliveries.outcome_warnings` named every one
# of them: the night's full pass finished 2,246 jobs `done_with_warnings` on
# `plex: artwork pending`. `outcome_warnings` and `servers.presence` now leave
# backgrounds out; this is the one-time removal of what the backfill created.
#
# A background row that DOES carry an `uploaded_at` is kept: it is a record
# of what a server was once sent, and that is not ours to erase. None exists
# in production today.
_DELETE_UNDELIVERED_BACKGROUND_ROWS_SQL = """
    DELETE FROM render_deliveries
     USING renders
     WHERE renders.id = render_deliveries.render_id
       AND renders.art_kind = 'background'
       AND render_deliveries.uploaded_at IS NULL
"""

# The same backfill's other casualty: 5 `title_card` rows `pending` with a
# NULL `next_attempt_at`, which the retry pass can never select -- it asks
# `next_attempt_at <= now`, and NULL is never that. Every code path that
# writes `pending` also writes a horizon, so a NULL one is only ever this
# backfill's; `now()` makes each due on the next `pending_deliveries` run,
# which is what a row that has waited since the backfill is owed.
# `metadata_writes` has no such row in production and gets the same
# statement for symmetry, so the invariant "pending means a horizon" holds
# in both outcome tables after this revision.
_ARM_STRANDED_SQL = """
    UPDATE {table}
       SET next_attempt_at = now()
     WHERE status = 'pending'
       AND next_attempt_at IS NULL
"""


def upgrade() -> None:
    """Upgrade schema."""
    # Delete first, so the arming below never gives a background row a
    # horizon only for the DELETE to discard it.
    op.execute(_DELETE_UNDELIVERED_BACKGROUND_ROWS_SQL)
    for table in ('render_deliveries', 'metadata_writes'):
        op.execute(_ARM_STRANDED_SQL.format(table=table))


def downgrade() -> None:
    """Downgrade schema."""
    # Not reversed, like `ae7e4aa59f8a`'s collapse: the deleted rows were
    # noise no pass could settle and the armed rows were stranded, and
    # downgrading the schema is not a reason to put either back. There is no
    # schema change here to undo.
    pass
