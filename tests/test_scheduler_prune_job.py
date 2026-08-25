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
from types import SimpleNamespace

from sqlalchemy import select, update

from autoposter.db.models import EventLog, ItemFacts, Job, MediaItem, Render
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import _upsert_media_item
from autoposter.scheduler.prune import (
    PRUNE_EVENT,
    PRUNE_SOURCE,
    dismiss_jobs_for,
    find_prunable,
    implausible_prune_count,
    intent_for,
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
    assert "100" not in {c.rating_key for c in scan.prunable}


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
