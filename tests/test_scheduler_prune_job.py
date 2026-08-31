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
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import EventLog, ItemFacts, Job, MediaItem, Render, ScheduledRun
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import _upsert_media_item
from autoposter.scheduler.core import Scheduler
from autoposter.scheduler.prune import (
    PRUNE_EVENT,
    PRUNE_SOURCE,
    PruneRefused,
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
    never be the same answer.
    """

    def __init__(self, live=(), *, error=None):
        self._live = set(live)
        self._error = error
        self.asked = []

    async def exists_many(self, intents):
        if self._error is not None:
            raise self._error
        self.asked.extend(intents)
        return [intent.rating_key in self._live for intent in intents]


async def _add_item(session, rating_key, *, kind="movie", parent=None, **columns):
    """One ``media_items`` row, committed so its ``updated_at`` is a settled
    value from its own transaction (which the concurrency guard depends on)."""
    item = MediaItem(
        rating_key=rating_key,
        library=columns.pop("library", "Movies" if kind == "movie" else "TV Shows"),
        kind=kind,
        title=columns.pop("title", f"Item {rating_key}"),
        parent_id=parent.id if parent is not None else None,
        **columns,
    )
    session.add(item)
    await session.commit()
    return item


async def test_a_row_plex_still_resolves_is_never_a_candidate(session):
    await _add_item(session, "10")

    scan = await find_prunable(session, FakePlex(live={"10"}))

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (0, 0, 1)


async def test_a_row_that_no_longer_resolves_is_prunable(session):
    await _add_item(session, "10")
    await _add_item(session, "11")

    scan = await find_prunable(session, FakePlex(live={"10"}))

    assert [c.rating_key for c in scan.prunable] == ["11"]
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

    await find_prunable(session, plex)

    (intent,) = plex.asked
    assert intent.rating_key == "42"
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

    scan = await find_prunable(session, FakePlex(live={"111"}))

    assert scan.prunable == []
    assert (scan.gone, scan.held, scan.total) == (2, 2, 3)


async def test_a_family_that_is_entirely_gone_is_prunable_deepest_first(session):
    """Order is not cosmetic: the cascade means a show deleted first removes
    its season and episode before either gets an audit row of its own."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, FakePlex(live=set()))

    assert [c.rating_key for c in scan.prunable] == ["111", "110", "100"]
    assert (scan.gone, scan.held, scan.total) == (3, 0, 3)


async def test_a_gone_child_under_a_surviving_parent_is_prunable_on_its_own(session):
    """The re-match case: the show is fine, 64 episode rows are not. The
    descendant rule protects live rows from a cascade; it does not protect
    gone rows from themselves."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)

    scan = await find_prunable(session, FakePlex(live={"100", "110"}))

    assert [c.rating_key for c in scan.prunable] == ["111"]
    assert (scan.gone, scan.held, scan.total) == (1, 0, 3)


async def test_an_empty_table_probes_nothing(session):
    plex = FakePlex(live=set())

    scan = await find_prunable(session, plex)

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

    scan = await find_prunable(session, FakePlex(live=set()))

    assert len(scan.prunable) == 4
    assert scan.directories == 2


def test_intent_for_leaves_no_identity_behind():
    """Every id the row has must ride into the probe: the rating-key hint can
    be stale (Plex renumbers on a rebuild) and the GUID fallback is all that
    stands between a renumbered item and a wrongly pruned row."""
    from datetime import datetime, timezone

    from autoposter.scheduler.prune import PruneCandidate

    candidate = PruneCandidate(
        id=1, rating_key="9", kind="movie", library="Movies", title="A Movie",
        parent_id=None, tmdb_id=1, tvdb_id=2, imdb_id="tt3", year=1999,
        season_number=None, episode_number=None, logo_upload_key=None,
        updated_at=datetime.now(timezone.utc),
    )

    intent = intent_for(candidate)

    assert (intent.tmdb_id, intent.tvdb_id, intent.imdb_id) == (1, 2, "tt3")
    assert intent.rating_key == "9"
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

    scan = await find_prunable(session, FakePlex(live={"100"}))

    assert scan.total == 2
    assert scan.prunable == []


async def test_an_applied_retire_deletes_the_row(session):
    item = await _add_item(session, "11")
    item_id = item.id
    scan = await find_prunable(session, FakePlex(live=set()))

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == ["11"]
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
    scan = await find_prunable(session, FakePlex(live=set()))

    await retire(session, scan.prunable)
    await session.commit()

    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert (event.source, event.event_type) == (PRUNE_SOURCE, PRUNE_EVENT)
    assert event.payload["rating_key"] == "11"
    assert event.payload["title"] == "A Movie"
    assert event.payload["library"] == "Movies"
    assert event.payload["kind"] == "movie"
    assert event.payload["tmdb_id"] == 5
    assert event.payload["imdb_id"] == "tt9"
    assert event.payload["year"] == 2001
    assert event.payload["render_count"] == 1
    assert event.payload["logo_upload_key"] == "upload://abc123"
    assert "no Plex item" in event.outcome


async def test_every_row_of_a_pruned_family_gets_its_own_audit_row(session):
    """The cascade is silent. Deleting the show first would remove its season
    and episode with no record that they had ever been there, which is why
    ``find_prunable`` hands them over deepest-first."""
    show = await _add_item(session, "100", kind="show")
    season = await _add_item(session, "110", kind="season", parent=show)
    await _add_item(session, "111", kind="episode", parent=season)
    scan = await find_prunable(session, FakePlex(live=set()))

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == ["111", "110", "100"]
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
    scan = await find_prunable(session, FakePlex(live=set()))

    await retire(session, scan.prunable)
    await session.commit()

    session.expire_all()
    assert (await session.execute(select(Render))).scalars().all() == []
    assert (await session.execute(select(ItemFacts))).scalars().all() == []


async def test_a_row_re_upserted_under_the_pass_survives_and_is_counted_skipped(session):
    """The concurrency guard, exercised through the actual writer.

    A worker can resolve and re-upsert a row between this sweep's probe and its
    delete -- the pass is minutes long and the pool never stops. The delete is
    keyed on ``(id, updated_at)`` and the upsert's ON CONFLICT arm stamps
    ``updated_at=now()``, so such a row no longer matches and is left exactly
    as the worker just wrote it. Each write is its own committed transaction,
    which is what makes the two ``now()`` values differ; nothing here asserts
    on a timestamp or a duration.
    """
    item = await _add_item(session, "11", title="Old Title")
    item_id = item.id
    scan = await find_prunable(session, FakePlex(live=set()))
    assert [c.rating_key for c in scan.prunable] == ["11"]

    resolved = ResolvedItem(
        rating_key="11", library="Movies", kind="movie", title="New Title",
        year=2001, season_number=None, episode_number=None,
        root_folder="New Title (2001)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=None, tvdb_id=None, imdb_id=None,
    )
    await _upsert_media_item(session, resolved)
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
    scan = await find_prunable(session, FakePlex(live=set()))
    assert [c.rating_key for c in scan.prunable] == ["111", "110", "100"]

    # parent_rating_key so the upsert keeps the episode under its season:
    # _upsert_media_item resolves parent_id from it, and without it the row
    # would come back detached and out of the cascade's reach, which is the
    # very thing this test needs to stay in it.
    resolved = ResolvedItem(
        rating_key="111", library="TV Shows", kind="episode", title="New Title",
        year=2001, season_number=1, episode_number=3,
        root_folder="A Show (2001)", file_path="/mnt/Media/TV/x.mkv",
        art_url=None, tmdb_id=None, tvdb_id=None, imdb_id=None,
        parent_rating_key="110",
    )
    await _upsert_media_item(session, resolved)
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
    await _add_item(session, "111", kind="episode", parent=season)
    movie = await _add_item(session, "11", title="A Movie")
    movie_id = movie.id
    scan = await find_prunable(session, FakePlex(live=set()))

    # parent_rating_key so the upsert keeps the episode under its season:
    # _upsert_media_item resolves parent_id from it, and without it the row
    # would come back detached and out of the cascade's reach, which is the
    # very thing this test needs to stay in it.
    resolved = ResolvedItem(
        rating_key="111", library="TV Shows", kind="episode", title="New Title",
        year=2001, season_number=1, episode_number=3,
        root_folder="A Show (2001)", file_path="/mnt/Media/TV/x.mkv",
        art_url=None, tmdb_id=None, tvdb_id=None, imdb_id=None,
        parent_rating_key="110",
    )
    await _upsert_media_item(session, resolved)
    await session.commit()

    outcome = await retire(session, scan.prunable)
    await session.commit()

    assert outcome.pruned == ["11"]
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
    one would park by construction -- nothing can resolve it any more."""
    for state in ("pending", "parked"):
        session.add(Job(
            kind="process_item",
            payload={"kind": "movie", "title": "Gone", "rating_key": "11"},
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


async def test_a_running_job_is_left_to_park_itself(session):
    """A claimed job is never interrupted anywhere in this project; one running
    against a pruned row simply parks, and the next applied pass dismisses it."""
    session.add(Job(
        kind="process_item",
        payload={"kind": "movie", "title": "Gone", "rating_key": "11"},
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
        payload={"kind": "movie", "title": "Still Here", "rating_key": "22"},
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


def _config(*, apply=False, max_prunes=500, max_prune_share=0.25, max_orphans=500):
    """Only what the job actually reads, the ``tests/test_scheduler_cleanup_job.py``
    ``_config`` pattern -- a SimpleNamespace keeps each test's intent on screen."""
    return SimpleNamespace(
        prune=SimpleNamespace(
            apply=apply, max_prunes=max_prunes, max_prune_share=max_prune_share
        ),
        cleanup=SimpleNamespace(max_orphans=max_orphans, max_orphan_share=0.25),
        scheduler=SimpleNamespace(prune_days=7),
    )


def _job(config, plex, *, healthy=True):
    return make_prune_job(ConfigHolder(config), lambda: plex, lambda: healthy)


class _ReupsertingPlex(FakePlex):
    """A probe that answers "gone" and then writes the row back, mid-pass.

    The race the ``(id, updated_at)`` key exists for: a worker re-resolves and
    re-upserts a row between this pass's probe and its delete. Doing the write
    from inside ``exists_many`` puts it where a fixture cannot reach it -- in
    the middle of one ``job.run`` -- so what these tests exercise is the job's
    own handling of a skip, not ``retire``'s.
    """

    def __init__(self, session, resolved, live=()):
        super().__init__(live)
        self._session = session
        self._resolved = resolved

    async def exists_many(self, intents):
        flags = await super().exists_many(intents)
        await _upsert_media_item(self._session, self._resolved)
        await self._session.commit()
        return flags


def _resolved_movie(rating_key, *, title="New Title"):
    """What a worker would write back for a movie it has just re-resolved."""
    return ResolvedItem(
        rating_key=rating_key, library="Movies", kind="movie", title=title,
        year=2001, season_number=None, episode_number=None,
        root_folder=f"{title} (2001)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=None, tvdb_id=None, imdb_id=None,
    )


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
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2
    assert (await session.execute(select(EventLog))).scalars().all() == []


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
        payload={"kind": "movie", "title": "Gone", "rating_key": "11"},
        dedupe_key="process_item:movie:title gone",
        state="parked",
    ))
    await session.commit()

    summary = await _job(_config(apply=True), FakePlex(live={"10"})).run(session)

    assert "pruned 1 of 2" in summary
    assert "dismissed 1" in summary
    assert "asset_cleanup" in summary
    session.expire_all()
    assert [i.rating_key for i in (await session.execute(select(MediaItem))).scalars()] == ["10"]
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

    plex = _ReupsertingPlex(session, _resolved_movie("11"))
    summary = await _job(_config(apply=True, max_orphans=1), plex).run(session)

    assert "pruned 1 of 2" in summary
    assert "1 asset director" in summary
    assert "WARNING" not in summary, (
        f"only the pruned row orphans a directory, and it is at the cap: {summary!r}"
    )


async def test_a_skipped_rows_queued_job_is_not_dismissed(session):
    """Why the disposal is keyed on ``outcome.pruned`` and not on the candidate
    list, said at the job level. The row was re-upserted between this pass's
    probe and its delete, so it survives -- and the work queued against it is
    still live work, which a dismissal keyed on the candidates would sweep away
    on the strength of an observation that stopped being true mid-pass."""
    await _add_item(session, "11", title="Old Title")
    session.add(Job(
        kind="process_item",
        payload={"kind": "movie", "title": "Gone", "rating_key": "11"},
        dedupe_key="process_item:movie:title gone",
        state="parked",
    ))
    await session.commit()

    plex = _ReupsertingPlex(session, _resolved_movie("11"))
    summary = await _job(_config(apply=True), plex).run(session)

    assert "pruned 0 of 1" in summary
    assert "dismissed 0" in summary
    assert "shielded by one that did" in summary
    session.expire_all()
    survivor = (await session.execute(select(MediaItem))).scalar_one()
    assert (survivor.rating_key, survivor.title) == ("11", "New Title")
    assert (await session.execute(select(Job))).scalar_one().state == "parked"
    assert (await session.execute(select(EventLog))).scalars().all() == [], (
        "nothing was deleted, so nothing may carry a deletion audit row"
    )


def test_the_job_name_is_in_the_hand_trigger_allowlist():
    """``SCHEDULED_JOB_NAMES`` is spelled out in api/routes.py rather than
    imported from the factories, so the two can drift and a job silently
    becomes untriggerable. This is the check that they have not."""
    from autoposter.api.routes import SCHEDULED_JOB_NAMES

    job = make_prune_job(ConfigHolder(_config()), lambda: FakePlex(), lambda: True)

    assert job.name in SCHEDULED_JOB_NAMES
