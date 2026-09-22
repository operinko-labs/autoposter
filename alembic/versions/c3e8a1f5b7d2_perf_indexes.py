"""perf indexes: the jobs readers' own indexes, a partial due index, six duplicates gone

Revision ID: c3e8a1f5b7d2
Revises: a1f4c2d90e73
Create Date: 2026-09-22 00:00:00.000000

Perf spec D1. Plain CREATE INDEX rather than CONCURRENTLY: env.py runs every
migration inside one transaction at boot (CONCURRENTLY refuses to run in
one), and these tables are tens of thousands of rows -- a build measured in
seconds against a pod that is not serving yet.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3e8a1f5b7d2'
down_revision: Union[str, Sequence[str], None] = 'a1f4c2d90e73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index, table, column). Each one duplicates the leading column of an index
# that stays: every `WHERE <column> = ?` it could serve, the survivor serves
# too, and each is one more b-tree maintained on every write for nothing --
# the argument models.py already makes for metadata_writes.item_id.
_REDUNDANT = (
    # ix_jobs_state_updated (state, updated_at DESC, id DESC) leads with state.
    ('ix_jobs_state', 'jobs', 'state'),
    # uq_item_facts_item (item_id).
    ('ix_item_facts_item_id', 'item_facts', 'item_id'),
    # uq_delivery_render_server (render_id, server).
    ('ix_render_deliveries_render_id', 'render_deliveries', 'render_id'),
    # uq_render_item_kind (item_id, art_kind).
    ('ix_renders_item_id', 'renders', 'item_id'),
    # uq_item_metadata_override_item_field (item_id, field).
    ('ix_item_metadata_overrides_item_id', 'item_metadata_overrides', 'item_id'),
    # uq_action_dismissal_item_kind (item_id, art_kind).
    ('ix_action_dismissals_item_id', 'action_dismissals', 'item_id'),
)


def upgrade() -> None:
    """Upgrade schema."""
    # The "state X, newest first" readers: the dashboard's processed-last-24h
    # count, the Failures list (parked) and the done-with-warnings list, all
    # of which ORDER BY updated_at DESC, id DESC within one state.
    op.create_index(
        'ix_jobs_state_updated', 'jobs',
        ['state', sa.text('updated_at DESC'), sa.text('id DESC')],
    )
    # Action Center's DISTINCT ON (dedupe_key) ... ORDER BY dedupe_key, id
    # DESC, and retention's "is there a newer row for this key" probe.
    op.create_index(
        'ix_jobs_latest_per_key', 'jobs',
        ['dedupe_key', sa.text('id DESC')],
        postgresql_where=sa.text("kind = 'process_item' AND dedupe_key IS NOT NULL"),
    )
    # The claim orders by (run_after, id) over the two claimable states; the
    # old (state, run_after) index answered the WHERE and left a sort.
    op.drop_index('ix_jobs_claimable', table_name='jobs')
    op.create_index(
        'ix_jobs_claim', 'jobs', ['run_after', 'id'],
        postgresql_where=sa.text("state IN ('pending', 'deferred')"),
    )
    # PARTIAL, mirroring f1c8d24a9e73's change to the metadata_writes twin:
    # every non-pending row carries a NULL next_attempt_at, and the retry
    # pass -- the column's only reader -- asks only for due pending rows,
    # ordered (next_attempt_at, id).
    op.drop_index('ix_render_deliveries_next_attempt_at', table_name='render_deliveries')
    op.create_index(
        'ix_render_deliveries_next_attempt_at', 'render_deliveries',
        ['next_attempt_at', 'id'],
        postgresql_where=sa.text("status = 'pending'"),
    )
    for name, table, _column in _REDUNDANT:
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    """Downgrade schema."""
    for name, table, column in _REDUNDANT:
        op.create_index(name, table, [column], unique=False)
    op.drop_index('ix_render_deliveries_next_attempt_at', table_name='render_deliveries')
    op.create_index(
        'ix_render_deliveries_next_attempt_at', 'render_deliveries', ['next_attempt_at'],
    )
    op.drop_index('ix_jobs_claim', table_name='jobs')
    op.create_index('ix_jobs_claimable', 'jobs', ['state', 'run_after'], unique=False)
    op.drop_index('ix_jobs_latest_per_key', table_name='jobs')
    op.drop_index('ix_jobs_state_updated', table_name='jobs')
