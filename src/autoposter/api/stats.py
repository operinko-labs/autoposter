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

**``GET /api/stats/runs`` (roadmap row 53)** is served from the same module
and by the same rules: one SELECT over ``runs`` plus a database-clock read,
counts and timestamps only, no path and no ``str(exc)``. What it adds to the
list of things that are *not* served is any aggregation of
``jobs.last_error`` -- a per-run "top errors" breakdown grouped on that column
would re-serve whatever those strings hold, and ``PlexPathMismatch``
deliberately carries the operator's host filesystem paths.

Its count fields are **window attribution**, and the response says so by
serving ``null`` -- never zero -- for a run whose window was not attributed.
See ``db/models.py``'s ``Run`` docstring for what a window can and cannot
mean.
"""

from sqlalchemy import case, func, select

from fastapi import APIRouter, Depends, Query, Request

from autoposter.api.auth import ApiKeyPrincipal, api_key_or_session
from autoposter.catchup import CATCH_UP_KIND
from autoposter.db.models import MediaItem, Render, Run
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


# The maximum a caller may ask for in one page. The retention clause holds the
# whole table to ~500 rows per name, so this is not a memory bound so much as
# a "one request cannot ask for everything" bound -- and a widget's typo
# should be clamped rather than answered with a 422 an operator has to debug
# from a tile that just says `error`.
_MAX_RUNS = 500
_DEFAULT_RUNS = 50


def _rendered(row) -> dict | None:
    """The four art-kind counts, or None when this run was not attributed.

    All four keys together or none of them: a partially-filled mapping would
    let a chart read a zero that means "not measured" as one that means
    "nothing was composited", which are different claims about a pass.

    A closed catch-up (``kind == CATCH_UP_KIND``) also answers None despite
    carrying a non-null ``processed``: that column holds its own tallies --
    backlog marked, budget-exhausted, still-due -- not the four render
    columns this dict reports, which a catch-up never touches (it delivers
    and writes what already exists; nothing is rendered). Serving zeros here
    would claim it composited nothing, which is a different and false claim
    from "this run does not render at all".
    """
    if row.processed is None or row.kind == CATCH_UP_KIND:
        return None
    return {
        "poster": row.rendered_poster or 0,
        "season_poster": row.rendered_season_poster or 0,
        "background": row.rendered_background or 0,
        "title_card": row.rendered_title_card or 0,
    }


async def runs_snapshot(
    session, limit: int = _DEFAULT_RUNS, counted: bool = False
) -> dict:
    """The most recent runs, newest first, with their durations and counts.

    ``counted`` keeps only the runs whose window was attributed -- the ones
    with a ``processed`` count, which is full passes and catch-ups. The
    dashboard's counts chart asks for exactly that, because the plain page is
    the newest ``limit`` runs of any kind, and a scheduled job that records
    every fifteen minutes fills fifty rows in half a day: in production it
    buried the only completed full pass 1,337 rows deep, and the chart said no
    full pass had ever completed.

    Two round trips: the page itself, and ``generated_at`` from the DATABASE
    clock -- never ``datetime.now()``, the rule every timestamp this API
    serves follows (see ``api/snapshots.py::_run_status`` for what mixing the
    two once cost).

    ``duration_seconds`` is computed here rather than stored: it is a
    difference of two columns that are already served, and a stored copy is a
    third thing that can disagree with them. It is ``None`` while the run is
    still open, which is exactly what a chart should skip rather than plot as
    zero.

    The session is the caller's and nothing here writes, so nothing commits.
    """
    bounded = max(1, min(int(limit), _MAX_RUNS))
    query = select(Run)
    if counted:
        query = query.where(Run.processed.is_not(None))
    rows = (
        await session.execute(
            query.order_by(Run.started_at.desc(), Run.id.desc()).limit(bounded)
        )
    ).scalars().all()

    generated_at = (await session.execute(select(func.now()))).scalar_one()

    runs = []
    for row in rows:
        duration = None
        if row.finished_at is not None:
            duration = round((row.finished_at - row.started_at).total_seconds(), 3)
        runs.append(
            {
                "id": row.id,
                "kind": row.kind,
                "name": row.name,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "status": row.status,
                "duration_seconds": duration,
                # The catch-up's server (spec §5), NULL for every other kind,
                # so the runs list can group by it.
                "server": row.server,
                # Already row-213 narrowed by whichever closer wrote it -- a
                # counts sentence, never a job's last_error.
                "detail": row.detail,
                "rendered": _rendered(row),
                # int() rather than the raw column so a chart never receives a
                # value it cannot plot; None stays None, which is the whole
                # point of the column being nullable.
                "processed": None if row.processed is None else int(row.processed),
                "failed": None if row.failed is None else int(row.failed),
                "deferred": None if row.deferred is None else int(row.deferred),
            }
        )

    return {"runs": runs, "generated_at": generated_at}


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


@router.get("/stats/runs")
async def run_stats(
    request: Request,
    limit: int = Query(default=_DEFAULT_RUNS),
    counted: bool = Query(default=False),
    _: SessionModel | ApiKeyPrincipal = Depends(api_key_or_session),
) -> dict:
    """Recent run history: duration, outcome and per-window counts.

    Readable with a browser session OR with the read-only ``X-API-Key``
    (roadmap row 51), like ``/api/stats/storage`` beside it. Both halves of
    that scope have to agree: this handler takes ``api_key_or_session`` AND
    ``/api/stats/runs`` is in ``api.auth.ALLOWLIST``, which the dependency
    checks itself, so a ``Depends`` placed here without the allowlist entry
    fails closed rather than opening a route.

    An out-of-range ``limit`` is clamped; a non-integer is refused with a 422
    (see ``runs_snapshot``). Row 213: the body carries counts, timestamps,
    job names, art-kind tokens, the catch-up's ``server`` (spec §5) and the
    already-narrowed ``detail`` copy -- narrowed by whichever closer wrote it,
    never a job's ``last_error`` or anything else this module has not already
    cut down to a fixed-width counts sentence.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        return await runs_snapshot(session, limit, counted)
