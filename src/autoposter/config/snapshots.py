"""History for the overrides document, and the read side of restoring it.

Deliberately NOT ``autoposter.api.snapshots``: that name is already taken by
the status/events payload builders behind ``GET /api/status`` and the dashboard
stream, and a second meaning for it would be a trap for every later reader.
This lives beside ``config/overrides.py``, which is where the document's other
readers already live.

The capture itself takes a caller's session and adds to it rather than opening
one, because it has to land in the same transaction as the write it is a
snapshot of. See ``api/routes._persist_and_swap``.
"""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.config.overrides import STORE_FORMAT, document_paths
from autoposter.db.models import ConfigOverrideSnapshot

#: How many previous documents to keep. A module constant rather than a
#: setting, for the same reason the drop cap is one: a recovery depth an
#: operator can lower from the page they are about to break is not a recovery
#: depth. Twenty covers a bad afternoon and costs a few kilobytes.
SNAPSHOT_RETENTION = 20


async def capture_snapshot(
    session: AsyncSession, document: dict, reason: str, *, format: int = STORE_FORMAT
) -> None:
    """Record ``document`` as the state a write is about to replace.

    Adds to the caller's session and does not commit: the snapshot and the
    write it protects must land together, or the history says something that
    did not happen.

    ``format`` says what the document IS: 2 is a whole configuration document,
    1 a delta from before the store became the document
    (``config/overrides.STORE_FORMAT``). Defaulted rather than required so the
    four callers that snapshot the current store -- save, apply, restore,
    import -- keep saying what they always said, which is "this is the store".

    An empty outgoing document is skipped. There is nothing to restore to, and
    a fresh deployment would otherwise fill the table with empty rows before
    ever having anything worth keeping.

    Pruning runs here, inline, in the same transaction. Unbounded growth on a
    config table is not acceptable and a scheduled pruner is more machinery
    than twenty rows deserve.
    """
    if not document:
        return
    session.add(
        ConfigOverrideSnapshot(
            document=document,
            path_count=len(document_paths(document)),
            reason=reason,
            format=format,
        )
    )
    # So the row about to be inserted is inside the keep set and cannot prune
    # itself out of existence on a table already at the retention limit.
    await session.flush()
    keep = (
        select(ConfigOverrideSnapshot.id)
        .order_by(ConfigOverrideSnapshot.id.desc())
        .limit(SNAPSHOT_RETENTION)
    )
    await session.execute(
        delete(ConfigOverrideSnapshot).where(
            ConfigOverrideSnapshot.id.not_in(keep.scalar_subquery())
        )
    )


async def list_snapshots(session: AsyncSession) -> list[dict]:
    """Every kept snapshot's metadata, newest first -- and no documents.

    The list UI needs to label its rows ("3 settings, 14:22 today"), not to
    hold twenty configurations. One of those configurations holds a push token,
    so shipping them all to render a list would be a leak with no upside.

    Ordered by ``id``, not by ``created_at``: identity is monotonic, and this
    project has a recorded deployment whose clock is not.
    """
    result = await session.execute(
        select(
            ConfigOverrideSnapshot.id,
            ConfigOverrideSnapshot.created_at,
            ConfigOverrideSnapshot.path_count,
            ConfigOverrideSnapshot.reason,
        ).order_by(ConfigOverrideSnapshot.id.desc())
    )
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "path_count": row.path_count,
            "reason": row.reason,
        }
        for row in result
    ]


async def load_snapshot(session: AsyncSession, snapshot_id: int) -> dict:
    """One snapshot's stored document, exactly as it was captured.

    Unredacted, because both callers need it that way for opposite reasons: the
    restore has to write the real ``notifications.url`` back, and the single-
    snapshot GET redacts it itself with the same machinery ``GET /api/config``
    uses. Redacting here would quietly make restore destroy a push token.

    Raises ``LookupError`` when there is no such row -- the caller owns the HTTP
    status, this module does not import fastapi.
    """
    row = await session.get(ConfigOverrideSnapshot, snapshot_id)
    if row is None:
        raise LookupError(f"no config snapshot {snapshot_id}")
    return dict(row.document)
