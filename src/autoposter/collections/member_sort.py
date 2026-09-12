"""Roadmap row 269 -- the collections pass's half of member sort.

``record`` is called from ``engine._run_one`` at the one point that holds a
definition's ordered, filtered, capped members. ``commit_assignment`` is
called once per library from ``engine.run_library`` after every definition
ran: it applies the ownership rule, upserts the survivors, RELEASES departed
members (never deletes -- the pipeline owes each one a blank-and-unlock
write first, ``facts/gather.py`` and ``plex/writer.py``), and hands back the
``process_item`` entries ``service.reconcile_libraries`` enqueues below its
commit.

**The settled rule**, verbatim from the spec (``docs/design/2026-09-12-
member-sort-design.md`` §9): a definition releases members only when it ran
this pass and resolved at least one member. A failed, gated, empty or
filter-emptied definition leaves its rows untouched -- an outage must not
strip a franchise's sort titles. A placeholder that could not be expanded,
or was outside its schedule, cannot even NAME its units, so it holds every
release in the library for this pass (``hold_releases``).

**Ownership**: the first definition to claim an item in ``run_library``'s
order wins it; the loser is logged once per definition at commit, with the
count and the winner, never per item.
"""
import logging
from dataclasses import asdict, dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemSortPosition, MediaItem
from autoposter.facts.franchise_sort import sort_base
from autoposter.intake.arr import RenderIntent

logger = logging.getLogger(__name__)

_KEY = "member_sort.assignment"


@dataclass
class Assignment:
    """One library's pass, as far as member sort is concerned."""

    # rating_key -> (definition_title, base, position, total); first writer wins.
    held: dict[str, tuple[str, str, int, int]] = field(default_factory=dict)
    # Definitions with member_sort that ran and recorded nothing this pass.
    unsettled: set[str] = field(default_factory=set)
    # definition_title -> (members it lost, the title that owns them).
    contested: dict[str, tuple[int, str]] = field(default_factory=dict)
    hold_reason: str | None = None


def _assignment(run_cache: dict, library: str) -> Assignment:
    return run_cache.setdefault(_KEY, {}).setdefault(library, Assignment())


def record(run_cache: dict, library: str, definition_title: str, items: list) -> int:
    """Claim positions for ``items`` in this order; returns the NEW claims.

    An empty ``items`` marks the definition unsettled rather than claiming
    nothing silently -- that is what keeps its existing rows from being
    released at commit.
    """
    assignment = _assignment(run_cache, library)
    if not items:
        assignment.unsettled.add(definition_title)
        return 0
    base = sort_base(definition_title) or definition_title
    total = len(items)
    claimed = 0
    lost = 0
    owner = ""
    for position, item in enumerate(items, start=1):
        key = str(item.ratingKey)
        if key in assignment.held:
            lost += 1
            owner = assignment.held[key][0]
            continue
        assignment.held[key] = (definition_title, base, position, total)
        claimed += 1
    if lost:
        assignment.contested[definition_title] = (lost, owner)
    return claimed


def hold_releases(run_cache: dict, library: str, reason: str) -> None:
    """This pass may not release anything in ``library``: a definition whose
    units cannot be named this pass could own any of the rows."""
    _assignment(run_cache, library).hold_reason = reason


async def commit_assignment(
    session: AsyncSession, run_cache: dict, library: str, dry_run: bool,
) -> tuple[list[str], list[tuple[dict, str]]]:
    """Write this library's assignment. Returns ``(actions, reprocess)``.

    Inside the per-library transaction ``reconcile_libraries`` commits, so a
    failure here is that library's failure and rolls back with it. Under
    ``dry_run`` nothing is written and nothing is enqueued; the would-be
    counts are reported, like every other write in the pass.
    """
    assignment = _assignment(run_cache, library)
    for title, (lost, owner) in sorted(assignment.contested.items()):
        logger.info(
            "%s: %r lists %d member(s) %r already owns; the first definition wins",
            library, title, lost, owner,
        )

    media: dict[str, MediaItem] = {}
    if assignment.held:
        rows = (
            await session.execute(
                select(MediaItem).where(
                    MediaItem.library == library,
                    MediaItem.rating_key.in_(list(assignment.held)),
                )
            )
        ).scalars().all()
        media = {row.rating_key: row for row in rows}
    missing = [key for key in assignment.held if key not in media]
    if missing:
        logger.info(
            "%s: %d member(s) have no media_items row yet and get no sort position "
            "this pass; the pass after they are first processed will",
            library, len(missing),
        )

    current = {
        row.item_id: row
        for row in (
            await session.execute(
                select(ItemSortPosition).where(ItemSortPosition.library == library)
            )
        ).scalars()
    }
    wanted = {
        media[key].id: (key, title, base, position, total)
        for key, (title, base, position, total) in assignment.held.items()
        if key in media
    }
    changed = [
        item_id
        for item_id, (_, _, base, position, total) in wanted.items()
        if item_id not in current
        or current[item_id].released_at is not None
        or (current[item_id].base, current[item_id].position, current[item_id].total)
        != (base, position, total)
    ]
    to_release = [
        item_id
        for item_id, row in current.items()
        if item_id not in wanted
        and row.released_at is None
        and row.definition_title not in assignment.unsettled
    ]
    if assignment.hold_reason is not None and to_release:
        logger.info(
            "%s: holding %d release(s) this pass: %s",
            library, len(to_release), assignment.hold_reason,
        )
        to_release = []

    if dry_run:
        return [
            "%s: would record %d sort position(s) and release %d"
            % (library, len(wanted), len(to_release))
        ], []

    if wanted:
        statement = insert(ItemSortPosition).values([
            {
                "item_id": item_id, "library": library, "definition_title": title,
                "base": base, "position": position, "total": total,
                "released_at": None,
            }
            for item_id, (_, title, base, position, total) in wanted.items()
        ])
        await session.execute(statement.on_conflict_do_update(
            index_elements=["item_id"],
            set_={
                "library": statement.excluded.library,
                "definition_title": statement.excluded.definition_title,
                "base": statement.excluded.base,
                "position": statement.excluded.position,
                "total": statement.excluded.total,
                "released_at": None,
                "recorded_at": func.now(),
            },
        ))
    if to_release:
        await session.execute(
            update(ItemSortPosition)
            .where(ItemSortPosition.item_id.in_(to_release))
            .values(released_at=func.now())
        )

    reprocess: list[tuple[dict, str]] = []
    released_rows: dict[int, MediaItem] = {}
    if to_release:
        released_rows = {
            row.id: row
            for row in (
                await session.execute(select(MediaItem).where(MediaItem.id.in_(to_release)))
            ).scalars()
        }
    for item_id in [*changed, *to_release]:
        row = media[wanted[item_id][0]] if item_id in wanted else released_rows[item_id]
        intent = RenderIntent(
            kind=row.kind, title=row.title, tmdb_id=row.tmdb_id, tvdb_id=row.tvdb_id,
            imdb_id=row.imdb_id, year=row.year, season_number=row.season_number,
            episode_number=row.episode_number, rating_key=row.rating_key,
        )
        reprocess.append((asdict(intent), intent.dedupe_key))

    actions: list[str] = []
    if wanted or to_release:
        actions.append(
            "%s: recorded %d sort position(s), released %d"
            % (library, len(wanted), len(to_release))
        )
    return actions, reprocess
