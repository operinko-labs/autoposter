"""widen jobs.state so done_with_warnings fits

Revision ID: b3d91f7c05ea
Revises: a3f7e15c92b8
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3d91f7c05ea'
down_revision: Union[str, Sequence[str], None] = 'a3f7e15c92b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `jobs.state` has no CHECK constraint, so the state vocabulary has always
    # widened in the model's comment alone. `done_with_warnings` is the first
    # word that does not fit: eighteen characters against a VARCHAR(16), which
    # PostgreSQL rejects outright. Twenty-four matches `render_deliveries`'
    # and `metadata_writes`' own status columns.
    op.alter_column(
        'jobs', 'state',
        existing_type=sa.String(length=16),
        type_=sa.String(length=24),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Every row that would not fit back into sixteen characters is a FINISHED
    # job, so narrowing it back reports the one thing the narrow column cannot
    # hold: the warning. `done` with the sentence left in `last_error` is the
    # honest loss -- the per-server rows the sentence was read off are
    # untouched, so nothing about the item's actual state is discarded.
    op.execute("UPDATE jobs SET state = 'done' WHERE state = 'done_with_warnings'")
    op.alter_column(
        'jobs', 'state',
        existing_type=sa.String(length=24),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
