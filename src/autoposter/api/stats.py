"""The storage numbers behind ``GET /api/stats/storage`` (roadmap row 52).

**This module never touches the filesystem.** Every number here comes out of
three round trips: two SELECTs over ``renders`` joined to ``media_items`` (a
grouped count/sum and a distinct-item count) plus a database-clock read for
``generated_at``; the bytes come from
``renders.size_bytes``, stamped by ``render/pipeline.py`` when each artifact
is published and back-filled for older rows by the scheduled ``asset_stats``
pass (``scheduler/jobs.py``). ``assets_root`` is an NFS PV, and a widget
polling every sixty seconds must never be able to make a request wait on a
mount -- that is the whole reason the walk is a scheduled job and not a lazy
refresh here. ``tests/test_api_stats_storage.py`` pins it by making ``os.walk``
and ``Path.stat`` raise and asserting the endpoint still answers.

What is served: counts, byte totals, a timestamp, library names and art-kind
tokens. **No path** -- not ``asset_path``, not ``assets_root``, not any root
(roadmap row 213; an operator's filesystem layout is a disclosure). The
library names are already served by ``GET /api/items/filters``.

**The contract on the numbers themselves:** ``totals.bytes`` is a floor, not
a measurement, while any ``unknown_size`` remains -- an artifact whose file
could not be read still counts in ``assets`` (it was rendered) but
contributes 0 to ``bytes``, and a row zeroed this way is corrected only when
that artifact is next re-rendered (the pipeline's own "unchanged"
short-circuit returns before the size is ever touched, so a pass that wrote
nothing never restamps).
"""

from sqlalchemy import case, func, select

from fastapi import APIRouter, Depends, Request

from autoposter.api.auth import ApiKeyPrincipal, api_key_or_session
from autoposter.db.models import MediaItem, Render
from autoposter.db.models import Session as SessionModel

# The four artifact kinds, in the order the response reports them. Every
# library's ``by_art_kind`` carries all four, zero-filled -- ``jobs_by_state``'s
# rule (api/snapshots.py): always all keys, so a dashboard mapping never points
# at a field that vanished because a library happens to have no title cards.
ART_KINDS = ("poster", "season_poster", "background", "title_card")

# Only a `rendered` row names a file that exists. `pending`, `no_art`,
# `skipped`, `truncated` and `failed` rows all carry an asset_path that was
# never written, and counting them would report storage nobody is using.
_COUNTED_STATUS = "rendered"


def _zero() -> dict:
    return {"assets": 0, "bytes": 0, "unknown_size": 0}


async def storage_snapshot(session) -> dict:
    """Counts and bytes for every rendered artifact, by library and art kind.

    Three round trips, not one. The grouped pass below is the
    ``/actions/summary`` pattern (one grouped scan with a conditional sum
    rather than a query per cell). ``totals.items`` cannot come out of it: a
    distinct-item count is not summable across groups -- four artifacts for
    one show are one item -- so it is its own ``COUNT(DISTINCT)`` over the
    same join. The third is ``generated_at``.

    ``generated_at`` is read from the DATABASE clock, never ``datetime.now()``:
    every other timestamp this API serves is Postgres's, and mixing the two is
    how the dashboard once mislabelled a healthy run (see
    ``api/snapshots.py::_run_status``).

    The session is the caller's and none of the three statements writes, so
    nothing here commits.
    """
    grouped = (
        await session.execute(
            select(
                MediaItem.library,
                Render.art_kind,
                func.count(),
                func.coalesce(func.sum(Render.size_bytes), 0),
                func.sum(case((Render.size_bytes.is_(None), 1), else_=0)),
            )
            .join(MediaItem, Render.item_id == MediaItem.id)
            .where(Render.status == _COUNTED_STATUS)
            .group_by(MediaItem.library, Render.art_kind)
        )
    ).all()

    items = (
        await session.execute(
            select(func.count(func.distinct(Render.item_id)))
            .select_from(Render)
            .join(MediaItem, Render.item_id == MediaItem.id)
            .where(Render.status == _COUNTED_STATUS)
        )
    ).scalar_one()

    generated_at = (await session.execute(select(func.now()))).scalar_one()

    by_library: dict[str, dict] = {}
    totals = _zero()
    for library, art_kind, assets, size_bytes, unknown in grouped:
        # int() on both aggregates, deliberately: PostgreSQL's SUM over a
        # bigint column is NUMERIC, which asyncpg hands back as a Decimal --
        # and a Decimal in the response body serialises as a string, which is
        # not a number a widget can format. COUNT is already an int.
        size_bytes = int(size_bytes)
        unknown = int(unknown)
        entry = by_library.setdefault(
            library,
            {**_zero(), "by_art_kind": {kind: _zero() for kind in ART_KINDS}},
        )
        entry["assets"] += assets
        entry["bytes"] += size_bytes
        entry["unknown_size"] += unknown
        # A row whose art_kind is not one of the four (nothing writes one
        # today) still counts toward its library's totals and simply has no
        # per-kind cell -- the alternative is inventing a key from stored
        # data, which is how an unexpected token reaches a served surface.
        kind_entry = entry["by_art_kind"].get(art_kind)
        if kind_entry is not None:
            kind_entry["assets"] += assets
            kind_entry["bytes"] += size_bytes
            kind_entry["unknown_size"] += unknown
        totals["assets"] += assets
        totals["bytes"] += size_bytes
        totals["unknown_size"] += unknown

    return {
        "totals": {"items": items, **totals},
        "by_library": by_library,
        "generated_at": generated_at,
    }


router = APIRouter()


@router.get("/stats/storage")
async def storage_stats(
    request: Request,
    _: SessionModel | ApiKeyPrincipal = Depends(api_key_or_session),
) -> dict:
    """Counts and bytes for every rendered artifact, by library and art kind.

    Readable with a browser session OR with the read-only ``X-API-Key``
    (roadmap row 51) -- the intended consumer is a gethomepage ``customapi``
    widget, which has no session. Both halves of row 51's scope have to agree:
    this handler takes ``api_key_or_session`` AND ``/api/stats/storage`` is in
    ``api.auth.ALLOWLIST``, which the dependency checks itself, so a
    ``Depends`` placed here without the allowlist entry fails closed rather
    than opening a route.

    One session, three round trips, no filesystem (see this module's docstring).
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        return await storage_snapshot(session)
