"""renders.size_bytes: the bytes on disk for each published artifact

Revision ID: a3f81c05d6e2
Revises: e9b25c07af13
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a3f81c05d6e2'
down_revision: Union[str, Sequence[str], None] = 'e9b25c07af13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Roadmap row 52. One nullable column, no backfill and no data rewrite:
    # NULL is the correct value for every existing row, meaning "never
    # measured". The scheduled `asset_stats` pass (scheduler/jobs.py) fills
    # them in over successive runs, in bounded batches, off the request path.
    #
    # BigInteger rather than Integer: a title card is small, but the column
    # is summed across a whole library and there is no reason to make the
    # sum's width the one thing that has to be revisited.
    #
    # No server_default. The `upload_status` precedent (server_default
    # "pending") exists because that column is NOT NULL against a populated
    # renders table; this one is nullable precisely so that the absence of a
    # measurement stays visible instead of being reported as zero bytes.
    op.add_column('renders', sa.Column('size_bytes', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Lossless in the only sense that matters: the column is a cache of a
    # filesystem fact. Everything it held can be re-derived by re-running the
    # asset_stats pass after a re-upgrade.
    op.drop_column('renders', 'size_bytes')
