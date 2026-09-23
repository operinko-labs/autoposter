"""settle background renders' upload_status to skipped

Revision ID: e8c3f5a2b691
Revises: d4b7e1a9c250
Create Date: 2026-09-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e8c3f5a2b691'
down_revision: Union[str, Sequence[str], None] = 'd4b7e1a9c250'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# What `d4b7e1a9c250` left: it removed the backgrounds' phantom `plex`
# delivery rows, but every background render still reads
# `renders.upload_status = 'pending'` (2,253 on 2026-09-23). That is the column
# default, not a roll-up: `deliver` records nothing for a background, so no
# roll-up ever ran for one, and the item page and the action centre show
# `pending` for artwork nothing will ever send. `skipped` is
# `deliveries.rollup`'s own word for a render owed nothing, and
# `pipeline._get_or_create_render` now gives it to every NEW background at
# birth; this brings the existing ones into line.
#
# `pending` only: a background whose roll-up says anything else got it from
# real delivery rows (`d4b7e1a9c250` kept any background row carrying an
# `uploaded_at`), and those are the truth about it.
_SETTLE_BACKGROUND_ROLLUP_SQL = """
    UPDATE renders
       SET upload_status = 'skipped'
     WHERE art_kind = 'background'
       AND upload_status = 'pending'
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(_SETTLE_BACKGROUND_ROLLUP_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    # Not reversed, like `d4b7e1a9c250`: the backgrounds' `pending` was a
    # column default nothing meant, and downgrading the schema is not a
    # reason to put it back. There is no schema change here to undo.
    pass
