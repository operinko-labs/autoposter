"""merge collection poster hash and sessions

Revision ID: 6c515e89a1f0
Revises: 61c977285fa7, b4b52d7f26df
Create Date: 2026-08-21 21:10:13.543203

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c515e89a1f0'
down_revision: Union[str, Sequence[str], None] = ('61c977285fa7', 'b4b52d7f26df')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
