"""``find_prunable`` / ``retire`` / ``make_prune_job``: the media_items prune sweep.

Roadmap row 129. Items that leave Plex -- deleted, moved into a library the
operator excluded, or re-matched under a new rating key -- leave a
``media_items`` row nothing removes. Every full pass re-enqueues that row, the
job cannot resolve it, and it parks; forever. This sweep retires those rows.

The three things worth knowing before changing anything here:

* an unreachable Plex makes EVERY row look gone, so "cannot probe" must read as
  refuse and never as a work order -- the inversion of the cleanup sweep's
  empty-table guard, and the reason several tests here are about NOT deleting;
* ``media_items.parent_id`` cascades, so a show deleted first takes its seasons
  and episodes with it, silently and without audit rows -- hence the
  descendant rules and the deepest-first ordering;
* the delete is keyed on ``(id, updated_at)`` so a row a worker re-upserted
  under the pass survives it.
"""
import asyncio
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import (
    EventLog, ItemFacts, Job, MediaItem, MediaItemServerRef, Render, ScheduledRun,
)
from autoposter.intake.arr import RenderIntent
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.prune import (
    PRUNE_EVENT,
    PRUNE_SOURCE,
    PruneRefused,
    _directory_count,
    dismiss_jobs_for,
    find_prunable,
    implausible_prune_count,
    intent_for,
    make_prune_job,
    retire,
)


class FakePlex:
    """A ``PlexClient`` stand-in answering only the question the pruner asks.

    ``live`` is the set of rating keys that still resolve; ``error``, when set,
    is raised instead, because "the probe failed" and "everything is gone" must
    never be the same answer. ``name`` defaults to ``"plex"`` -- every test in
    this suite predates a second server -- but Task 19's per-server
    ``find_prunable`` reads each server's OWN native id off the intent, so a
    test standing this in for another server (``test_the_audit_rows_refs_
    include_every_server``) passes ``name="jellyfin"`` to match.
    """

    def __init__(self, live=(), *, error=None, name: str = "plex"):
        self._live = set(live)
        self._error = error
        self._name = name
        self.asked = []

    async def exists_many(self, intents):
        if self._error is not None:
            raise self._error
        self.asked.extend(intents)
        return [intent.native_id_on(self._name) in self._live for intent in intents]


async def _add_item(session, rating_key, *, kind="movie", parent=None, **columns):
    """One ``media_items`` row, with its own Plex ref, committed so its
    ``updated_at`` is a settled value from its own transaction (which the
    concurrency guard depends on).

    ``identity_key`` is synthesized from the rating key rather than from any
    external id a test also passes in: ``media_items.identity_key`` is
    UNIQUE (Task 6), and this suite's whole point is rows distinguished only
    by their own Plex id, exactly like ``rating_key`` used to be before it
    moved off this table.
    """
    library = columns.pop("library", "Movies" if kind == "movie" else "TV Shows")
    item = MediaItem(
        identity_key=f"{kind}:legacy:plex:{rating_key}",
        library=library,
        kind=kind,
        title=columns.pop("title", f"Item {rating_key}"),
        parent_id=parent.id if parent is not None else None,
        **columns,
    )
    session.add(item)
    await session.flush()
    session.add(MediaItemServerRef(
        item_id=item.id, server="plex", native_id=rating_key, library=library,
    ))
    await session.commit()
    # Test bookkeeping only -- not a mapped column, so it survives
    # session.expire_all() untouched and needs no extra query to read back.
    item.native_id = rating_key
    return item


def _producer_payload(native_id: str, *, title: str = "Gone", kind: str = "movie") -> dict:
    """A job payload built the way the real producers build it -- through
    ``RenderIntent`` and ``asdict`` -- so ``dismiss_jobs_for``'s jsonb query is
    exercised against the shape production actually writes
    (``payload["refs"]["plex"]``), not a hand-built ``{"rating_key": ...}``
    dict that would stay green even if the ``refs`` half of the query broke.
    """
    return asdict(RenderIntent(kind=kind, title=title, refs={"plex": native_id}))


async def test_a_row_plex_still_resolves_is_never_a_candidate(session):
    await _add_item(session, "10")

    scan = await find_prunable(session, {"plex": FakePlex(live={"10"})})

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (0, 0, 1)


async def test_a_row_that_no_longer_resolves_is_prunable(session):
    await _add_item(session, "10")
    await _add_item(session, "11")

    scan = await find_prunable(session, {"plex": FakePlex(live={"10"})})

    assert [c.native_id for c in scan.prunable] == ["11"]
    assert (scan.gone, scan.held, scan.total) == (1, 0, 2)


async def test_the_probe_is_asked_exactly_what_the_pipeline_asks(session):
    """The intent carries the stored rating key and the row's external ids,
    field for field what ``_enqueue_reprocess`` builds (api/routes.py). Probing
    a different question than the pipeline poses would judge rows on evidence
    the pipeline never sees."""
    await _add_item(
        session, "42", kind="episode", title="An Episode",
        tvdb_id=77, season_number=1, episode_number=3, year=2020,
    )
    plex = FakePlex(live={"42"})

    await find_prunable(session, {"plex": plex})

    (intent,) = plex.asked
    assert intent.native_id_on("plex") == "42"
    assert intent.kind == "episode"
    assert intent.title == "An Episode"
    assert intent.tvdb_id == 77
    assert (intent.season_number, intent.episode_number) == (1, 3)
    assert intent.year == 2020


async def test_a_parent_is_held_when_any_descendant_still_resolves(session):
    """``parent_id`` cascades. Deleting a show whose episode still resolves
    would take a live row with it, silently -- so one surviving descendant
    holds the whole chain above it, and the count is reported, not swallowed."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, {"plex": FakePlex(live={"111"})})

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (2, 2, 3)


async def test_a_family_that_is_entirely_gone_is_prunable_deepest_first(session):
    """Order is not cosmetic: the cascade means a show deleted first removes
    its season and episode before either gets an audit row of its own."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    assert [c.native_id for c in scan.prunable] == ["111", "110", "100"]
    assert (scan.gone, scan.held, scan.total) == (3, 0, 3)


async def test_a_gone_child_under_a_surviving_parent_is_prunable_on_its_own(session):
    """The re-match case: the show is fine, 64 episode rows are not. The
    descendant rule protects live rows from a cascade; it does not protect
    gone rows from themselves."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, {"plex": FakePlex(live={"100", "110"})})

    assert [c.native_id for c in scan.prunable] == ["111"]
    assert (scan.gone, scan.held, scan.total) == (1, 0, 3)


async def test_an_excluded_librarys_row_is_gone_even_though_plex_resolves_it(session):
    """The probe alone is not enough. ``_search_sync`` falls back from the
    stored rating key to a GUID walk over every PERMITTED section of the right
    type, so a show or episode whose identity also exists in a non-excluded
    library resolves under the excluded row's intent and reads as PRESENT --
    a DVR library duplicating shows already in the TV library is exactly that
    case. The row's own ``library`` column is the fact that settles it: the
    pipeline will never write to that item again either way."""
    await _add_item(session, "10", library="Movies")
    await _add_item(session, "11", library="DVR")

    scan = await find_prunable(
        session, {"plex": FakePlex(live={"10", "11"})}, {"plex": frozenset({"DVR"})}
    )

    assert [c.native_id for c in scan.prunable] == ["11"]
    assert (scan.gone, scan.held, scan.total, scan.excluded) == (1, 0, 2, 1)


async def test_an_excluded_familys_resolving_descendant_does_not_hold_it(session):
    """The cascade guard asks "does a descendant still RESOLVE", and inside an
    excluded library every one of them does. If the guard read the raw probe
    flags, an excluded show would be held by its own episode forever and the
    population this pass exists to retire would never shrink. So the guard
    reads the same effective reachability the ``gone`` set does."""
    show = await _add_item(session, "100", kind="show", library="DVR")
    season = await _add_item(session, "110", kind="season", parent=show, library="DVR")
    await _add_item(session, "111", kind="episode", parent=season, library="DVR")

    scan = await find_prunable(
        session, {"plex": FakePlex(live={"100", "110", "111"})}, {"plex": frozenset({"DVR"})}
    )

    assert [c.native_id for c in scan.prunable] == ["111", "110", "100"]
    assert (scan.gone, scan.held, scan.excluded) == (3, 0, 3)


async def test_the_fold_never_retires_a_row_whose_own_library_is_permitted(session):
    """Both cross-library directions, in one pair of families, because the
    fold has to get both right and they pull opposite ways.

    UPWARD -- an excluded show whose episode lives in a PERMITTED library and
    still resolves is HELD, and kept. ``media_items.parent_id`` is
    ``ondelete="CASCADE"`` (``db/models.py:36``), so deleting the show would
    silently take a row this service still manages, without even an audit row.
    That is the one outcome the cascade guard exists to prevent, and widening
    the guard's input must not cost it: the episode survives this sweep, so it
    holds every ancestor above it whatever its parent's library says.

    DOWNWARD -- a permitted show that still resolves keeps its own row while
    its DVR-library episode is retired underneath it. Goneness for a row whose
    own library is permitted is exactly the probe's answer, unchanged by the
    fold: ``not (resolved and True)`` is ``not resolved``. The fold can never
    make a permitted row gone that the probe called present.
    """
    excluded_show = await _add_item(session, "200", kind="show", library="DVR")
    await _add_item(
        session, "201", kind="episode", parent=excluded_show, library="TV Shows"
    )
    permitted_show = await _add_item(session, "300", kind="show", library="TV Shows")
    await _add_item(
        session, "301", kind="episode", parent=permitted_show, library="DVR"
    )

    scan = await find_prunable(
        session,
        {"plex": FakePlex(live={"200", "201", "300", "301"})},
        {"plex": frozenset({"DVR"})},
    )

    # Only the excluded episode goes. The excluded show is gone-but-held by its
    # permitted episode; the permitted show was never gone at all.
    assert [c.native_id for c in scan.prunable] == ["301"]
    assert (scan.gone, scan.held, scan.total, scan.excluded) == (2, 1, 4, 1)


async def test_an_empty_table_probes_nothing(session):
    plex = FakePlex(live=set())

    scan = await find_prunable(session, {"plex": plex})

    assert (scan.prunable, scan.gone, scan.held, scan.total) == ([], 0, 0, 0)
    assert plex.asked == []


async def test_the_scan_counts_the_asset_directories_a_prune_would_orphan(session):
    """One per pruned movie or show. Seasons and episodes keep their artwork
    under the show's folder (render/naming.py), so they orphan nothing of their
    own -- and the count is what the summary hands to the operator, because
    those directories become the existing asset_cleanup sweep's work."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)
    await _add_item(session, "200", kind="movie")

    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    assert len(scan.prunable) == 4
    assert scan.directories == 2


def test_intent_for_leaves_no_identity_behind():
    """Every id the row has must ride into the probe: the rating-key hint can
    be stale (Plex renumbers on a rebuild) and the GUID fallback is all that
    stands between a renumbered item and a wrongly pruned row."""
    from datetime import datetime, timezone

    from autoposter.scheduler.prune import PruneCandidate

    candidate = PruneCandidate(
        id=1, native_id="9", kind="movie", library="Movies", title="A Movie",
        parent_id=None, tmdb_id=1, tvdb_id=2, imdb_id="tt3", year=1999,
        season_number=None, episode_number=None, logo_upload_key=None,
        updated_at=datetime.now(timezone.utc),
    )

    intent = intent_for(candidate)

    assert (intent.tmdb_id, intent.tvdb_id, intent.imdb_id) == (1, 2, "tt3")
    assert intent.native_id_on("plex") == "9"
    assert intent.kind == "movie"


async def test_the_ancestor_walk_terminates_on_a_parent_id_cycle(session):
    """``parent_id`` is a self-referential FK, so a corrupted pair pointing at
    each other is possible; the ``_ancestors`` walk must terminate rather than
    hang the sweep, and a cycle must not get a live row misclassified as
    prunable. Built with a direct UPDATE after both rows exist, since a mutual
    cycle can never satisfy the FK at insert time."""
    a = await _add_item(session, "100", kind="season")
    b = await _add_item(session, "110", kind="season", parent=a)
    await session.execute(
        update(MediaItem).where(MediaItem.id == a.id).values(parent_id=b.id)
    )
    await session.commit()

    scan = await find_prunable(session, {"plex": FakePlex(live={"100"})})

    assert scan.total == 2
    assert scan.prunable == []


async def test_an_applied_retire_deletes_the_row(session):
    item = await _add_item(session, "11")
    item_id = item.id
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    outcome = await retire(session, scan.prunable)
    await session.commit()

    # ``media_items.id``, not the Plex native id: a candidate need not have
    # one at all (I4), and a None in this list matched every other ref-less
    # candidate downstream.
    assert outcome.pruned == [item_id]
    assert outcome.skipped == 0
    session.expire_all()
    assert (
        await session.execute(select(MediaItem).where(MediaItem.id == item_id))
    ).scalar_one_or_none() is None


async def test_each_delete_writes_one_audit_row_carrying_the_whole_identity(session):
    """The row is gone for good, so the audit is the only record that it ever
    existed. ``logo_upload_key`` is in it deliberately: a pruned item's
    uploaded clearlogo can no longer be reverted, and that loss must be written
    down rather than discovered later."""
    item = await _add_item(
        session, "11", title="A Movie", tmdb_id=5, imdb_id="tt9", year=2001,
        logo_upload_key="upload://abc123",
    )
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/assets/a.jpg"))
    await session.commit()
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    await retire(session, scan.prunable)
    await session.commit()

    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert (event.source, event.event_type) == (PRUNE_SOURCE, PRUNE_EVENT)
    assert event.payload["rating_key"] == "11"
    assert event.payload["refs"] == {"plex": "11"}
    assert event.payload["title"] == "A Movie"
    assert event.payload["library"] == "Movies"
    assert event.payload["kind"] == "movie"
    assert event.payload["tmdb_id"] == 5
    assert event.payload["imdb_id"] == "tt9"
    assert event.payload["year"] == 2001
    assert event.payload["render_count"] == 1
    assert event.payload["logo_upload_key"] == "upload://abc123"
    assert "no Plex item" in event.outcome


async def test_the_audit_rows_refs_include_every_server(session):
    """A row this service also knows through a second server keeps every
    ref in the audit, not just the Plex one the legacy ``rating_key`` scalar
    names."""
    item = await _add_item(session, "12", title="A Show")
    session.add(MediaItemServerRef(
        item_id=item.id, server="jellyfin", native_id="0a", library=item.library,
    ))
    await session.commit()
    # Both servers gone, so nothing holds the row reachable -- Task 19's
    # per-server prune only retires a row once EVERY configured server has
    # lost it, and this test is about the audit payload, not cross-server
    # reachability.
    scan = await find_prunable(
        session, {"plex": FakePlex(live=set()), "jellyfin": FakePlex(live=set(), name="jellyfin")}
    )

    await retire(session, scan.prunable)
    await session.commit()

    event = (await session.execute(select(EventLog))).scalar_one()
    assert event.payload["refs"] == {"plex": "12", "jellyfin": "0a"}


async def test_every_row_of_a_pruned_family_gets_its_own_audit_row(session):
    """The cascade is silent. Deleting the show first would remove its season
    and episode with no record that they had ever been there, which is why
    ``find_prunable`` hands them over deepest-first."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    episode = await _add_item(session, "111", kind="episode", parent=season)
    deepest_first = [episode.id, season.id, show.id]
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == deepest_first
    keys = {
        event.payload["rating_key"]
        for event in (await session.execute(select(EventLog))).scalars().all()
    }
    assert keys == {"100", "110", "111"}
    session.expire_all()
    assert (await session.execute(select(MediaItem))).scalars().all() == []


async def test_the_renders_and_facts_of_a_pruned_row_go_with_it(session):
    """Both cascade at the database level (db/models.py), so this asserts the
    schema does what the prune assumes rather than adding code to do it."""
    item = await _add_item(session, "11")
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/assets/a.jpg"))
    session.add(ItemFacts(item_id=item.id))
    await session.commit()
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    await retire(session, scan.prunable)
    await session.commit()

    session.expire_all()
    assert (await session.execute(select(Render))).scalars().all() == []
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_a_row_re_upserted_under_the_pass_survives_and_is_counted_skipped(session):
    """The concurrency guard, exercised against the row a worker touched.

    A worker can resolve and re-upsert a row between this sweep's probe and its
    delete -- the pass is minutes long and the pool never stops. The delete is
    keyed on ``(id, updated_at)`` and the upsert's ON CONFLICT arm stamps
    ``updated_at=now()``, so such a row no longer matches and is left exactly
    as the worker just wrote it. Each write is its own committed transaction,
    which is what makes the two ``now()`` values differ; nothing here asserts
    on a timestamp or a duration.

    A plain ORM update stands in for the real upsert here: since Task 6 a real
    ``_upsert_media_item`` finds the row to update by ``identity_key``, and
    this row's synthetic one (``_add_item``) is not something a real
    ``ResolvedItem`` could ever compute -- the guard being pinned reads
    ``updated_at`` alone, which ``onupdate=func.now()`` stamps on any ORM
    update to the row, exactly as the real upsert's own ON CONFLICT arm does.
    """
    item = await _add_item(session, "11", title="Old Title")
    item_id = item.id
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})
    assert [c.native_id for c in scan.prunable] == ["11"]

    await session.execute(update(MediaItem).where(MediaItem.id == item_id).values(title="New Title"))
    await session.commit()

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == []
    assert outcome.skipped == 1
    session.expire_all()
    survivor = (
        await session.execute(select(MediaItem).where(MediaItem.id == item_id))
    ).scalar_one()
    assert survivor.title == "New Title"
    assert (await session.execute(select(EventLog))).scalars().all() == [], (
        "a row that was not deleted must not get a deletion audit row"
    )


async def test_a_re_upserted_child_holds_its_whole_family_back_from_the_delete(session):
    """The guard alone would spare the child and then lose it to the cascade.

    Candidates arrive deepest-first, so the skipped episode is followed by its
    still-gone season and show, whose ``updated_at`` the episode's upsert never
    touched -- those deletes would match, and ``parent_id``'s ON DELETE CASCADE
    would take the live episode with them: no audit row, absent from
    ``pruned``, and the worker that just wrote it left holding a stale id. So a
    skip propagates upward, and the whole chain is counted skipped.
    """
    show = await _add_item(session, "100", kind="show", title="A Show")
    season = await _add_item(session, "110", kind="season", parent=show)
    episode = await _add_item(session, "111", kind="episode", parent=season)
    show_id, season_id, episode_id = show.id, season.id, episode.id
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})
    assert [c.native_id for c in scan.prunable] == ["111", "110", "100"]

    # A plain ORM update stands in for the real re-upsert (see the sibling
    # test's docstring): the episode's parent_id is already correct from
    # _add_item, so touching only its own row is enough to bump its
    # updated_at the way a real re-resolve would.
    await session.execute(update(MediaItem).where(MediaItem.id == episode_id).values(title="New Title"))
    await session.commit()

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == []
    assert outcome.skipped == 3
    session.expire_all()
    survivors = {
        item.id
        for item in (await session.execute(select(MediaItem))).scalars().all()
    }
    assert survivors == {show_id, season_id, episode_id}
    assert (await session.execute(select(EventLog))).scalars().all() == [], (
        "nothing was deleted, so nothing may carry a deletion audit row"
    )


async def test_a_blocked_family_does_not_spare_an_unrelated_gone_row(session):
    """The block is per-chain, not a pass-wide abort. A re-upserted episode
    holds its own ancestors and nothing else -- an unrelated movie that probed
    gone is still deleted in the same pass, with its audit row."""
    show = await _add_item(session, "100", kind="show", title="A Show")
    season = await _add_item(session, "110", kind="season", parent=show)
    episode = await _add_item(session, "111", kind="episode", parent=season)
    episode_id = episode.id
    movie = await _add_item(session, "11", title="A Movie")
    movie_id = movie.id
    scan = await find_prunable(session, {"plex": FakePlex(live=set())})

    # A plain ORM update stands in for the real re-upsert -- see
    # test_a_row_re_upserted_under_the_pass_survives_and_is_counted_skipped's
    # docstring.
    await session.execute(update(MediaItem).where(MediaItem.id == episode_id).values(title="New Title"))
    await session.commit()

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == [movie_id]
    assert outcome.skipped == 3
    session.expire_all()
    assert (
        await session.execute(select(MediaItem).where(MediaItem.id == movie_id))
    ).scalar_one_or_none() is None
    keys = {
        event.payload["rating_key"]
        for event in (await session.execute(select(EventLog))).scalars().all()
    }
    assert keys == {"11"}


async def test_pending_and_parked_jobs_for_a_pruned_row_are_dismissed(session):
    """A parked job for a pruned row is pure Failures-page noise, and a pending
    one would park by construction -- nothing can resolve it any more.

    Enqueued through the real producer shape (``payload["refs"]["plex"]``),
    not a hand-built ``{"rating_key": ...}`` payload: the query this pins is
    ``payload["refs"]["plex"] OR payload["rating_key"]``, and a test that only
    ever wrote the legacy half would stay green even if the ``refs`` half of
    that query were broken.
    """
    for state in ("pending", "parked"):
        session.add(Job(
            kind="process_item",
            payload=_producer_payload("11"),
            dedupe_key=f"process_item:movie:title gone:{state}",
            state=state,
        ))
    await session.commit()

    dismissed = await dismiss_jobs_for(session, ["11"])
    await session.commit()

    assert dismissed == 2
    session.expire_all()
    states = {job.state for job in (await session.execute(select(Job))).scalars().all()}
    assert states == {"dismissed"}


async def test_a_legacy_shaped_payload_is_still_matched(session):
    """A job queued before ``refs`` existed carries only the bare
    ``rating_key`` key -- the OR clause's other half -- and must still be
    found."""
    session.add(Job(
        kind="process_item",
        payload={"kind": "movie", "title": "Gone", "rating_key": "11"},
        dedupe_key="process_item:movie:title gone:legacy",
        state="parked",
    ))
    await session.commit()

    dismissed = await dismiss_jobs_for(session, ["11"])
    await session.commit()

    assert dismissed == 1
    session.expire_all()
    assert (await session.execute(select(Job))).scalar_one().state == "dismissed"


async def test_a_running_job_is_left_to_park_itself(session):
    """A claimed job is never interrupted anywhere in this project; one running
    against a pruned row simply parks, and the next applied pass dismisses it."""
    session.add(Job(
        kind="process_item",
        payload=_producer_payload("11"),
        dedupe_key="process_item:movie:title gone",
        state="running",
    ))
    await session.commit()

    dismissed = await dismiss_jobs_for(session, ["11"])
    await session.commit()

    assert dismissed == 0
    session.expire_all()
    assert (await session.execute(select(Job))).scalar_one().state == "running"


async def test_jobs_for_other_rating_keys_are_untouched(session):
    session.add(Job(
        kind="process_item",
        payload=_producer_payload("22", title="Still Here"),
        dedupe_key="process_item:movie:title still here",
        state="parked",
    ))
    await session.commit()

    assert await dismiss_jobs_for(session, ["11"]) == 0
    assert await dismiss_jobs_for(session, []) == 0
    session.expire_all()
    assert (await session.execute(select(Job))).scalar_one().state == "parked"


def test_an_implausible_absolute_count_refuses_with_both_numbers():
    prune = SimpleNamespace(max_prunes=3, max_prune_share=0.25)

    refusal = implausible_prune_count(4, 100, prune)

    assert refusal is not None
    assert "4" in refusal and "3" in refusal and "100" in refusal
    assert "refus" in refusal.lower()
    assert implausible_prune_count(3, 100, prune) is None


def test_an_implausible_share_refuses_on_a_library_too_small_for_the_absolute_cap():
    prune = SimpleNamespace(max_prunes=500, max_prune_share=0.25)

    refusal = implausible_prune_count(10, 20, prune)

    assert refusal is not None and "refus" in refusal.lower()
    # Below the minimum sample the share means nothing and only the absolute
    # cap applies -- "2 of 3 rows are gone" is a small library, not evidence.
    assert implausible_prune_count(2, 3, prune) is None


def _config(*, apply=False, max_prunes=500, max_prune_share=0.25, max_orphans=500,
            excluded=()):
    """Only what the job actually reads, the ``tests/test_scheduler_cleanup_job.py``
    ``_config`` pattern -- a SimpleNamespace keeps each test's intent on screen."""
    return SimpleNamespace(
        prune=SimpleNamespace(
            apply=apply, max_prunes=max_prunes, max_prune_share=max_prune_share
        ),
        cleanup=SimpleNamespace(max_orphans=max_orphans, max_orphan_share=0.25),
        scheduler=SimpleNamespace(prune_days=7),
        plex=SimpleNamespace(excluded_libraries=list(excluded)),
        # Task 19: make_prune_job's servers_factory builds every configured
        # server; this suite is Plex-only throughout, so `jellyfin` reads as
        # not configured, exactly like a real Config with no jellyfin: block.
        jellyfin=None,
    )


def _job(config, plex, *, healthy=True, extra_servers=None):
    # Task 19: make_prune_job's second argument is a servers_factory
    # returning a {name: MediaServer} mapping, not one bare server.
    # `extra_servers` lets a multi-server test add e.g. a jellyfin double
    # beside `plex` without a whole second helper.
    servers = {"plex": plex, **(extra_servers or {})}
    return make_prune_job(ConfigHolder(config), lambda: servers, lambda: healthy)


class _ReupsertingPlex(FakePlex):
    """A probe that answers "gone" and then writes the row back, mid-pass.

    The race the ``(id, updated_at)`` key exists for: a worker re-resolves and
    re-upserts a row between this pass's probe and its delete. Doing the write
    from inside ``exists_many`` puts it where a fixture cannot reach it -- in
    the middle of one ``job.run`` -- so what these tests exercise is the job's
    own handling of a skip, not ``retire``'s.

    A plain ORM update of the row named by ``native_id`` stands in for the
    real re-upsert: since Task 6 the real ``_upsert_media_item`` finds its
    row by ``identity_key``, which this fixture's rows (``_add_item``) never
    carry one a real ``ResolvedItem`` could compute -- ``onupdate=func.now()``
    bumps ``updated_at`` on this update exactly as the real upsert's ON
    CONFLICT arm does, which is the only thing the guard being pinned reads.

    Task 19: the real re-upsert also re-touches this server's OWN ref row
    (``upsert_server_ref``'s ON CONFLICT arm, keyed on ``uq_server_ref``),
    and ``_prune_stale_refs`` now guards its own delete on that ref's
    ``(id, updated_at)`` the same way ``retire`` guards the item's -- so the
    stand-in bumps the ref's ``updated_at`` too, or the ref would still read
    as stale and be deleted out from under a `MediaItem` this fixture means
    to keep alive.
    """

    def __init__(self, session, native_id, *, title="New Title", live=()):
        super().__init__(live)
        self._session = session
        self._native_id = native_id
        self._title = title

    async def exists_many(self, intents):
        flags = await super().exists_many(intents)
        item_id = (
            await self._session.execute(
                select(MediaItemServerRef.item_id).where(
                    MediaItemServerRef.server == "plex",
                    MediaItemServerRef.native_id == self._native_id,
                )
            )
        ).scalar_one()
        await self._session.execute(
            update(MediaItemServerRef)
            .where(
                MediaItemServerRef.item_id == item_id, MediaItemServerRef.server == "plex",
            )
            .values(updated_at=func.now())
        )
        await self._session.execute(
            update(MediaItem).where(MediaItem.id == item_id).values(title=self._title)
        )
        await self._session.commit()
        return flags


class _ReupsertingRefOnlyPlex(FakePlex):
    """Fix round 2, NB1: the genuine ``retry_pending_deliveries`` shape --
    it calls ``upsert_server_ref`` entirely on its own, unrelated to this
    sweep, which re-touches ONLY the ref's own ``updated_at`` and never
    ``media_items.updated_at`` at all (unlike ``_ReupsertingPlex`` above,
    which stands in for a worker re-upserting the WHOLE item). Reachability
    has to key its match on ``(id, updated_at)``, not the ref's id alone, or
    this concurrent re-upsert is invisible to it and the whole row is
    deleted out from under it on the strength of the scan's now-stale
    observation."""

    def __init__(self, session, native_id, *, live=()):
        super().__init__(live)
        self._session = session
        self._native_id = native_id

    async def exists_many(self, intents):
        flags = await super().exists_many(intents)
        await self._session.execute(
            update(MediaItemServerRef)
            .where(
                MediaItemServerRef.server == "plex",
                MediaItemServerRef.native_id == self._native_id,
            )
            .values(updated_at=func.now())
        )
        await self._session.commit()
        return flags


async def test_a_ref_only_reupsert_between_scan_and_apply_is_never_a_candidate(session):
    """Fix round 2, NB1: a ref re-upserted between the scan and the apply --
    ``retry_pending_deliveries``'s own ``upsert_server_ref`` call, which
    never touches ``media_items.updated_at`` -- must survive, and so must
    the row it belongs to. Before the fix, reachability matched on the
    ref's id alone, so this concurrent re-upsert read as the same stale ref
    the scan observed and the whole item was pruned anyway."""
    await _add_item(session, "10")

    plex = _ReupsertingRefOnlyPlex(session, "10", live=set())
    summary = await _job(_config(apply=True), plex).run(session)

    assert "pruned 0 of 1" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1
    assert len((await session.execute(select(MediaItemServerRef))).scalars().all()) == 1


async def test_the_job_is_named_and_paced_off_the_holder():
    """The cadence is a deref, not a captured number, so an edit to
    ``scheduler.prune_days`` is live the way every other job's is."""
    holder = ConfigHolder(_config())
    job = make_prune_job(holder, lambda: FakePlex(), lambda: True)

    assert job.name == "plex_prune"
    assert job.current_interval() == 7 * 24 * 3600

    faster = _config()
    faster.scheduler.prune_days = 1
    holder.swap(faster)

    assert job.current_interval() == 24 * 3600


async def test_an_unhealthy_plex_refuses_before_anything_is_probed(session):
    """The inversion that makes this sweep dangerous: a server that answers
    nothing makes EVERY row look gone. So this is the first check, before the
    table is read and before a client is even built."""
    await _add_item(session, "11")

    def exploding_factory():
        raise AssertionError("no Plex client may be built when Plex is unhealthy")

    job = make_prune_job(ConfigHolder(_config(apply=True)), exploding_factory, lambda: False)
    summary = await job.run(session)

    assert "refus" in summary.lower() and "unhealthy" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1


async def test_an_empty_media_items_table_refuses(session):
    """An empty table means a restore has not finished, not that the library
    is gone -- the ``refuse_if_empty`` guard every mode and the cleanup sweep
    already share."""

    def exploding_factory():
        raise AssertionError("no Plex client may be built for an empty table")

    job = make_prune_job(ConfigHolder(_config(apply=True)), exploding_factory, lambda: True)
    summary = await job.run(session)

    assert "refus" in summary.lower() and "empty" in summary.lower()


async def test_a_probe_failure_takes_the_pass_down_rather_than_reading_as_gone(session):
    """The failure this sweep must never have. It raises rather than returning
    so ``last_status`` records ``failed`` (scheduler/core.py), and the message
    names the exception class only -- a Plex error's text carries the server
    address, and ``last_detail`` is rendered in the dashboard."""
    await _add_item(session, "11")
    plex = FakePlex(error=ConnectionError("https://plex.example:32400 connection reset"))

    job = _job(_config(apply=True), plex)

    with pytest.raises(PruneRefused) as caught:
        await job.run(session)

    message = str(caught.value)
    assert "ConnectionError" in message
    assert "plex.example" not in message and "connection reset" not in message
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1


async def test_the_session_is_not_idle_in_transaction_when_the_probe_runs(session):
    """The direct analogue of ``plex_merge``'s guard (516af28,
    tests/test_scheduler_merge_job.py's
    ``test_the_session_is_not_idle_in_transaction_when_the_probe_runs``), for
    the job that actually fired the alert.

    ``refuse_if_empty``'s probe and ``find_prunable``'s full ``media_items``
    read open a transaction, and the probe below is
    ``PlexClient.exists_many`` -- one thread for the whole walk, 15,794
    sequential HTTP round-trips, **797 seconds measured** on 2026-09-03, on a
    pass that changed nothing. Idle-in-transaction for that whole window pins
    a pooled connection and the vacuum horizon. One ``rollback()`` between the
    candidate build and the probe closes it, and nothing is lost:
    ``PruneCandidate`` is a frozen dataclass and ``retire()`` re-reads every
    row anyway, deleting on ``(id, updated_at)`` precisely so a row that
    changed under the pass survives.
    """
    await _add_item(session, "10")
    await _add_item(session, "11")

    seen = {}

    class SpyingPlex(FakePlex):
        async def exists_many(self, intents):
            seen["in_transaction"] = session.in_transaction()
            return await super().exists_many(intents)

    # Through the real entry point, not find_prunable directly: the span the
    # alert measured starts at refuse_if_empty's read inside job.run, one
    # statement ABOVE find_prunable, so a test that called find_prunable alone
    # would leave the job's own opening read uncovered.
    job = _job(_config(apply=True), SpyingPlex(live={"10"}))

    summary = await job.run(session)

    assert seen == {"in_transaction": False}
    # The rollback strands nothing: the scan still classifies both rows and
    # retire() still opens its own transaction and deletes.
    assert "pruned 1 of 2" in summary


async def test_the_refusal_reaches_the_dashboard_whole(session, session_factory):
    """``PruneRefused.served_detail``'s end-to-end guard (roadmap row 209).

    ``scheduler/core.py``'s failure branch narrows a failure to its class name
    on the served copy, unless the exception marks itself ``served_detail =
    True``. Delete that marker from ``PruneRefused`` and every refusal above
    reaches the dashboard as the bare word "PruneRefused" -- the operator
    loses the whole reason, which is the only thing a refusal exists to say.
    Asserted through a real ``Scheduler`` writing the real ``scheduled_runs``
    row, because the marker is read nowhere else; the test above stops at
    ``str(caught.value)`` and so cannot see the narrowing at all.

    ``CollectionsPassFailed``'s marker has the same guard at
    ``test_builder_knobs.py``'s ``last_detail`` assertion.
    """
    await _add_item(session, "11")
    plex = FakePlex(error=ConnectionError("https://plex.example:32400 connection reset"))

    stop = asyncio.Event()
    scheduler = Scheduler(session_factory, [_job(_config(apply=True), plex)], poll_seconds=0.01)
    task = asyncio.create_task(scheduler.run(stop))
    try:
        async with asyncio.timeout(5):
            while True:
                async with session_factory() as check:
                    row = (
                        await check.execute(select(ScheduledRun))
                    ).scalar_one_or_none()
                if row is not None and row.last_status == "failed":
                    break
                await asyncio.sleep(0.01)
    finally:
        stop.set()
        await task

    assert row.last_detail.startswith("refused: scanning for prunable rows failed")
    assert "ConnectionError" in row.last_detail
    # The narrowing this guard exists to catch, named rather than implied.
    assert row.last_detail != "PruneRefused"
    # And the refusal is still served-safe on the way through.
    assert "plex.example" not in row.last_detail


async def test_a_connect_failure_refuses_without_leaking_the_server_address(session):
    """The client is built inside the same try as the scan, because building it
    connects. A refused connection, a rejected token or a plexapi
    ``BadRequest`` all raise with the server address in the message, and
    ``is_healthy()`` cannot prevent it: the liveness probe is periodic, so a
    server can drop between the last probe and this connect. Uncaught, that
    message is what ``scheduler/core.py`` writes to ``last_detail``, which the
    dashboard renders."""
    await _add_item(session, "11")

    def exploding_factory():
        raise ConnectionError("https://plex.example:32400 boom")

    job = make_prune_job(ConfigHolder(_config(apply=True)), exploding_factory, lambda: True)

    with pytest.raises(PruneRefused) as caught:
        await job.run(session)

    message = str(caught.value)
    assert "ConnectionError" in message
    assert "plex.example" not in message and "boom" not in message
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1


async def test_a_dry_run_deletes_nothing_and_reports_the_counts(session):
    await _add_item(session, "10")
    await _add_item(session, "11")

    summary = await _job(_config(apply=False), FakePlex(live={"10"})).run(session)

    assert "dry run" in summary.lower()
    assert "1 of 2" in summary
    assert "refs to retire: 1" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2
    assert (await session.execute(select(EventLog))).scalars().all() == []
    # C1: a dry run must never write, so "11"'s own stale ref -- the whole
    # reason it is prunable at all -- is still there to prove it.
    assert len((await session.execute(select(MediaItemServerRef))).scalars().all()) == 2


async def test_an_implausible_share_refuses_the_whole_pass(session):
    """Rows exist and Plex answers, but it answers about a different library --
    rebuilt, renamed, still loading. The health probe is happy and every row
    reads as gone."""
    for n in range(30):
        await _add_item(session, str(100 + n))

    summary = await _job(_config(apply=True), FakePlex(live=set())).run(session)

    assert "refus" in summary.lower()
    assert "30" in summary, f"the refusal must report the real numbers: {summary!r}"
    session.expire_all()
    # C1: the refusal fires before `_retire_stale_refs` ever runs -- every
    # one of these 30 rows' own ref would otherwise have been deleted.
    assert len((await session.execute(select(MediaItemServerRef))).scalars().all()) == 30
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 30


async def test_an_implausible_absolute_count_refuses_the_whole_pass(session):
    for n in range(4):
        await _add_item(session, str(100 + n))

    config = _config(apply=True, max_prunes=3, max_prune_share=1.0)
    summary = await _job(config, FakePlex(live=set())).run(session)

    assert "refus" in summary.lower()
    assert "4" in summary and "3" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 4

    # One over the cap and the same library is worked normally.
    config = _config(apply=True, max_prunes=4, max_prune_share=1.0)
    summary = await _job(config, FakePlex(live=set())).run(session)
    assert "pruned 4" in summary
    session.expire_all()
    assert (await session.execute(select(MediaItem))).scalars().all() == []


async def test_an_applied_pass_prunes_dismisses_and_reports_the_file_consequence(session):
    await _add_item(session, "10")
    item = await _add_item(session, "11")
    session.add(Render(item_id=item.id, art_kind="poster", asset_path="/assets/a.jpg"))
    session.add(Job(
        kind="process_item",
        payload=_producer_payload("11"),
        dedupe_key="process_item:movie:title gone",
        state="parked",
    ))
    await session.commit()

    summary = await _job(_config(apply=True), FakePlex(live={"10"})).run(session)

    assert "pruned 1 of 2" in summary
    assert "dismissed 1" in summary
    assert "asset_cleanup" in summary
    session.expire_all()
    surviving_ids = (await session.execute(select(MediaItem.id))).scalars().all()
    survivor_refs = (
        await session.execute(
            select(MediaItemServerRef.native_id).where(MediaItemServerRef.item_id.in_(surviving_ids))
        )
    ).scalars().all()
    assert survivor_refs == ["10"]
    assert (await session.execute(select(Job))).scalar_one().state == "dismissed"
    assert len((await session.execute(select(EventLog))).scalars().all()) == 1


async def test_the_summary_warns_when_the_prune_would_disarm_the_asset_cleanup(session):
    """A large prune orphans a lot of directories at once, and past
    ``cleanup.max_orphans`` the cleanup pass refuses ENTIRELY -- so a prune can
    silently stop the sweep that was supposed to handle its aftermath. Said in
    the summary; this module still touches no files."""
    for n in range(4):
        await _add_item(session, str(100 + n))

    config = _config(apply=False, max_prunes=500, max_prune_share=1.0, max_orphans=3)
    summary = await _job(config, FakePlex(live=set())).run(session)

    assert "WARNING" in summary
    assert "cleanup.max_orphans" in summary
    assert "4" in summary and "3" in summary


async def test_the_summary_reports_held_parents_even_when_nothing_is_prunable(session):
    """A held family is the interesting case, not a silent zero: it says the
    show is gone but one of its episodes still resolves, which is a Plex-side
    inconsistency an operator wants to see."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    summary = await _job(_config(apply=True), FakePlex(live={"111"})).run(session)

    assert "2" in summary and "held" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 3


async def test_an_applied_pass_counts_directories_off_what_it_actually_pruned(session):
    """A row that survived the pass orphans nothing -- it is still there.
    Counting the candidates instead would claim a directory no delete created
    and, as here, fire the cleanup-cap warning over a line this prune never
    crossed: one directory, exactly at ``max_orphans``, is not a warning."""
    await _add_item(session, "10")
    await _add_item(session, "11", title="Old Title")

    plex = _ReupsertingPlex(session, "11")
    summary = await _job(_config(apply=True, max_orphans=1), plex).run(session)

    assert "pruned 1 of 2" in summary
    assert "1 asset director" in summary
    assert "WARNING" not in summary, (
        f"only the pruned row orphans a directory, and it is at the cap: {summary!r}"
    )


async def _add_ref_less_item(session, title, *, kind="movie", library="Movies"):
    """A ``media_items`` row with NO server ref at all -- what a Jellyfin-only
    item, or a row whose only ref was deleted, looks like to this sweep.
    ``find_prunable`` reads its ``native_id`` back as ``None``."""
    item = MediaItem(identity_key=f"{kind}:path:::{title}", library=library, kind=kind, title=title)
    session.add(item)
    await session.commit()
    return item


async def test_a_skipped_ref_less_row_does_not_speak_for_the_other_ref_less_rows(session):
    """I4. A candidate with no Plex ref has ``native_id=None``, so keying the
    applied pass's "what was actually deleted" set on the native id put a
    ``None`` in it -- and every OTHER ref-less candidate then matched, whether
    or not it was deleted. Here the surviving row would have claimed the
    deleted one's directory count as a second orphan. Matching on
    ``media_items.id`` is what closes it.

    Task 19: a ref-less row has no ref on any server, so it never reaches a
    per-server probe at all (there is nothing to check) -- ``_ReupsertingByIdPlex``'s
    old hook, inside ``exists_many``, has nothing left to fire from. The
    concurrent write is applied directly here, between the scan and the
    delete, in its place; the row's OWN reachability answer (gone, having no
    ref anywhere) is unaffected either way, which is the point of this test.
    """
    kept = await _add_ref_less_item(session, "Kept")
    # Captured before find_prunable's own rollback expires this instance --
    # a lazy reload of `.id` afterward would be a plain attribute access
    # outside greenlet context.
    kept_id = kept.id
    await _add_ref_less_item(session, "Gone")

    scan = await find_prunable(session, {"plex": FakePlex(live=set())})
    await session.execute(
        update(MediaItem).where(MediaItem.id == kept_id).values(title="New Title")
    )
    await session.commit()

    outcome = await retire(session, scan.prunable)

    assert outcome.skipped == 1
    assert kept_id not in outcome.pruned
    assert _directory_count([c for c in scan.prunable if c.id in set(outcome.pruned)]) == 1, (
        "the skipped ref-less row was counted as deleted too"
    )
    session.expire_all()
    survivors = (await session.execute(select(MediaItem.title))).scalars().all()
    assert survivors == ["New Title"]


async def test_a_skipped_rows_queued_job_is_not_dismissed(session):
    """Why the disposal is keyed on ``outcome.pruned`` and not on the candidate
    list, said at the job level. The row was re-upserted between this pass's
    probe and its delete, so it survives -- and the work queued against it is
    still live work, which a dismissal keyed on the candidates would sweep away
    on the strength of an observation that stopped being true mid-pass.

    Task 19: the re-upsert also touches this item's OWN ref
    (``_ReupsertingPlex``'s own note), so ``_prune_stale_refs``'s ``(id,
    updated_at)`` guard now catches the very same race one step earlier --
    the ref survives, the row is never even a candidate, and there is no
    ``outcome.skipped`` count or "shielded by one that did" line to show for
    it. What still has to hold, and is still asserted here, is the point of
    the test: the row is not pruned, and its queued job is not dismissed."""
    await _add_item(session, "11", title="Old Title")
    session.add(Job(
        kind="process_item",
        payload=_producer_payload("11"),
        dedupe_key="process_item:movie:title gone",
        state="parked",
    ))
    await session.commit()

    plex = _ReupsertingPlex(session, "11")
    summary = await _job(_config(apply=True), plex).run(session)

    assert "pruned 0 of 1" in summary
    assert "dismissed 0" in summary
    session.expire_all()
    survivor = (await session.execute(select(MediaItem))).scalar_one()
    survivor_native_id = (
        await session.execute(
            select(MediaItemServerRef.native_id).where(MediaItemServerRef.item_id == survivor.id)
        )
    ).scalar_one()
    assert (survivor_native_id, survivor.title) == ("11", "New Title")
    assert (await session.execute(select(Job))).scalar_one().state == "parked"
    assert (await session.execute(select(EventLog))).scalars().all() == [], (
        "nothing was deleted, so nothing may carry a deletion audit row"
    )


async def test_an_applied_pass_retires_excluded_rows_and_counts_them_separately(session):
    """The whole finding, through the real job: Plex still resolves the DVR
    row -- the operator did not delete it, they excluded its library -- and it
    is retired anyway, through the same retire path the orphan case uses. Its
    forever-deferring job goes with it: `dismiss_jobs_for` covers `deferred`,
    which is the state an unresolvable item's job actually reaches (the worker
    defers on ItemNotFound, it does not park)."""
    await _add_item(session, "10", library="Movies")
    await _add_item(session, "11", library="DVR", kind="show")
    session.add(Job(
        kind="process_item",
        payload=_producer_payload("11", title="Recorded", kind="show"),
        dedupe_key="process_item:show:title recorded",
        state="deferred",
    ))
    await session.commit()

    config = _config(apply=True, excluded=["DVR"])
    summary = await _job(config, FakePlex(live={"10", "11"})).run(session)

    assert "pruned 1 of 2" in summary
    assert "1 row(s) in excluded libraries retired" in summary
    assert "dismissed 1" in summary
    session.expire_all()
    surviving_ids = (await session.execute(select(MediaItem.id))).scalars().all()
    survivor_refs = (
        await session.execute(
            select(MediaItemServerRef.native_id).where(MediaItemServerRef.item_id.in_(surviving_ids))
        )
    ).scalars().all()
    assert survivor_refs == ["10"]
    assert (await session.execute(select(Job))).scalar_one().state == "dismissed"
    (event,) = (await session.execute(select(EventLog))).scalars().all()
    assert event.payload["rating_key"] == "11"
    assert event.payload["library"] == "DVR"


async def test_an_applied_pass_deletes_the_doomed_ref_but_keeps_a_row_another_server_still_resolves(
    session,
):
    """Fix round 1, C1's whole point through the real job: a ref going stale
    on ONE server must not touch the row at all when another server still
    resolves it -- only that one ref is deleted, the media_items row and its
    live ref on the other server survive untouched."""
    item = await _add_item(session, "10")
    session.add(MediaItemServerRef(
        item_id=item.id, server="jellyfin", native_id="j10", library=item.library,
    ))
    await session.commit()

    summary = await _job(
        _config(apply=True), FakePlex(live=set()),
        extra_servers={"jellyfin": FakePlex(live={"j10"}, name="jellyfin")},
    ).run(session)

    assert "pruned 0 of 1" in summary
    assert "refs retired: 1" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 1
    refs = {
        (row.server, row.native_id)
        for row in (await session.execute(select(MediaItemServerRef))).scalars().all()
    }
    assert refs == {("jellyfin", "j10")}, "the stale plex ref must be gone, the live jellyfin ref kept"


async def test_a_dry_run_names_the_excluded_population_without_deleting(session):
    """Dry run is the default and stays honoured: the population is named and
    counted, and nothing is touched."""
    await _add_item(session, "10", library="Movies")
    await _add_item(session, "11", library="DVR")

    config = _config(apply=False, excluded=["DVR"])
    summary = await _job(config, FakePlex(live={"10", "11"})).run(session)

    assert "1 row(s) in excluded libraries would be retired" in summary
    assert "refs to retire: 1" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2
    assert (await session.execute(select(EventLog))).scalars().all() == []
    # C1: dry run touches nothing, even though "11"'s only ref is on an
    # excluded library and would otherwise be a delete candidate.
    assert len((await session.execute(select(MediaItemServerRef))).scalars().all()) == 2


async def test_the_caps_still_rule_a_newly_excluded_library(session):
    """Excluded rows are not exempt from the plausibility caps -- they join
    `prunable` and are measured with everything else. Excluding a large
    library in one edit therefore refuses and reports the numbers rather than
    retiring a library's worth of rows on the next pass, which is the whole
    point of the caps and is what the operator note warns about."""
    for n in range(4):
        await _add_item(session, str(100 + n), library="DVR")

    config = _config(apply=True, max_prunes=3, max_prune_share=1.0, excluded=["DVR"])
    summary = await _job(config, FakePlex(live={"100", "101", "102", "103"})).run(session)

    assert "refus" in summary.lower()
    assert "4" in summary and "3" in summary
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 4


def test_the_job_name_is_in_the_hand_trigger_allowlist():
    """``SCHEDULED_JOB_NAMES`` is spelled out in api/routes.py rather than
    imported from the factories, so the two can drift and a job silently
    becomes untriggerable. This is the check that they have not."""
    from autoposter.api.routes import SCHEDULED_JOB_NAMES

    job = make_prune_job(ConfigHolder(_config()), lambda: FakePlex(), lambda: True)

    assert job.name in SCHEDULED_JOB_NAMES
