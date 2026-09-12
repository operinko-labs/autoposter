"""Merging the twin ``media_items`` rows a re-key was too late to prevent.

An item re-matched or renumbered in Plex resolves to a key its stored row does
not hold. ``_upsert_media_item`` (``render/pipeline.py``) is an ``ON CONFLICT
(rating_key)`` upsert, so before that module learned to re-key, a new key
conflicted with nothing and INSERTED: a second row that inherited every future
render, while the original kept its renders, its facts, its credits, its
dismissals, its ``logo_upload_key`` and its children and was never written
again.

``scheduler/prune.py`` cannot own this population and could not be made to
without changing what "gone" means: its probe asks whether the pipeline can
still resolve a row, and a re-keyed item resolves -- by the same GUID walk
that forked it. That is why the prune sweep correctly reported ``pruned 0``
against a library full of twins. Deleting the stale row IS the right end, but
it is a merge's last step and not the whole of it: the stale row can hold the
only ``item_facts``, the only credits, the only ``logo_upload_key`` and the
parent link for every season and episode under it.

A maintenance job rather than a migration, for four reasons in order of
weight. The population regenerates until every producing path carries the fix.
The survivor election needs a live Plex probe, and Alembic must never depend
on a reachable server. The operator's stated need is to see it first, which
``apply: false`` plus one summary string already is everywhere else in this
tree. And a migration cannot refuse and cannot be resumed -- a partial failure
mid-merge would leave a half-repointed graph with no audit trail.

**Nothing moves on disk.** ``render/naming.py``'s ``asset_path`` is keyed by
library and root folder, never by the rating key, so both rows of a pair
record byte-identical paths: repointing a render leaves the file where it is,
and deleting one leaves it there too. ``asset_cleanup`` needs no change.

**The cascade is the danger.** ``media_items.parent_id`` is a self-FK with ON
DELETE CASCADE, so deleting the stale row of a show pair would take every
season and episode under it. Children are repointed onto the survivor BEFORE
the delete, in the same transaction. Nothing in this module may be reordered
past that.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import SHARE_CHECK_MIN_ITEMS, refuse_if_empty
from autoposter.config.holder import ConfigHolder
from autoposter.db.models import (
    ActionDismissal,
    EventLog,
    ItemCredit,
    ItemFacts,
    ItemMetadataOverride,
    MediaItem,
    Render,
)
from autoposter.db.refs import native_ids as native_ids_for
from autoposter.db.refs import refs_for
from autoposter.intake.arr import RenderIntent
from autoposter.scheduler.core import Job
# Reused verbatim rather than reimplemented: an in-flight payload names a
# Plex native id and nothing else, and two spellings of "find the jobs for
# this id" would eventually disagree about which states count.
from autoposter.scheduler.prune import dismiss_jobs_for

logger = logging.getLogger(__name__)

# The events_log identity of a merge. Constants for the reason
# PRUNE_SOURCE/PRUNE_EVENT are: the audit row is the only surviving record of
# a deleted row, and a typo in either would make them unfindable.
MERGE_SOURCE = "merge"
MERGE_EVENT = "media_items_merged"


class MergeRefused(Exception):
    """A pass that could not be trusted to run at all.

    The ``PruneRefused`` shape, and drawn on the same line: raised for what
    interrupts the pass mid-walk with its own state no longer known; returned
    as an ordinary summary for a pass that ran correctly and declined the work
    it found, and for the unhealthy-Plex refusal, which describes a pass that
    never started.
    """

    # Read by scheduler/core.py's failure branch: every message this is raised
    # with is hand-built served-safe -- the probe-failure site names the
    # exception class only, never str(exc) and never a URL -- so the scheduler
    # serves it verbatim instead of narrowing it to "MergeRefused".
    served_detail = True


@dataclass(frozen=True)
class MergeRow:
    """One ``media_items`` row, as plain data.

    Read as columns rather than as an ORM object for ``PruneCandidate``'s
    reason: the scan holds the whole library at once, and ``updated_at`` has
    to survive into the delete as a value the session cannot refresh
    underneath it -- it is half the delete's key.
    """

    id: int
    native_id: str | None
    kind: str
    library: str
    title: str
    parent_id: int | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    year: int | None
    season_number: int | None
    episode_number: int | None
    logo_upload_key: str | None
    facts_attempted_at: datetime | None
    credits_attempted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class MergePair:
    """One elected pair: the row that dies and the row that lives."""

    stale: MergeRow
    survivor: MergeRow


@dataclass(frozen=True)
class MergePlan:
    """Everything one merge would do, decided before anything is written.

    Computed once and used by BOTH the dry run and the applied pass, so the
    dry run and the apply never DESCRIBE a pair's merge differently -- the
    failure a separate "count it" query would eventually produce. It does NOT
    freeze the underlying rows: the window between the scan and ``merge``
    includes the whole of ``verify_survivors``' live Plex walk, and a worker
    can write either row of a pair during it. ``merge`` re-locks both rows and
    compares them against the values captured here before writing anything, so
    a pair that drifted is skipped whole rather than partially applied -- see
    the guard in ``merge``'s own docstring.
    """

    pair: MergePair
    renders_repoint: list[str]
    renders_drop: list[str]
    dismissals_repoint: list[str]
    dismissals_drop: list[str]
    children: int
    facts_repoint: bool
    logo_carried: bool


@dataclass(frozen=True)
class MergeScan:
    """What one probe-free scan found.

    ``no_identity_match`` and ``neither_scored`` are reported separately and
    never merged into the headline: the first is the population this job
    cannot reach (an adopted season or episode stores its OWN external ids
    while ``resolve()`` reports the show's, so those rows have no twin this
    predicate can see), and the second is a pair where neither row was ever
    scored -- a different defect, which this job must not claim credit for.

    ``fossils`` is a third such bucket and the only one that NAMES its rows.
    A row whose ``kind`` and ``library`` disagree about the Plex namespace is
    refused by every component and can never score, so the only remedy is a
    hand repair, and an operator cannot start one from a count. It is held
    out of the pairing entirely (``find_mergeable``), so this job never
    merges or deletes one.
    """

    plans: list[MergePlan]
    ambiguous: int
    unelectable: int
    no_identity_match: int
    neither_scored: int
    fossils: list[MergeRow]
    total: int


@dataclass(frozen=True)
class ProbeResult:
    """The applied pass's live verification of each elected pair."""

    accepted: list[MergePlan]
    both_live: int
    neither_live: int
    election_disagreed: int


@dataclass(frozen=True)
class MergeOutcome:
    """What one applied pass actually did.

    ``merged`` carries ``(stale key, survivor key)`` per completed merge, not
    the pairs offered: the job disposal downstream keys off what happened, or
    it would dismiss the queued work of a pair that was skipped.
    """

    merged: list[tuple[str, str]]
    renders_repointed: int
    renders_dropped: int
    dismissals_repointed: int
    dismissals_dropped: int
    children_repointed: int
    parents_carried: int
    facts_repointed: int
    # Roadmap row 99. Counted separately from facts because they are a
    # different KIND of thing: ``item_facts`` is what a provider said and
    # there is at most one row of it, while these are what an operator
    # declared and there are as many as they typed. An operator reading a
    # merge summary needs to know their own declarations were carried, not
    # just that "some children moved".
    overrides_repointed: int
    overrides_dropped: int
    logos_carried: int
    skipped: int


def intent_for_row(row: MergeRow) -> RenderIntent:
    """The intent the pipeline would build for this row.

    Field for field what ``prune.intent_for`` builds, the stored Plex native
    id included -- the probe has to ask exactly what the pipeline asks, or
    this job would decide a row's fate on a question the pipeline never poses.
    """
    return RenderIntent(
        kind=row.kind,
        title=row.title,
        tmdb_id=row.tmdb_id,
        tvdb_id=row.tvdb_id,
        imdb_id=row.imdb_id,
        year=row.year,
        season_number=row.season_number,
        episode_number=row.episode_number,
        refs={"plex": row.native_id} if row.native_id else {},
    )


def _family(kind: str) -> str:
    """The Plex section type a row's ``kind`` implies.

    ``_search_sync``'s own split, verbatim (``plex/client.py:467``, and
    ``_key_resolves_sync`` at ``:661``): a movie kind can only be answered by
    a movie section; show, season and episode all resolve through a show one.
    """
    return "movie" if kind == "movie" else "show"


def _fossil_rows(rows: list[MergeRow]) -> list[MergeRow]:
    """Rows whose ``kind`` and ``library`` disagree about the Plex namespace.

    Before ``1e32efc`` (2026-08-24) the GUID fallback walked every section
    with no type filter and no post-match type check, so a show-kind intent
    carrying a TMDB integer that collides across the movie and TV namespaces
    could be answered by the Movies library. ``resolve()`` stamps ``kind``
    from the INTENT (``plex/client.py:727``) and takes ``library``,
    ``title``, ``root_folder`` and the ids from whatever Plex handed back, so
    the row written contradicts itself. Every guard refuses such a row today,
    which is exactly why it is stuck: it can never score, and while it was
    still being processed ``is_movie = kind == "movie"`` selected the wrong
    provider namespace -- which is how another title's artwork was uploaded
    onto a real Plex item and another title's year and studio written over
    its metadata. The minting path is closed. This makes the survivors
    visible so one can never hide again.

    **The section type is not available here, and that is not an oversight.**
    The only reader of a section's type is ``PlexClient._sections``
    (``plex/client.py:316-328``): private, returning plexapi objects that must
    not cross the thread boundary, and in a module this change does not touch.
    Calling it would also put a Plex request inside a scan that is probe-free
    on purpose -- the dry run is the one report an operator can always read
    during an outage (``find_mergeable``'s docstring), and this bucket has to
    appear in it. Config cannot answer either: ``PlexConfig`` carries
    ``excluded_libraries`` and no library-to-type map, and
    ``Config._playlist_libraries_must_be_configured`` says so in as many words
    -- "this document holds library NAMES and nothing that says what type a
    name is" (``config/schema.py``).

    So the rule is read off the DATA, never guessed from a library's title. A
    Plex section has exactly one type, so every row genuinely resolved out of
    one ``library`` carries one family; where both families appear under one
    library name, the rows in the STRICT MINORITY are the fossils. Three limits,
    all stated in ``deploy/README.md`` beside the SQL form of this function:
    an exact tie names nobody, because with no section type to appeal to there
    is no honest way to say which side is wrong; a library whose rows are ALL
    fossils is invisible by construction; and a library where fossils OUTNUMBER
    the real rows names the real rows instead. The bucket is report-only —
    never a merge input, never a delete — so a misnaming costs the operator a
    look, not a row; the operator's own item view settles which side is wrong.

    Rows arrive in ``id`` order and the result preserves it, so the summary
    string is deterministic and reads no clock.
    """
    tally: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = tally.setdefault(row.library, {"movie": 0, "show": 0})
        counts[_family(row.kind)] += 1

    fossils = []
    for row in rows:
        family = _family(row.kind)
        other = "show" if family == "movie" else "movie"
        if tally[row.library][other] > tally[row.library][family]:
            fossils.append(row)
    return fossils


def _identity_tokens(row: MergeRow) -> list[tuple[str, object]]:
    """The external ids this row can be recognised by. Empty means unmatchable
    -- a row with no external id is never paired on a title."""
    tokens: list[tuple[str, object]] = []
    if row.tmdb_id is not None:
        tokens.append(("tmdb", row.tmdb_id))
    if row.tvdb_id is not None:
        tokens.append(("tvdb", row.tvdb_id))
    if row.imdb_id is not None:
        tokens.append(("imdb", row.imdb_id))
    return tokens


def _identity_clusters(rows: list[MergeRow]) -> list[list[MergeRow]]:
    """Every set of two or more rows sharing one identity.

    Union-find rather than the operator's pairwise self-join, because a
    self-join answers about PAIRS and the decision here is about CLUSTERS:
    three rows for one item is not three independent merges, it is one
    ambiguity, and a pairwise walk would collapse them in an order nobody
    chose. The sizing query in ``deploy/README.md`` stays the independent
    cross-check, not the implementation.

    Rows arrive in ``id`` order and every group preserves it, so the pass is
    deterministic and reads no clock.
    """
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(one: int, other: int) -> None:
        root_one, root_other = find(one), find(other)
        if root_one != root_other:
            parent[max(root_one, root_other)] = min(root_one, root_other)

    seen: dict[tuple, int] = {}
    for index, row in enumerate(rows):
        # The scope IS part of the key: same kind, same library, same
        # season/episode coordinates. Without the coordinates every season of
        # one show shares the show's ids and the whole series reads as one
        # cluster; without the library, a 4K copy and an HD copy of one film
        # read as twins and merging them is data loss.
        scope = (row.kind, row.library, row.season_number, row.episode_number)
        for token in _identity_tokens(row):
            key = (scope, token)
            other = seen.get(key)
            if other is None:
                seen[key] = index
            else:
                union(other, index)

    grouped: dict[int, list[MergeRow]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(find(index), []).append(row)
    return [members for members in grouped.values() if len(members) > 1]


async def _all_rows(session: AsyncSession) -> list[MergeRow]:
    rows = (
        await session.execute(
            select(
                MediaItem.id,
                MediaItem.kind,
                MediaItem.library,
                MediaItem.title,
                MediaItem.parent_id,
                MediaItem.tmdb_id,
                MediaItem.tvdb_id,
                MediaItem.imdb_id,
                MediaItem.year,
                MediaItem.season_number,
                MediaItem.episode_number,
                MediaItem.logo_upload_key,
                MediaItem.facts_attempted_at,
                MediaItem.credits_attempted_at,
                MediaItem.updated_at,
            ).order_by(MediaItem.id)
        )
    ).all()
    # One query for the whole table's Plex ids, not one per row -- the same
    # discipline find_prunable's own batch read follows.
    plex_ids = await native_ids_for(session, [row.id for row in rows], "plex")
    return [MergeRow(native_id=plex_ids.get(row.id), **row._mapping) for row in rows]


async def _plan_merges(
    session: AsyncSession, pairs: list[MergePair]
) -> list[MergePlan]:
    """Decide every pair's work in four queries, not four per pair."""
    if not pairs:
        return []
    ids = [row.id for pair in pairs for row in (pair.stale, pair.survivor)]
    stale_ids = [pair.stale.id for pair in pairs]

    render_kinds: dict[int, set[str]] = {}
    for item_id, art_kind in (
        await session.execute(
            select(Render.item_id, Render.art_kind).where(Render.item_id.in_(ids))
        )
    ).all():
        render_kinds.setdefault(item_id, set()).add(art_kind)

    dismissal_kinds: dict[int, set[str]] = {}
    for item_id, art_kind in (
        await session.execute(
            select(ActionDismissal.item_id, ActionDismissal.art_kind)
            .where(ActionDismissal.item_id.in_(ids))
        )
    ).all():
        dismissal_kinds.setdefault(item_id, set()).add(art_kind)

    facts_ids = set(
        (
            await session.execute(
                select(ItemFacts.item_id).where(ItemFacts.item_id.in_(ids))
            )
        ).scalars()
    )

    children = dict(
        (
            await session.execute(
                select(MediaItem.parent_id, func.count())
                .where(MediaItem.parent_id.in_(stale_ids))
                .group_by(MediaItem.parent_id)
            )
        ).all()
    )

    plans = []
    for pair in pairs:
        stale_renders = render_kinds.get(pair.stale.id, set())
        survivor_renders = render_kinds.get(pair.survivor.id, set())
        stale_dismissals = dismissal_kinds.get(pair.stale.id, set())
        survivor_dismissals = dismissal_kinds.get(pair.survivor.id, set())
        plans.append(MergePlan(
            pair=pair,
            # Sorted so the plan, the summary and the audit row all name the
            # kinds in one order -- a set's iteration order is not one.
            renders_repoint=sorted(stale_renders - survivor_renders),
            renders_drop=sorted(stale_renders & survivor_renders),
            dismissals_repoint=sorted(stale_dismissals - survivor_dismissals),
            dismissals_drop=sorted(stale_dismissals & survivor_dismissals),
            children=children.get(pair.stale.id, 0),
            facts_repoint=(
                pair.stale.id in facts_ids and pair.survivor.id not in facts_ids
            ),
            logo_carried=(
                pair.stale.logo_upload_key is not None
                and pair.survivor.logo_upload_key is None
            ),
        ))
    return plans


async def find_mergeable(session: AsyncSession) -> MergeScan:
    """Every twin pair, elected and planned, with no Plex probe at all.

    Probe-free on purpose (A5): the operator can read this report during an
    outage, size the population, and only then decide. The election is the
    row with the later ``updated_at`` (ties broken by the higher ``id``) --
    since Task 6's identity-keyed upsert, two rows sharing one identity can
    no longer be minted going forward (``identity_key`` is unique), so this
    election only ever meets a pair a migration or a restore left behind, and
    the APPLIED pass verifies it against Plex before it deletes anything,
    which is where the heuristic earns its keep or is refused.
    """
    rows = await _all_rows(session)
    if not rows:
        return MergeScan([], 0, 0, 0, 0, [], 0)

    # Held out of the pairing rather than left to fall outside every cluster
    # scope by luck. Two fossils of one item in one library share a
    # ``(kind, library, season, episode)`` scope AND their ids, so the
    # union-find WOULD pair them -- and merging two self-contradicting rows
    # destroys the evidence the hand repair needs. Nothing below this line
    # can reach them: not the election, not the probe, not the delete.
    fossils = _fossil_rows(rows)
    fossil_ids = {row.id for row in fossils}
    pairable = [row for row in rows if row.id not in fossil_ids]

    pairs: list[MergePair] = []
    ambiguous = 0
    # No row can make a pair unelectable any more: the election orders rows
    # by (updated_at, id), which every row has, rather than by parsing a
    # server-specific key as an integer. Kept in MergeScan's shape (and in
    # every summary line) rather than removed, so a future election rule that
    # CAN refuse a pair has a bucket to report into without another shape
    # change.
    unelectable = 0
    clustered_ids: set[int] = set()
    for cluster in _identity_clusters(pairable):
        clustered_ids.update(row.id for row in cluster)
        if len(cluster) != 2:
            ambiguous += 1
            continue
        survivor, stale = sorted(cluster, key=lambda row: (row.updated_at, row.id), reverse=True)
        pairs.append(MergePair(stale=stale, survivor=survivor))

    plans = await _plan_merges(session, pairs)

    unscored = select(Render.item_id).where(
        Render.status == "rendered", Render.quality_scored_at.is_(None)
    )
    # Fossils are excluded here too, so every row lands in exactly ONE
    # bucket. A fossil has no identity twin either -- counting it in both
    # would send an operator chasing one row through two different remedies.
    excluded = clustered_ids | fossil_ids
    if excluded:
        unscored = unscored.where(Render.item_id.notin_(excluded))
    no_identity_match = len(set((await session.execute(unscored)).scalars()))

    paired_ids = [row.id for pair in pairs for row in (pair.stale, pair.survivor)]
    scored_ids: set[int] = set()
    if paired_ids:
        scored_ids = set(
            (
                await session.execute(
                    select(Render.item_id)
                    .where(Render.item_id.in_(paired_ids))
                    .where(Render.quality_scored_at.isnot(None))
                )
            ).scalars()
        )
    neither_scored = sum(
        1
        for pair in pairs
        if pair.stale.id not in scored_ids and pair.survivor.id not in scored_ids
    )

    return MergeScan(
        plans=plans,
        ambiguous=ambiguous,
        unelectable=unelectable,
        no_identity_match=no_identity_match,
        neither_scored=neither_scored,
        fossils=fossils,
        total=len(rows),
    )


async def verify_survivors(plex, plans: list[MergePlan]) -> ProbeResult:
    """Keep only the pairs Plex agrees about, in one walk.

    ``keys_resolve`` and never ``exists_many``: the latter falls through to
    the GUID walk, under which a re-keyed item resolves from BOTH rows and the
    probe would say nothing at all. Three refusals, counted apart because they
    mean three different things to an operator:

    * **both keys live** -- two real Plex items carrying one identity, i.e. a
      duplicate in the library. An operator's decision, not this job's.
    * **neither key live** -- ``plex_prune``'s population. Deleting here would
      act on a probe that says nothing about which row is real.
    * **the election disagreed** -- the newest key is the one Plex refuses, so
      the dry run's preview was wrong about this pair and nothing is done to
      it. It is reported so the heuristic can be judged on evidence.
    """
    if not plans:
        return ProbeResult([], 0, 0, 0)
    intents = [intent_for_row(plan.pair.survivor) for plan in plans]
    intents += [intent_for_row(plan.pair.stale) for plan in plans]
    flags = await plex.keys_resolve(intents)
    survivor_flags = flags[: len(plans)]
    stale_flags = flags[len(plans):]

    accepted: list[MergePlan] = []
    both = neither = disagreed = 0
    for plan, survivor_live, stale_live in zip(
        plans, survivor_flags, stale_flags, strict=True
    ):
        if survivor_live and stale_live:
            both += 1
        elif not survivor_live and not stale_live:
            neither += 1
        elif stale_live:
            disagreed += 1
        else:
            accepted.append(plan)
    return ProbeResult(
        accepted=accepted,
        both_live=both,
        neither_live=neither,
        election_disagreed=disagreed,
    )


def _later(one: datetime | None, other: datetime | None) -> datetime | None:
    """The later of two "we looked" stamps, ignoring NULLs.

    Taking the survivor's alone would make it read as unvisited whenever the
    stale row was the one that had been swept, and it would be re-swept for
    nothing.
    """
    if one is None:
        return other
    if other is None:
        return one
    return max(one, other)


async def merge(session: AsyncSession, plans: list[MergePlan]) -> MergeOutcome:
    """Apply each plan, one committed transaction per pair.

    **One transaction per pair is what makes this resumable.** A failure part
    way through leaves every completed merge committed and audited and every
    remaining pair untouched, so the next pass simply continues. That is the
    thing a migration could not offer.

    **Both rows are locked, and the change test runs under that lock BEFORE
    anything is written.** ``prune.retire`` can put its ``(id, updated_at)``
    guard on the delete itself, because the delete is the only write it makes.
    Here the delete is the LAST of seven writes, and a guard that fired at the
    end would leave a half-moved graph. So the pair is taken ``FOR UPDATE``,
    and BOTH rows' ``updated_at`` -- not just the stale row's -- are compared
    to the values the scan read; a pair where EITHER row changed is skipped
    with no write at all. The survivor side is not redundant: the window this
    guard closes is the whole of ``verify_survivors``'s live Plex walk
    (minutes, not milliseconds, at the scale ``max_merges`` lets through --
    that cap REFUSES an over-cap pass outright, it does not batch a large one
    into runs of 500), and a worker
    that lands on the survivor's live key during it re-upserts ``media_items``
    -- which always runs before that same worker can go on to write a
    ``renders`` row -- so checking the survivor's stamp here catches the
    upsert before it happens, rather than letting a later repoint collide with
    a render that worker just inserted (``uq_render_item_kind``) as an
    uncaught ``IntegrityError`` past ``dismiss_jobs_for``. Under the lock a
    concurrent ``_upsert_media_item`` waits, then finds the row gone and
    inserts a fresh one -- a new twin, which the next pass merges. The guard
    stays on the delete as well, because a guard that can only be redundant is
    cheaper than a guard that can only be missing.

    **The locked read selects COLUMNS, not ORM rows, and that is not a style
    choice.** ``_upsert_media_item``'s ``ON CONFLICT`` arm stamps
    ``updated_at`` with a server-side ``now()``, which SQLAlchemy does not
    write back into an instance already in the identity map; a
    ``select(MediaItem)`` would hand back that instance with its stale
    in-memory timestamp and the guard would compare the scan's value against
    itself and always match. A column select reads the real row. It is the
    same reason ``prune.retire`` compares ``updated_at`` inside the DELETE's
    own WHERE clause rather than in Python.

    **The survivor's OWN parent link is carried too, decided off the same
    locked read.** A season or episode minted by the fork resolved its parent
    by the LIVE rating key while the show's row still held the STALE one, so
    ``_upsert_media_item``'s parent lookup missed and the survivor was
    inserted with ``parent_id = NULL``. The stale row holds the correct link;
    losing it orphans the survivor from its show until a later pass happens to
    re-resolve the parent's own key. Carried onto the survivor exactly like
    ``logo_upload_key``, and only when the survivor's is NULL -- a parent it
    has acquired since the scan must not be overwritten.

    **Children are repointed BEFORE the delete.** ``media_items.parent_id`` is
    a self-FK with ON DELETE CASCADE: a delete that ran first would take every
    season and episode under a stale show, silently, with no audit row and
    absent from every count. Nothing in this function may be reordered past
    that line.

    A repointed dismissal will usually re-surface on the queue, because
    ``action_dismissals.evidence`` hashes the render facts the queue judges
    and the survivor is scored where the stale row was not. That is the
    dismissal contract -- it holds only while the facts hold -- and the
    summary says so rather than hiding it.

    A per-item metadata override (roadmap row 99) is carried the same way and
    for the same reason: it is a child of ``media_items`` and the delete below
    cascades. When both twins declare the same field the survivor's value is
    kept -- it is the row every write since the fork has landed on -- and the
    stale one is dropped with an INFO naming the items and the field, never
    the value.
    """
    merged: list[tuple[str, str]] = []
    renders_repointed = renders_dropped = 0
    dismissals_repointed = dismissals_dropped = 0
    children_repointed = parents_carried = facts_repointed = logos_carried = 0
    overrides_repointed = overrides_dropped = 0
    skipped = 0

    for plan in plans:
        pair = plan.pair
        locked = {
            row.id: row
            for row in (
                await session.execute(
                    select(
                        MediaItem.id,
                        MediaItem.updated_at,
                        MediaItem.parent_id,
                        MediaItem.logo_upload_key,
                        MediaItem.facts_attempted_at,
                        MediaItem.credits_attempted_at,
                    )
                    .where(MediaItem.id.in_((pair.stale.id, pair.survivor.id)))
                    .order_by(MediaItem.id)
                    .with_for_update()
                )
            ).all()
        }
        stale = locked.get(pair.stale.id)
        survivor = locked.get(pair.survivor.id)
        if (
            stale is None
            or survivor is None
            or stale.updated_at != pair.stale.updated_at
            or survivor.updated_at != pair.survivor.updated_at
        ):
            logger.info(
                "merge: %s -> %s changed under the pass; left alone",
                pair.stale.native_id, pair.survivor.native_id,
            )
            await session.rollback()
            skipped += 1
            continue

        if plan.renders_drop:
            await session.execute(
                delete(Render)
                .where(Render.item_id == stale.id)
                .where(Render.art_kind.in_(plan.renders_drop))
                .execution_options(synchronize_session=False)
            )
        if plan.renders_repoint:
            await session.execute(
                update(Render)
                .where(Render.item_id == stale.id)
                .where(Render.art_kind.in_(plan.renders_repoint))
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        # Re-read dismissal art_kinds AFTER the lock rather than trusting the
        # plan's scan-time sets: api/action_center.py inserts a dismissal
        # without writing media_items, so the (id, updated_at) guard above
        # cannot see one that arrived in the scan-to-lock window. Deciding
        # repoint-vs-drop off this read means a dismissal that landed in that
        # window is handled like one that existed at scan time, instead of
        # being silently cascade-deleted with the stale row.
        dismissal_kinds: dict[int, set[str]] = {stale.id: set(), survivor.id: set()}
        for item_id, art_kind in (
            await session.execute(
                select(ActionDismissal.item_id, ActionDismissal.art_kind)
                .where(ActionDismissal.item_id.in_((stale.id, survivor.id)))
            )
        ).all():
            dismissal_kinds[item_id].add(art_kind)
        dismissals_drop = sorted(
            dismissal_kinds[stale.id] & dismissal_kinds[survivor.id]
        )
        dismissals_repoint = sorted(
            dismissal_kinds[stale.id] - dismissal_kinds[survivor.id]
        )
        if dismissals_drop:
            await session.execute(
                delete(ActionDismissal)
                .where(ActionDismissal.item_id == stale.id)
                .where(ActionDismissal.art_kind.in_(dismissals_drop))
                .execution_options(synchronize_session=False)
            )
        if dismissals_repoint:
            await session.execute(
                update(ActionDismissal)
                .where(ActionDismissal.item_id == stale.id)
                .where(ActionDismissal.art_kind.in_(dismissals_repoint))
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        if plan.facts_repoint:
            await session.execute(
                update(ItemFacts)
                .where(ItemFacts.item_id == stale.id)
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        else:
            # UNIQUE(item_id) forbids two rows on the survivor, and the
            # survivor's is the fresher of the two by construction -- it is
            # the row every sweep since the fork has been writing.
            await session.execute(
                delete(ItemFacts)
                .where(ItemFacts.item_id == stale.id)
                .execution_options(synchronize_session=False)
            )
        # Credits are a composite-PK many-to-many, so the collision is per
        # (kind, person) rather than per row: drop the ones the survivor
        # already holds, then repoint the rest.
        await session.execute(
            delete(ItemCredit)
            .where(ItemCredit.item_id == stale.id)
            .where(
                tuple_(ItemCredit.kind, ItemCredit.person).in_(
                    select(ItemCredit.kind, ItemCredit.person)
                    .where(ItemCredit.item_id == survivor.id)
                )
            )
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(ItemCredit)
            .where(ItemCredit.item_id == stale.id)
            .values(item_id=survivor.id)
            .execution_options(synchronize_session=False)
        )

        # Roadmap row 99's C2 carry rule. Read AFTER the lock rather than off
        # the plan, for the reason the dismissal block above states: the PUT
        # endpoint writes an override row without touching ``media_items``,
        # so the (id, updated_at) guard cannot see one that arrived in the
        # scan-to-lock window, and a scan-time set would leave it for the
        # CASCADE delete below to take silently.
        #
        # Conflict rule: when BOTH twins carry the same field the SURVIVOR's
        # row wins and the stale one is dropped -- UNIQUE(item_id, field)
        # forbids two rows on the survivor, and the survivor is the row every
        # write since the fork has been landing on. Logged at INFO with the
        # item ids and the field name and NOTHING ELSE: an override's value is
        # operator-typed free text (roadmap row 213).
        override_fields: dict[int, set[str]] = {stale.id: set(), survivor.id: set()}
        for item_id, field in (
            await session.execute(
                select(ItemMetadataOverride.item_id, ItemMetadataOverride.field)
                .where(ItemMetadataOverride.item_id.in_((stale.id, survivor.id)))
            )
        ).all():
            override_fields[item_id].add(field)
        overrides_drop = sorted(
            override_fields[stale.id] & override_fields[survivor.id]
        )
        overrides_repoint = sorted(
            override_fields[stale.id] - override_fields[survivor.id]
        )
        for field in overrides_drop:
            logger.info(
                "merge: items %s and %s both override %s; the survivor's "
                "value is kept and the stale row's is dropped",
                stale.id, survivor.id, field,
            )
        if overrides_drop:
            await session.execute(
                delete(ItemMetadataOverride)
                .where(ItemMetadataOverride.item_id == stale.id)
                .where(ItemMetadataOverride.field.in_(overrides_drop))
                .execution_options(synchronize_session=False)
            )
        if overrides_repoint:
            await session.execute(
                update(ItemMetadataOverride)
                .where(ItemMetadataOverride.item_id == stale.id)
                .where(ItemMetadataOverride.field.in_(overrides_repoint))
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )

        # Decided off the LOCKED values, not off the plan's: the plan is the
        # dry run's preview, read before the lock, and a logo marker the
        # survivor has acquired since then must not be overwritten. The plan's
        # own flag stays what the report was built from.
        carry_logo = (
            stale.logo_upload_key is not None and survivor.logo_upload_key is None
        )
        carried_logo = stale.logo_upload_key if carry_logo else None
        # Same reasoning, for the survivor's OWN parent link. A season or
        # episode minted by the fork resolved its parent by the LIVE rating
        # key while the show's row still held the STALE one
        # (``render/pipeline.py``'s ``_upsert_media_item`` looks
        # ``parent_rating_key`` up against the CURRENT ``rating_key``), so the
        # lookup missed and the survivor was inserted with ``parent_id =
        # NULL``. The stale row holds the correct link; without this it dies
        # with the row and the survivor is orphaned from its show until a
        # later pass happens to re-resolve the parent's own key.
        carry_parent = stale.parent_id is not None and survivor.parent_id is None
        carried_parent = stale.parent_id if carry_parent else None
        survivor_values = {
            "facts_attempted_at": _later(
                survivor.facts_attempted_at, stale.facts_attempted_at
            ),
            "credits_attempted_at": _later(
                survivor.credits_attempted_at, stale.credits_attempted_at
            ),
        }
        if carry_logo:
            survivor_values["logo_upload_key"] = carried_logo
        if carry_parent:
            survivor_values["parent_id"] = carried_parent
        await session.execute(
            update(MediaItem)
            .where(MediaItem.id == survivor.id)
            .values(**survivor_values)
            .execution_options(synchronize_session=False)
        )

        # THE CASCADE LAW. This runs before the delete below, always -- and
        # unconditionally, not gated on plan.children: that count is a
        # scan-time COUNT(*), taken minutes before this lock, and a child
        # inserted under the stale row in the scan-to-lock window writes no
        # media_items row of its own, so the (id, updated_at) guard above
        # cannot see it. Running the UPDATE unconditionally and counting its
        # rowcount repoints that child too, instead of leaving it for the
        # CASCADE delete below to take silently.
        children_result = await session.execute(
            update(MediaItem)
            .where(MediaItem.parent_id == stale.id)
            .values(parent_id=survivor.id)
            .execution_options(synchronize_session=False)
        )
        children_repointed_now = children_result.rowcount

        # Read before the delete below: the FK from media_item_server_refs
        # cascades on that same statement, so a refs_for read taken after it
        # would find nothing left to report for the stale row.
        stale_refs = await refs_for(session, stale.id)
        survivor_refs = await refs_for(session, survivor.id)

        removed = (
            await session.execute(
                delete(MediaItem)
                .where(MediaItem.id == stale.id)
                .where(MediaItem.updated_at == pair.stale.updated_at)
                .returning(MediaItem.id)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if removed is None:
            # Unreachable while the lock above holds, and kept anyway: this is
            # the one write whose failure would be irreversible in the other
            # direction, and a redundant guard costs a comparison.
            logger.warning(
                "merge: %s changed between the lock and the delete; rolled back",
                pair.stale.native_id,
            )
            await session.rollback()
            skipped += 1
            continue

        session.add(EventLog(
            source=MERGE_SOURCE,
            event_type=MERGE_EVENT,
            payload={
                "stale_media_item_id": pair.stale.id,
                # Legacy key names, kept for any reader still watching for
                # them (spec §4.5): the value is now the Plex native id.
                "stale_rating_key": pair.stale.native_id,
                "stale_refs": stale_refs,
                "survivor_media_item_id": pair.survivor.id,
                "survivor_rating_key": pair.survivor.native_id,
                "survivor_refs": survivor_refs,
                "kind": pair.stale.kind,
                "library": pair.stale.library,
                "title": pair.stale.title,
                "season_number": pair.stale.season_number,
                "episode_number": pair.stale.episode_number,
                "tmdb_id": pair.stale.tmdb_id,
                "tvdb_id": pair.stale.tvdb_id,
                "imdb_id": pair.stale.imdb_id,
                "renders_repointed": plan.renders_repoint,
                "renders_dropped": plan.renders_drop,
                "dismissals_repointed": dismissals_repoint,
                "dismissals_dropped": dismissals_drop,
                "children_repointed": children_repointed_now,
                "parent_id_carried": carried_parent,
                "facts_repointed": plan.facts_repoint,
                "overrides_repointed": overrides_repoint,
                "overrides_dropped": overrides_drop,
                "logo_upload_key_carried": carried_logo,
                # Recorded unconditionally, prune.retire's precedent: when
                # BOTH rows already hold a marker, carry_logo is False and the
                # stale one dies with the row -- an operator must still be
                # able to find the value that was lost, not just learn that
                # something was.
                "stale_logo_upload_key": stale.logo_upload_key,
            },
            outcome=(
                f"merged {pair.stale.native_id} into {pair.survivor.native_id} "
                "on an identity match"
            ),
        ))
        await session.commit()

        merged.append((pair.stale.native_id, pair.survivor.native_id))
        renders_repointed += len(plan.renders_repoint)
        renders_dropped += len(plan.renders_drop)
        dismissals_repointed += len(dismissals_repoint)
        dismissals_dropped += len(dismissals_drop)
        children_repointed += children_repointed_now
        parents_carried += int(carry_parent)
        facts_repointed += int(plan.facts_repoint)
        overrides_repointed += len(overrides_repoint)
        overrides_dropped += len(overrides_drop)
        logos_carried += int(carry_logo)

    return MergeOutcome(
        merged=merged,
        renders_repointed=renders_repointed,
        renders_dropped=renders_dropped,
        dismissals_repointed=dismissals_repointed,
        dismissals_dropped=dismissals_dropped,
        children_repointed=children_repointed,
        parents_carried=parents_carried,
        facts_repointed=facts_repointed,
        overrides_repointed=overrides_repointed,
        overrides_dropped=overrides_dropped,
        logos_carried=logos_carried,
        skipped=skipped,
    )


def implausible_merge_count(pairs: int, total: int, config) -> str | None:
    """Return a refusal summary if this many twin pairs cannot be believed.

    ``implausible_prune_count``'s shape with this sweep's causes. The failure
    it catches is not a server that answered wrongly -- this scan asks no
    server -- but an identity predicate that has become too generous: two
    libraries renamed into one name, an import that duplicated external ids, a
    restore that doubled the table. Every merge ends in a delete, so past
    either cap the pass reports the numbers instead of acting on them.
    """
    if pairs and pairs > config.max_merges:
        return (
            f"refused: {pairs} pair(s) ({2 * pairs} of {total} media_items "
            f"row(s)) look like twins, more than the safety cap of "
            f"{config.max_merges}; this usually means two libraries now "
            "share a name, an import duplicated external ids, or a restore "
            "doubled the table -- change nothing"
        )
    # A pair occupies TWO rows, so the share this compares against
    # config/autoposter.example.yaml's "share of the library" is rows, not
    # pairs -- pairs / total would need roughly double the real duplication
    # before the 0.25 default ever fired.
    share = (2 * pairs) / total if total else 0.0
    if total >= SHARE_CHECK_MIN_ITEMS and share > config.max_merge_share:
        return (
            f"refused: {pairs} pair(s) ({2 * pairs} of {total} media_items "
            f"row(s), {share:.0%}) look like twins, more than the safety cap "
            f"of {config.max_merge_share:.0%}; this usually means two "
            "libraries now share a name, an import duplicated external ids, "
            "or a restore doubled the table -- change nothing"
        )
    return None


# How many fossil rows one summary names before it starts counting instead.
# The whole string is stored in scheduled_runs.last_detail (Text) and rendered
# on the dashboard; listing a pathological population row by row would make
# that page unreadable, and the count is what sends an operator to the query
# in deploy/README.md.
_FOSSIL_NAMES_MAX = 20


def _tail(scan: MergeScan) -> str:
    """The sentences both summaries end with: what this pass did NOT do."""
    parts = []
    if scan.no_identity_match:
        parts.append(
            f"{scan.no_identity_match} unscored row(s) have no identity twin "
            "at all and are untouched by this job"
        )
    if scan.neither_scored:
        parts.append(
            f"{scan.neither_scored} pair(s) have no scored render on either "
            "row, which is a different problem"
        )
    if scan.ambiguous or scan.unelectable:
        parts.append(
            f"{scan.ambiguous} cluster(s) ambiguous and {scan.unelectable} "
            "unelectable, left alone -- neither resolves itself; an operator "
            "must fix the identity in Plex or dismiss the row, or this job "
            "reports them again next pass"
        )
    if scan.fossils:
        named = " | ".join(
            f"id={row.id}, key={row.native_id}, title={row.title!r}"
            for row in scan.fossils[:_FOSSIL_NAMES_MAX]
        )
        remainder = len(scan.fossils) - _FOSSIL_NAMES_MAX
        # Rows are joined with " | " and never "; ": the operator parses this
        # summary by its semicolons, and a list separator that matched them
        # would split one sentence into many.
        parts.append(
            f"{len(scan.fossils)} row(s) whose kind and library disagree "
            "(fossils), never merged or deleted by this job and repairable "
            f"only by hand -- see deploy/README.md: {named}"
            + (f" | and {remainder} more" if remainder > 0 else "")
        )
    return ("; " + "; ".join(parts)) if parts else ""


def make_merge_job(
    holder: ConfigHolder,
    plex_factory: Callable[[], object],
    is_healthy: Callable[[], bool],
) -> Job:
    """Build the scheduled twin-merge job.

    The ``make_prune_job`` shape: scheduled, dry run by default, one summary
    string per run, hand-triggerable once ``"plex_merge"`` is in
    ``SCHEDULED_JOB_NAMES``. Not a Plex-writing mode -- this writes only to the
    database, so the mode fence buys it nothing.

    One deliberate departure from the prune's ordering: the unhealthy-Plex
    refusal is checked **after** the scan and only on the applied path. The
    prune checks it first because a dead server makes every row read as gone;
    this scan asks no server at all, so refusing a read-only report during an
    outage would deny the operator the one report they can always have. The
    apply still refuses, because a survivor that cannot be verified must not
    have its twin deleted.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current

        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return empty

        scan = await find_mergeable(session)
        refusal = implausible_merge_count(len(scan.plans), scan.total, config.merge)
        if refusal is not None:
            return refusal

        if not config.merge.apply:
            repoint = sum(len(plan.renders_repoint) for plan in scan.plans)
            drop = sum(len(plan.renders_drop) for plan in scan.plans)
            moved = sum(len(plan.dismissals_repoint) for plan in scan.plans)
            dropped = sum(len(plan.dismissals_drop) for plan in scan.plans)
            children = sum(plan.children for plan in scan.plans)
            facts = sum(plan.facts_repoint for plan in scan.plans)
            logos = sum(plan.logo_carried for plan in scan.plans)
            parents = sum(
                plan.pair.stale.parent_id is not None
                and plan.pair.survivor.parent_id is None
                for plan in scan.plans
            )
            return (
                f"dry run: {len(scan.plans)} of {scan.total} media_items row(s) "
                f"would be merged into their twin; {repoint} render(s) would "
                f"repoint and {drop} would be dropped; {moved} dismissal(s) "
                f"would move and {dropped} would be dropped; {children} child "
                f"row(s) would repoint before the delete; {facts} item_facts "
                f"row(s) would repoint and {logos} logo marker(s) would carry; "
                f"{parents} survivor parent link(s) would carry"
                + _tail(scan)
            )

        if not is_healthy():
            return (
                "refused: Plex is unhealthy, so no surviving row can be "
                "verified before its twin is deleted; change nothing"
            )

        # The scan above issued six SELECTs (including a full media_items
        # read) and left the session idle-in-transaction otherwise, across
        # the whole of the probe walk below -- minutes, not milliseconds, at
        # this branch's own scale, pinning a pooled connection and the vacuum
        # horizon for nothing: MergePlan is frozen dataclasses and merge()
        # re-locks and re-reads both rows anyway.
        await session.rollback()

        if not scan.plans:
            # No plans, no probe -- and no reason to connect. After the first
            # applied pass this is every week's steady state, so building the
            # client here would mean a connect (and a possible refusal) on a
            # pass with nothing to do.
            probe = ProbeResult([], 0, 0, 0)
        else:
            try:
                # The factory is inside the try because it connects: a refused
                # connection, a rejected token or a plexapi BadRequest all raise
                # here, and every one of those messages carries the server address.
                plex = await asyncio.to_thread(plex_factory)
                probe = await verify_survivors(plex, scan.plans)
            except Exception as exc:
                # The class name only, never str(exc) and never a URL: this string
                # is stored in scheduled_runs.last_detail, which the dashboard
                # renders. The full traceback goes to the log.
                logger.warning("plex_merge: verifying survivors failed", exc_info=True)
                raise MergeRefused(
                    f"refused: verifying the surviving rows failed "
                    f"({type(exc).__name__}), so no row's death can be trusted; "
                    "change nothing"
                ) from None

        outcome = await merge(session, probe.accepted)
        # BOTH keys (A6): a parked payload can name the dead key, because it
        # was queued before the fork, or the survivor's, because the graph it
        # was queued against has just changed under it. Rewriting a claimed
        # job's payload is the one thing this queue never does, so disposal
        # looks up both instead.
        dismissed = await dismiss_jobs_for(
            session, [key for pair in outcome.merged for key in pair]
        )
        await session.commit()

        summary = (
            f"merged {len(outcome.merged)} of {scan.total} media_items row(s) "
            f"into their twin; repointed {outcome.renders_repointed} render(s) "
            f"and dropped {outcome.renders_dropped}; moved "
            f"{outcome.dismissals_repointed} dismissal(s) and dropped "
            f"{outcome.dismissals_dropped}; repointed "
            f"{outcome.children_repointed} child row(s) and "
            f"{outcome.parents_carried} survivor parent link(s); kept "
            f"{outcome.facts_repointed} item_facts row(s) and carried "
            f"{outcome.logos_carried} logo marker(s); repointed "
            f"{outcome.overrides_repointed} override(s) and dropped "
            f"{outcome.overrides_dropped}; dismissed {dismissed} "
            "queued job(s). A moved dismissal usually re-surfaces: its "
            "evidence hash covers whether the row is scored, and the survivor "
            "is."
        )
        if probe.both_live or probe.neither_live or probe.election_disagreed:
            summary += (
                f"; refused {probe.both_live} pair(s) whose rows BOTH resolve "
                f"by key (two real Plex items -- an operator decision), "
                f"{probe.neither_live} whose rows resolve by NEITHER (the "
                f"plex_prune sweep's population), and "
                f"{probe.election_disagreed} where the newest key is the one "
                "Plex no longer accepts -- which repeats every run until an "
                "operator resolves the identity in Plex or dismisses the row"
            )
        if outcome.skipped:
            summary += (
                f"; {outcome.skipped} pair(s) changed under the pass and were "
                "left untouched"
            )
        return summary + _tail(scan)

    return Job(
        name="plex_merge",
        interval_seconds=lambda: holder.current.scheduler.merge_days * 24 * 3600,
        run=run,
    )
