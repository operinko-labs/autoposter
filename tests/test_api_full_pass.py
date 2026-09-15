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
from autoposter.db.models import Job, MediaItem, MediaItemServerRef, MetadataWrite, RenderDelivery, Run
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, PLEX_CAPS

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
    return fields


async def _add_items(session, *items: dict) -> list[int]:
    """Bulk-insert ``media_items`` rows plus their Plex refs from ``_item``'s
    ``{"rating_key": ..., "library": ..., "kind": ..., ...}`` field dicts.

    Two set-based statements regardless of how many rows -- the shape the
    15,000-row test below needs: one awaited ``seed_media_item`` (a commit
    each) per item would hold this suite open for minutes.
    """
    media_rows = [
        {
            "identity_key": "%s:legacy:plex:%s" % (fields["kind"], fields["rating_key"]),
            **{k: v for k, v in fields.items() if k != "rating_key"},
        }
        for fields in items
    ]
    ids = (
        await session.execute(insert(MediaItem).returning(MediaItem.id), media_rows)
    ).scalars().all()
    await session.execute(
        insert(MediaItemServerRef),
        [
            {
                "item_id": item_id, "server": "plex",
                "native_id": fields["rating_key"], "library": fields["library"],
            }
            for item_id, fields in zip(ids, items, strict=True)
        ],
    )
    return list(ids)


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
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"total": 4, "queued": 4, "skipped": 0, "presence": {}}

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


async def test_every_queued_intent_carries_its_rows_plex_rating_key(
    client, auth_headers, session
):
    """Without it the ~14,000 jobs of a post-adoption pass mostly cannot
    resolve: adoption stored each season's and episode's OWN external ids, and
    the GUID search reads an episode intent's ids as the *series'* -- so they
    match nothing and the job retries towards parking, or match an unrelated
    item carrying the same number. The rating key is that item's exact Plex
    identity and is already in the row; it just was not being passed on.

    Asserted against the queued payload rather than the intent, because that
    payload is what ``queue/worker.py`` rebuilds the intent from.
    """
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    await client.post("/api/full-pass", headers=auth_headers)

    jobs = (await session.execute(select(Job))).scalars().all()
    assert {job.payload["kind"]: job.payload["refs"]["plex"] for job in jobs} == {
        "movie": "rk-movie",
        "show": "rk-show",
        "season": "rk-season",
        "episode": "rk-episode",
    }
    # And the queue keys are untouched by it, so jobs queued by the previous
    # release are still deduplicated against.
    assert "process_item:episode:tvdb200:s01e01" in {job.dedupe_key for job in jobs}


async def test_second_trigger_reports_the_dedupe_instead_of_queueing_again(
    client, auth_headers, session
):
    """Double-clicking while the first pass is still pending must insert
    nothing -- uq_jobs_pending_dedupe -- and must say so, not claim to have
    queued the library twice."""
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    first = await client.post("/api/full-pass", headers=auth_headers)
    second = await client.post("/api/full-pass", headers=auth_headers)

    assert first.json() == {"total": 4, "queued": 4, "skipped": 0, "presence": {}}
    assert second.json() == {"total": 4, "queued": 0, "skipped": 4, "presence": {}}

    jobs = (await session.execute(select(Job))).scalars().all()
    assert len(jobs) == 4


async def test_duplicate_dedupe_keys_within_one_pass_queue_once(
    client, auth_headers, session
):
    """The same movie in two libraries (a 4K copy) shares external ids and
    therefore a dedupe key; one pass must queue it once, exactly as two
    webhook events for it would."""
    await _add_items(
        session,
        _item(rating_key="rk-1080", library="Movies"),
        _item(rating_key="rk-4k", library="Movies 4K"),
    )
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 2, "queued": 1, "skipped": 1, "presence": {}}
    assert len((await session.execute(select(Job))).scalars().all()) == 1


async def test_a_done_job_does_not_block_a_new_pass(client, auth_headers, session):
    """The dedupe index is partial over pending rows only; a completed pass
    must not make the library untriggerable forever."""
    await _add_items(session, _item())
    session.add(Job(
        kind="process_item", payload={"kind": "movie", "title": "A Movie"},
        state="done", dedupe_key="process_item:movie:tmdb100",
    ))
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 1, "queued": 1, "skipped": 0, "presence": {}}


async def test_an_empty_library_reports_zeroes(client, auth_headers):
    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.json() == {"total": 0, "queued": 0, "skipped": 0, "presence": {}}


async def test_a_library_sized_pass_answers_inside_one_request(
    client, auth_headers, session
):
    """15,000 items -- the size of the library this exists for -- enqueued in
    one request. The bound is what makes this falsifiable: the set-based
    insert does this in about a second, while one enqueue() (a commit each)
    per item takes minutes and would hold the request open throughout."""
    await _add_items(
        session,
        *[
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

    assert response.json() == {"total": 15_000, "queued": 15_000, "skipped": 0, "presence": {}}
    print(f"\nfull pass over 15,000 items answered in {elapsed:.2f}s")
    # 60 s, not 10: the bound separates one set-based insert (seconds) from
    # a commit per item (minutes), and that is all it needs to separate. At
    # 10 s it failed main once when two runs shared the runner (10.84 s,
    # run 358) -- a wall-clock bound in CI has to leave room for a loaded
    # host without losing what it proves.
    assert elapsed < 60.0, f"full pass took {elapsed:.2f}s for 15,000 items"


async def test_the_response_does_not_wait_on_the_webhook(
    app, client, auth_headers, session
):
    """The notification is fire-and-forget. A webhook taking its worst-case
    duration (31.5s against an ordinary target, 50s against one that is
    rate-limiting us, on default retry config -- notify/dispatch.py) must not
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
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    started = time.perf_counter()
    response = await client.post("/api/full-pass", headers=auth_headers)
    elapsed = time.perf_counter() - started

    assert response.json() == {"total": 4, "queued": 4, "skipped": 0, "presence": {}}
    assert not notifier.done.is_set(), (
        "the endpoint waited for the webhook before answering"
    )
    assert elapsed < 2.0, f"response took {elapsed:.2f}s -- it waited on the webhook"

    await asyncio.wait_for(notifier.done.wait(), timeout=10)
    event, _summary, detail = notifier.calls[0]
    assert event == "full_pass_enqueued"
    assert detail == {"total": 4, "queued": 4, "skipped": 0}


async def test_the_full_pass_opens_a_run_row(client, auth_headers, session):
    """A full pass is a run. Before this it was the one execution in the
    tree that persisted nothing at all -- it enqueued, notified, returned three
    numbers and left no row anywhere."""
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    response = await client.post("/api/full-pass", headers=auth_headers)
    assert response.status_code == 200

    row = (await session.execute(select(Run))).scalar_one()
    assert (row.kind, row.name, row.status) == ("full_pass", "full_pass", "running")
    assert row.finished_at is None
    assert row.detail is None


async def test_every_job_the_pass_enqueued_is_inside_its_own_window(client, auth_headers, session):
    """The property the whole of window attribution rests on, and it is not an
    accident: the run row is inserted in the SAME session and transaction as
    enqueue_batch's inserts, so `runs.started_at` and `jobs.created_at` both
    resolve to that transaction's timestamp. Open the row in a separate
    transaction and every job of the pass could be created microseconds before
    its own run began."""
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    await client.post("/api/full-pass", headers=auth_headers)

    run = (await session.execute(select(Run))).scalar_one()
    jobs = (await session.execute(select(Job))).scalars().all()
    assert jobs
    assert all(job.created_at >= run.started_at for job in jobs)


async def test_the_response_body_is_unchanged(client, auth_headers, session):
    """The run row is additive. The three numbers the button has always shown
    are what the operator reads, and they still come from enqueue_batch's own
    count rather than from anything this row knows."""
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    body = (await client.post("/api/full-pass", headers=auth_headers)).json()

    assert set(body) == {"total", "queued", "skipped", "presence"}
    assert body["total"] == body["queued"] + body["skipped"]


async def test_a_second_press_opens_a_second_run(client, auth_headers, session):
    """Pressing twice is not idempotent -- the dashboard already says so, and
    enqueue_batch's ON CONFLICT means the second press queues almost nothing.
    A second row is still opened, and the two windows overlap: both count the
    same drain. Recorded here as the decided behaviour rather than left to be
    discovered, because the alternative (reusing an open row) leaks -- a row
    nothing ever closes would suppress every future full pass's history."""
    await _add_items(session, *_one_of_each_kind())
    await session.commit()

    await client.post("/api/full-pass", headers=auth_headers)
    await client.post("/api/full-pass", headers=auth_headers)

    rows = (await session.execute(select(Run).order_by(Run.id))).scalars().all()
    assert len(rows) == 2
    assert all(row.status == "running" for row in rows)


async def test_a_full_pass_marks_a_library_jellyfin_does_not_carry_absent(
    client, auth_headers, session
):
    """spec §6: a dual registry with one library absent on Jellyfin records
    `absent` once per library and never resolves those items there."""
    from sqlalchemy import select
    from autoposter.render import pipeline
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    photo = await seed_media_item(session, "rk-a", library="Photos", title="P")
    render = await pipeline._get_or_create_render(session, photo, "poster", "/a/p.jpg")
    render.status = "rendered"
    await seed_media_item(session, "rk-b", library="Movies", title="M")
    await session.commit()

    client._transport.app.state.servers = Servers({
        "plex": FakeMediaServer(name="plex", capabilities=PLEX_CAPS, libraries={"Movies", "Photos"}),
        "jellyfin": FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"}),
    })

    body = (await client.post("/api/full-pass", headers=auth_headers)).json()

    # Counted per table (review minor 2): one ITEM's metadata row and one
    # RENDER's delivery row, not an unreadable `2`.
    assert body["presence"]["jellyfin"] == {
        "metadata": {"absent": 1, "rearmed": 0},
        "artwork": {"absent": 1, "rearmed": 0},
    }
    assert body["presence"]["plex"] == {
        "metadata": {"absent": 0, "rearmed": 0},
        "artwork": {"absent": 0, "rearmed": 0},
    }
    rows = (await session.execute(
        select(MetadataWrite.server, MetadataWrite.status, MetadataWrite.item_id)
    )).all()
    assert rows == [("jellyfin", "absent", photo.id)]
    delivery = (await session.execute(
        select(RenderDelivery.server, RenderDelivery.status)
    )).one()
    assert delivery == ("jellyfin", "absent")


async def test_a_full_pass_survives_a_server_that_cannot_list_its_libraries(
    client, auth_headers, session
):
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    await seed_media_item(session, "rk-c", library="Movies", title="M")
    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

    async def boom():
        raise RuntimeError("down")

    jf.library_names = boom
    client._transport.app.state.servers = Servers({"jellyfin": jf})

    response = await client.post("/api/full-pass", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["presence"] == {}
    assert response.json()["queued"] == 1


async def test_a_plex_only_pass_whose_sections_all_match_changes_nothing(
    client, auth_headers, session
):
    """Review I5/T5: the binding constraint "Plex-only deployments see no
    behaviour change beyond recorded rows". Every existing presence test here
    runs with an empty registry and asserts `presence == {}`, which exercises
    neither Plex's own `library_names()` nor the absent path on the identity
    server -- so a Plex-only deployment's actual first pass was untested."""
    from autoposter.render import pipeline
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    movie = await seed_media_item(session, "rk-p1", library="Movies", title="M")
    render = await pipeline._get_or_create_render(session, movie, "poster", "/a/p.jpg")
    render.status = "rendered"
    await seed_media_item(session, "rk-p2", library="TV Shows", kind="show", title="S")
    await session.commit()

    client._transport.app.state.servers = Servers({
        "plex": FakeMediaServer(
            name="plex", capabilities=PLEX_CAPS, libraries={"Movies", "TV Shows"},
        ),
    })

    body = (await client.post("/api/full-pass", headers=auth_headers)).json()

    assert body["presence"] == {"plex": {
        "metadata": {"absent": 0, "rearmed": 0},
        "artwork": {"absent": 0, "rearmed": 0},
    }}
    assert body["queued"] == 2
    # Not one row in either table, and the render's roll-up untouched: the
    # pass recorded nothing it did not have to.
    assert (await session.execute(select(MetadataWrite))).scalars().all() == []
    assert (await session.execute(select(RenderDelivery))).scalars().all() == []


async def test_an_excluded_plex_section_becomes_absent_on_plex_itself(
    client, auth_headers, session
):
    """The other half of the same decision: a section the operator excluded
    after ingest no longer carries the item under the name the row records, so
    spec §1's rule applies to the identity server like any other."""
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    retired = await seed_media_item(session, "rk-p3", library="Home Videos", title="H")
    await seed_media_item(session, "rk-p4", library="Movies", title="M")
    await session.commit()

    client._transport.app.state.servers = Servers({
        "plex": FakeMediaServer(name="plex", capabilities=PLEX_CAPS, libraries={"Movies"}),
    })

    body = (await client.post("/api/full-pass", headers=auth_headers)).json()

    assert body["presence"]["plex"]["metadata"] == {"absent": 1, "rearmed": 0}
    rows = (await session.execute(
        select(MetadataWrite.server, MetadataWrite.status, MetadataWrite.item_id)
    )).all()
    assert rows == [("plex", "absent", retired.id)]


async def test_a_full_pass_never_rebuilds_the_jellyfin_item_index(
    client, auth_headers, session
):
    """Review I3: the pass invalidates every index and then asks each server
    which libraries it carries. Answering that through `LibraryIndex.rebuild`
    put the whole `/Items?recursive=true` enumeration -- every folder, the
    entire library -- inside the request handler, and (because Plex's stamps
    have already been issued) with a database transaction open throughout.
    The folder list is all presence needs."""
    import httpx

    from autoposter.jellyfin.client import JellyfinApi, JellyfinClient
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    called: list[str] = []

    async def handler(request):
        called.append(request.url.path)
        if request.url.path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[
                {"Name": "Movies", "CollectionType": "movies",
                 "Locations": ["/media/Movies"], "ItemId": "lib1"},
            ])
        return httpx.Response(200, json={"Items": []})

    await seed_media_item(session, "rk-jf", library="Movies", title="M")
    await session.commit()

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    jellyfin = JellyfinClient(
        JellyfinApi(http, "https://jf.example", api_key="k", version="v"),
        excluded_libraries=[], library_map={}, replace_thumb_with_backdrop=False,
    )
    client._transport.app.state.servers = Servers({"jellyfin": jellyfin})

    async with http:
        body = (await client.post("/api/full-pass", headers=auth_headers)).json()

    assert body["presence"]["jellyfin"] == {
        "metadata": {"absent": 0, "rearmed": 0},
        "artwork": {"absent": 0, "rearmed": 0},
    }
    assert called == ["/Library/VirtualFolders"], f"the pass listed items: {called}"


async def test_a_reappearing_library_is_re_armed_through_the_endpoint(
    client, auth_headers, session
):
    """A library that reappears must flip its rows back to `pending` -- a
    binding constraint of the presence rules, and one proven only at the
    `apply_presence` unit level. Two posts through the real endpoint, with the
    double's libraries changing in between."""
    from autoposter.render import pipeline
    from autoposter.servers.registry import Servers
    from conftest import seed_media_item

    photo = await seed_media_item(session, "rk-r1", library="Photos", title="P")
    render = await pipeline._get_or_create_render(session, photo, "poster", "/a/p.jpg")
    render.status = "rendered"
    await session.commit()

    jf = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"})
    client._transport.app.state.servers = Servers({"jellyfin": jf})

    first = (await client.post("/api/full-pass", headers=auth_headers)).json()
    assert first["presence"]["jellyfin"]["artwork"] == {"absent": 1, "rearmed": 0}

    # The library map corrected, or the library mounted at last.
    jf.libraries = {"Movies", "Photos"}
    second = (await client.post("/api/full-pass", headers=auth_headers)).json()

    assert second["presence"]["jellyfin"] == {
        "metadata": {"absent": 0, "rearmed": 1},
        "artwork": {"absent": 0, "rearmed": 1},
    }
    delivery = (await session.execute(
        select(RenderDelivery.status, RenderDelivery.detail, RenderDelivery.next_attempt_at)
    )).one()
    assert delivery.status == "pending" and delivery.detail is None
    assert delivery.next_attempt_at is not None, "a re-armed row is due for the ordinary retry"
    metadata = (await session.execute(select(MetadataWrite.status))).scalar_one()
    assert metadata == "pending"
