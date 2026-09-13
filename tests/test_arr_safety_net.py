"""``enqueue_unknown_items``: the safety net for a missed webhook.

Independent of Radarr and Sonarr -- this only cares whether Plex has an item
this service has never recorded a ``media_items`` row for. Uses the real
session fixture and the real ``enqueue`` (not a fake queue), per the phase
constraints: the whole point is exercising the real dedupe behaviour.
"""
import pytest
from sqlalchemy import select

from autoposter.arr.sync import enqueue_unknown_items
from autoposter.db.models import Job, MediaItem, MediaItemServerRef


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, rating_key, title, guids=()):
        self.ratingKey = rating_key
        self.title = title
        self.guids = [FakeGuid(g) for g in guids]
        self.year = 2021


async def _pending_jobs(session):
    return (await session.execute(select(Job).where(Job.state == "pending"))).scalars().all()


async def _known_item(session, rating_key, **columns):
    """One committed ``media_items`` row with its own Plex ref -- a row
    ``enqueue_unknown_items``'s anti-join (now over ``media_item_server_refs``)
    must find as already known.

    ``identity_key`` is synthesized from the rating key rather than computed
    from any external id a test also passes in: one fixture below builds two
    rows sharing one ``tmdb_id`` on purpose, an ambiguous re-key state a real
    identity-keyed upsert can no longer produce (``identity_key`` is UNIQUE).
    """
    fields = dict(library="Movies", kind="movie", title="Item")
    fields.update(columns)
    item = MediaItem(identity_key=f"{fields['kind']}:legacy:plex:{rating_key}", **fields)
    session.add(item)
    await session.flush()
    session.add(MediaItemServerRef(
        item_id=item.id, server="plex", native_id=rating_key, library=fields["library"],
    ))
    await session.commit()
    return item


async def test_an_item_with_a_media_items_row_is_not_enqueued(session):
    await _known_item(session, "1", title="Dune")

    items = [FakeItem("1", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie", "Movies")

    assert count == 0
    assert await _pending_jobs(session) == []


async def test_an_item_with_no_media_items_row_is_enqueued(session):
    items = [FakeItem("2", "Severance", ["tvdb://371980"])]
    count = await enqueue_unknown_items(session, items, "show", "Shows")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert len(jobs) == 1
    assert jobs[0].kind == "process_item"
    assert jobs[0].payload["title"] == "Severance"
    assert jobs[0].payload["tvdb_id"] == 371980


async def test_batch_size_caps_how_many_are_enqueued(session):
    items = [
        FakeItem("10", "A", ["tmdb://10"]),
        FakeItem("11", "B", ["tmdb://11"]),
        FakeItem("12", "C", ["tmdb://12"]),
    ]
    count = await enqueue_unknown_items(session, items, "movie", "Movies", batch_size=2)

    assert count == 2
    assert len(await _pending_jobs(session)) == 2


async def test_running_twice_does_not_double_enqueue(session):
    items = [FakeItem("20", "Only", ["tmdb://20"])]

    first = await enqueue_unknown_items(session, items, "movie", "Movies")
    second = await enqueue_unknown_items(session, items, "movie", "Movies")

    assert first == 1
    assert second == 0
    assert len(await _pending_jobs(session)) == 1


async def test_the_returned_count_matches_the_number_of_rows_still_missing(session):
    await _known_item(session, "30", title="Known")

    items = [
        FakeItem("30", "Known", ["tmdb://30"]),
        FakeItem("31", "Unknown One", ["tmdb://31"]),
        FakeItem("32", "Unknown Two", ["tmdb://32"]),
    ]
    count = await enqueue_unknown_items(session, items, "movie", "Movies")

    assert count == 2
    assert len(await _pending_jobs(session)) == 2


async def test_the_enqueued_payload_carries_the_items_rating_key(session):
    """Bamse-class regression: the resolver tries ``rating_key`` before any
    agent-crosswalk (``getGuid``) lookup, but only if discovery puts one in
    the payload. Without it, an item whose guids the crosswalk cannot
    resolve -- even guids read straight off the item itself -- stays
    unresolvable forever. See test_plex.py for the resolution-side pin.
    """
    items = [FakeItem("40", "Bamse", ["tmdb://55645", "tvdb://358385"])]
    count = await enqueue_unknown_items(session, items, "show", "Shows")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["refs"]["plex"] == "40"


async def test_an_empty_section_enqueues_nothing(session):
    count = await enqueue_unknown_items(session, [], "movie", "Movies")

    assert count == 0
    assert await _pending_jobs(session) == []


def test_the_id_coercion_helper_is_shared_with_the_plex_client():
    """``as_int`` had a verbatim duplicate here and in ``plex.client``. One
    definition, imported -- two copies of the same coercion would drift.
    """
    from autoposter.arr import sync
    from autoposter.plex.client import as_int

    assert sync.as_int is as_int
    assert not hasattr(sync, "_as_int")


async def test_an_identity_already_stored_under_another_key_is_enqueued_from_that_row(
    session,
):
    """Discovery's anti-join is on the KEY, so a re-matched item's live key is
    absent from ``media_items`` (only its stale key is there), the item reads
    as "unknown", and it is enqueued with the LIVE key -- which
    ``_fetch_by_rating_key_sync`` accepts, so no fork occurs, no warning
    fires, and ``_upsert_media_item`` inserts a twin. Running per section over
    whole libraries, that made this the largest twin producer in the tree.

    Enqueuing the intent built from the EXISTING ROW instead turns it from a
    producer into a repairer: the job forks, the pipeline's identity re-key
    fires, and the item is fixed rather than duplicated.
    """
    await _known_item(session, "900", title="Old Title", tmdb_id=438631, year=2021)

    items = [FakeItem("901", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie", "Movies")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["refs"]["plex"] == "900", (
        "discovery enqueued the live key and would have minted a twin"
    )
    assert jobs[0].payload["title"] == "Old Title"


async def test_two_rows_for_one_identity_fall_back_to_the_live_key(session):
    """Ambiguity picks no side. Two rows carrying one identity is the twin
    merge's pair; enqueuing either one's key here would choose at random, so
    discovery does exactly what it does today and leaves the pair alone."""
    for key in ("900", "902"):
        await _known_item(session, key, title="Old Title", tmdb_id=438631, year=2021)

    items = [FakeItem("901", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie", "Movies")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["refs"]["plex"] == "901"
    assert jobs[0].payload["title"] == "Dune"


@pytest.mark.parametrize(
    "stale_library, sweep_library",
    [
        ("Movies", "Movies 4K"),
        ("Movies 4K", "Movies"),
    ],
)
async def test_a_stale_row_in_a_different_library_is_not_the_guess(
    session, stale_library, sweep_library
):
    """The 4K/HD dual-library population this phase treats as first-class:
    the same external ids can legitimately carry two rows, one per library
    (``test_a_cross_library_match_is_not_a_re_key`` in
    ``tests/test_pipeline_rekey.py`` pins the pipeline's own half of this).
    A library-blind guess here matched the OTHER library's row and enqueued
    ITS intent -- so the discovered item was never enqueued at all, forever,
    and the wrong item was redundantly re-rendered on every sweep instead.

    The guess predicate must be exactly as wide as the
    pipeline's own re-key predicate -- kind + library + coordinates + id
    intersection -- so it takes the library being swept, not just the kind.
    Run both ways round: the stale row in "Movies" while sweeping "Movies 4K"
    must still enqueue the 4K item's own intent, and the reverse must still
    enqueue the Movies item's own intent -- neither library's guess may
    cross into the other's.
    """
    await _known_item(session, "900", library=stale_library, title="Old Title", tmdb_id=438631, year=2021)

    items = [FakeItem("901", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie", sweep_library)

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["refs"]["plex"] == "901", (
        "the guess crossed into another library and enqueued that row's "
        "intent instead of the discovered item's own"
    )
    assert jobs[0].payload["title"] == "Dune"
