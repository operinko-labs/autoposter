"""the provider clearlogo a poster last composited, and its digest

Revision ID: 5b9e2c7d4a18
Revises: c3e8a1f5b7d2
Create Date: 2026-09-22 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5b9e2c7d4a18'
down_revision: Union[str, Sequence[str], None] = 'c3e8a1f5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no server_default: NULL means "not recorded yet", which is the
    # truth for every existing row, and render_artifact records both on the
    # row's next pass (perf workstream B1).
    op.add_column('renders', sa.Column('logo_source_url', sa.Text(), nullable=True))
    op.add_column('renders', sa.Column('logo_sha256', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('renders', 'logo_sha256')
    op.drop_column('renders', 'logo_source_url')
