"""widen jobs dedupe index to cover deferred

Revision ID: f3aec27ebea8
Revises: a9d4e70c3b15
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f3aec27ebea8'
down_revision: Union[str, Sequence[str], None] = 'a9d4e70c3b15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Collapses any dedupe_key with more than one pending/deferred row down to a
# single survivor before the widened index below is created -- CREATE UNIQUE
# INDEX would otherwise fail outright against a deployed instance carrying
# duplicates (the production incident this migration exists to fix: 11
# deferred rows piled up for one item, each minted because the old index only
# ever saw 'pending'). The oldest row survives -- it is the one whichever
# consumer is already watching (a UI, a dedupe_key lookup) -- and every other
# row for that key is dismissed, not deleted, matching every other disposal
# in this project.
_COLLAPSE_DUPLICATE_DEDUPE_SQL = """
    WITH ranked AS (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY dedupe_key ORDER BY created_at ASC, id ASC
               ) AS ordinal
          FROM jobs
         WHERE dedupe_key IS NOT NULL
           AND state IN ('pending', 'deferred')
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
    op.execute(_COLLAPSE_DUPLICATE_DEDUPE_SQL)
    op.drop_index('uq_jobs_pending_dedupe', table_name='jobs', postgresql_where=sa.text("state = 'pending'"))
    op.create_index(
        'uq_jobs_pending_dedupe',
        'jobs',
        ['dedupe_key'],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'deferred')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'uq_jobs_pending_dedupe',
        table_name='jobs',
        postgresql_where=sa.text("state IN ('pending', 'deferred')"),
    )
    op.create_index(
        'uq_jobs_pending_dedupe',
        'jobs',
        ['dedupe_key'],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    # The collapse above is not reversed: it dismissed rows that were already
    # in a bad, unbounded-duplicate state, and downgrading the schema is not a
    # reason to resurrect them.
