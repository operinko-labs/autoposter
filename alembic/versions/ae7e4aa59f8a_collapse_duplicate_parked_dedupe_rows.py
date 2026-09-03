"""collapse duplicate parked dedupe rows

Revision ID: ae7e4aa59f8a
Revises: f3aec27ebea8
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'ae7e4aa59f8a'
down_revision: Union[str, Sequence[str], None] = 'f3aec27ebea8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The Failures-page incident this migration exists to fix: before #138
# stopped re-selecting blocked items, every backfill press for an
# already-parked item minted a fresh job that failed and parked alongside
# the previous parked rows -- 12 piled up for one movie, all "Inside Out 2".
# uq_jobs_pending_dedupe only ever covered 'pending' and 'deferred' (see its
# comment in db/models.py), so nothing about the index stopped the pile from
# growing. queue/jobs.py's fail() now sweeps its own parked siblings the
# moment a job parks, so the pile stops growing going forward -- this
# migration is the one-time collapse of what already piled up.
#
# The index itself is deliberately left uncovering 'parked': a parked item
# must stay re-enqueueable by an explicit retry or a fresh search (#138's
# design), and a unique index over 'parked' would block exactly that. The
# sweep in fail() is what keeps the count honest, not a schema constraint --
# see fail()'s docstring.
#
# The newest row survives here, not the oldest (contrast the pending/
# deferred collapse in f3aec27ebea8, which keeps the oldest): it carries the
# freshest failure reason, and Retry always acts on whichever row is left.
_COLLAPSE_DUPLICATE_PARKED_SQL = """
    WITH ranked AS (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY dedupe_key ORDER BY id DESC
               ) AS ordinal
          FROM jobs
         WHERE dedupe_key IS NOT NULL
           AND state = 'parked'
    )
    UPDATE jobs
       SET state = 'dismissed',
           updated_at = now()
      FROM ranked
     WHERE jobs.id = ranked.id
       AND ranked.ordinal > 1
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(_COLLAPSE_DUPLICATE_PARKED_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    # Not reversed: the collapsed rows were already a bad, unbounded-duplicate
    # pile, and downgrading the schema is not a reason to resurrect them --
    # matching f3aec27ebea8's own collapse.
    pass
