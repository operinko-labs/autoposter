"""config overrides single row

Revision ID: 9a1f4c72be05
Revises: d41c8f0b7e93
Create Date: 2026-08-23 18:40:02.113554

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9a1f4c72be05'
down_revision: Union[str, Sequence[str], None] = 'd41c8f0b7e93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Every reader of this table selects id = 1 and the only writer upserts
    # id = 1, so a second row would be configuration in name only: stored,
    # visible, and never applied. Autogenerate does not diff CHECK
    # constraints, so this is hand-written rather than generated -- see the
    # matching __table_args__ on db.models.ConfigOverride. `alembic check`
    # does compare named CHECK constraints, so tests/test_migrations.py goes
    # red if this and the model ever disagree.
    op.create_check_constraint(
        'ck_config_overrides_single_row', 'config_overrides', 'id = 1'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        'ck_config_overrides_single_row', 'config_overrides', type_='check'
    )
