"""``enqueue_unknown_items``: the safety net for a missed webhook.

Independent of Radarr and Sonarr -- this only cares whether Plex has an item
this service has never recorded a ``media_items`` row for. Uses the real
session fixture and the real ``enqueue`` (not a fake queue), per the phase
constraints: the whole point is exercising the real dedupe behaviour.
"""
from sqlalchemy import select

from autoposter.arr.sync import enqueue_unknown_items
from autoposter.db.models import Job, MediaItem


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


async def test_an_item_with_a_media_items_row_is_not_enqueued(session):
    session.add(MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune"))
    await session.commit()

    items = [FakeItem("1", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie")

    assert count == 0
    assert await _pending_jobs(session) == []


async def test_an_item_with_no_media_items_row_is_enqueued(session):
    items = [FakeItem("2", "Severance", ["tvdb://371980"])]
    count = await enqueue_unknown_items(session, items, "show")

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
    count = await enqueue_unknown_items(session, items, "movie", batch_size=2)

    assert count == 2
    assert len(await _pending_jobs(session)) == 2


async def test_running_twice_does_not_double_enqueue(session):
    items = [FakeItem("20", "Only", ["tmdb://20"])]

    first = await enqueue_unknown_items(session, items, "movie")
    second = await enqueue_unknown_items(session, items, "movie")

    assert first == 1
    assert second == 0
    assert len(await _pending_jobs(session)) == 1


async def test_the_returned_count_matches_the_number_of_rows_still_missing(session):
    session.add(MediaItem(rating_key="30", library="Movies", kind="movie", title="Known"))
    await session.commit()

    items = [
        FakeItem("30", "Known", ["tmdb://30"]),
        FakeItem("31", "Unknown One", ["tmdb://31"]),
        FakeItem("32", "Unknown Two", ["tmdb://32"]),
    ]
    count = await enqueue_unknown_items(session, items, "movie")

    assert count == 2
    assert len(await _pending_jobs(session)) == 2


async def test_an_empty_section_enqueues_nothing(session):
    count = await enqueue_unknown_items(session, [], "movie")

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
