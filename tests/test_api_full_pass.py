"""POST /api/full-pass: enqueue the entire library for processing."""
import asyncio
import time
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Job, MediaItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x", fanart_apikey="x",
        webhook_secret="x", admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _item(**overrides):
    fields = {
        "rating_key": "rk-movie",
        "library": "Movies",
        "kind": "movie",
        "title": "A Movie",
        "tmdb_id": 100,
    }
    fields.update(overrides)
    return MediaItem(**fields)


def _one_of_each_kind():
    """A movie plus a show with one season and one episode -- the four
    media_items kinds a real library holds as separate rows."""
    return [
        _item(),
        _item(rating_key="rk-show", library="TV", kind="show",
              title="A Show", tmdb_id=None, tvdb_id=200),
        _item(rating_key="rk-season", library="TV", kind="season",
              title="A Show", tmdb_id=None, tvdb_id=200, season_number=1),
        _item(rating_key="rk-episode", library="TV", kind="episode",
              title="Pilot", tmdb_id=None, tvdb_id=200,
              season_number=1, episode_number=1),
    ]


async def test_full_pass_requires_a_session(client):
    response = await client.post("/api/full-pass")
    assert response.status_code == 401


async def test_full_pass_covers_every_kind_not_just_movies_and_shows(
    client, auth_headers, session
):
    """Seasons and episodes are their own rows and process_item does not
    cascade from a show to them (render/pipeline.py builds only
    ART_KINDS_FOR[intent.kind]), so a movie/show-only pass -- the shape of
    the drift sweep's filter -- would never rebuild a season poster or a
    title card."""
    session.add_all(_one_of_each_kind())
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"total": 4, "queued": 4, "skipped": 0}

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 4
    assert all(job.kind == "process_item" and job.state == "pending" for job in jobs)
    assert {job.payload["kind"] for job in jobs} == {"movie", "show", "season", "episode"}
    # The season and episode jobs carry their numbers, both in the payload
    # process_item will resolve and in the dedupe key that keeps them
    # distinct from their parent show's job.
    keys = {job.dedupe_key for job in jobs}
    assert "process_item:season:tvdb200:s01" in keys
    assert "process_item:episode:tvdb200:s01e01" in keys


async def test_second_trigger_reports_the_dedupe_instead_of_queueing_again(
    client, auth_headers, session
):
    """Double-clicking while the first pass is still pending must insert
    nothing -- uq_jobs_pending_dedupe -- and must say so, not claim to have
    queued the library twice."""
    session.add_all(_one_of_each_kind())
    await session.commit()

    first = await client.post("/api/full-pass", headers=auth_headers)
    second = await client.post("/api/full-pass", headers=auth_headers)

    assert first.json() == {"total": 4, "queued": 4, "skipped": 0}
    assert second.json() == {"total": 4, "queued": 0, "skipped": 4}

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 4


async def test_duplicate_dedupe_keys_within_one_pass_queue_once(
    client, auth_headers, session
):
    """The same movie in two libraries (a 4K copy) shares external ids and
    therefore a dedupe key; one pass must queue it once, exactly as two
    webhook events for it would."""
    session.add_all([
        _item(rating_key="rk-1080", library="Movies"),
        _item(rating_key="rk-4k", library="Movies 4K"),
    ])
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 2, "queued": 1, "skipped": 1}
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_a_done_job_does_not_block_a_new_pass(client, auth_headers, session):
    """The dedupe index is partial over pending rows only; a completed pass
    must not make the library untriggerable forever."""
    session.add(_item())
    session.add(Job(
        kind="process_item", payload={"kind": "movie", "title": "A Movie"},
        state="done", dedupe_key="process_item:movie:tmdb100",
    ))
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 1, "queued": 1, "skipped": 0}


async def test_an_empty_library_reports_zeroes(client, auth_headers):
    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 0, "queued": 0, "skipped": 0}


async def test_a_library_sized_pass_answers_inside_one_request(
    client, auth_headers, session
):
    """15,000 items -- the size of the library this exists for -- enqueued in
    one request. The bound is what makes this falsifiable: the set-based
    insert does this in about a second, while one enqueue() (a commit each)
    per item takes minutes and would hold the request open throughout."""
    await session.execute(
        insert(MediaItem),
        [
            {
                "rating_key": f"rk{i}", "library": "Movies", "kind": "movie",
                "title": f"Movie {i}", "tmdb_id": i + 1,
            }
            for i in range(15_000)
        ],
    )
    await session.commit()

    started = time.perf_counter()
    response = await client.post("/api/full-pass", headers=auth_headers)
    elapsed = time.perf_counter() - started

    assert response.json() == {"total": 15_000, "queued": 15_000, "skipped": 0}
    print(f"\nfull pass over 15,000 items answered in {elapsed:.2f}s")
    assert elapsed < 10.0, f"full pass took {elapsed:.2f}s for 15,000 items"


async def test_the_response_does_not_wait_on_the_webhook(
    app, client, auth_headers, session
):
    """The notification is fire-and-forget. A webhook taking its worst-case
    duration (31.5s on default retry config -- notify/dispatch.py) must not
    hold the response open; the fake's 2-second sleep stands in for that.
    The response must come back with the send still in flight, and the send
    must still run to completion afterwards carrying the real counts."""

    class _SlowNotifier:
        def __init__(self):
            self.calls = []
            self.done = asyncio.Event()

        async def send(self, event, summary, detail):
            await asyncio.sleep(2)
            self.calls.append((event, summary, detail))
            self.done.set()
            return True

    notifier = _SlowNotifier()
    app.state.notifier = notifier
    session.add_all(_one_of_each_kind())
    await session.commit()

    started = time.perf_counter()
    response = await client.post("/api/full-pass", headers=auth_headers)
    elapsed = time.perf_counter() - started

    assert response.json() == {"total": 4, "queued": 4, "skipped": 0}
    assert not notifier.done.is_set(), (
        "the endpoint waited for the webhook before answering"
    )
    assert elapsed < 2.0, f"response took {elapsed:.2f}s -- it waited on the webhook"

    await asyncio.wait_for(notifier.done.wait(), timeout=10)
    event, _summary, detail = notifier.calls[0]
    assert event == "full_pass_enqueued"
    assert detail == {"total": 4, "queued": 4, "skipped": 0}
