"""POST/GET/DELETE /api/servers/{name}/catch-up and POST .../retry-failed (spec §5)."""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from types import SimpleNamespace

from autoposter import deliveries
from autoposter.api.auth import hash_password
from autoposter.api.servers import NOT_A_SERVER
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ItemFacts, MetadataWrite, RenderDelivery, Run
from autoposter.render import pipeline
from autoposter.servers.registry import Servers

from conftest import seed_media_item
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _app(session_factory, *, scheduler_enabled: bool = True):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    config = load_config(EXAMPLE)
    # `upload_to_jellyfin` defaults to OFF (tests/test_catchup.py's own
    # `catch_up_config` fixture carries the same override, for the same
    # reason): without it `start_catch_up`'s artwork gate returns
    # before a single RenderDelivery row is ever created, and the tests below
    # that assert one exists would find none.
    config.badges.upload_to_jellyfin = True
    config.scheduler.enabled = scheduler_enabled
    app = create_app(config, session_factory, secrets)
    app.state.servers = Servers({
        "jellyfin": FakeMediaServer(
            name="jellyfin", capabilities=JELLYFIN_CAPS, libraries={"Movies"}
        )
    })
    app.state.server_health = {}
    return app


@pytest_asyncio.fixture
async def client(session_factory):
    transport = ASGITransport(app=_app(session_factory))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def scheduler_off_client(session_factory):
    """The same app with `scheduler.enabled` false -- a supported deployment,
    and the one with no drain job registered."""
    transport = ASGITransport(app=_app(session_factory, scheduler_enabled=False))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    token = (await client.post("/api/login", json={"password": PASSWORD})).json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def _rendered_item(session, native="x1"):
    item = await seed_media_item(session, native, library="Movies", title=native)
    # A catch-up arms a metadata row only for an item this service has
    # something to write for (tests/test_catchup.py's
    # own `_item_with_render` precedent) -- without this row every test below
    # that expects a MetadataWrite finds none.
    session.add(ItemFacts(item_id=item.id))
    render = await pipeline._get_or_create_render(session, item, "poster", "/a/x.jpg")
    render.status = "rendered"
    render.badge_fingerprint = "fp1"
    await session.commit()
    return item, render


async def test_catch_up_requires_a_session(client):
    assert (await client.post("/api/servers/jellyfin/catch-up")).status_code == 401


async def test_starting_a_catch_up_opens_a_run_and_marks_the_backlog(
    client, auth_headers, session
):
    item, render = await _rendered_item(session)

    body = (await client.post(
        "/api/servers/jellyfin/catch-up", headers=auth_headers,
        json={"cadence_seconds": 90},
    )).json()

    assert body["server"] == "jellyfin" and body["cadence_seconds"] == 90
    run = (await session.execute(select(Run).where(Run.id == body["run_id"]))).scalar_one()
    assert run.kind == "catch_up" and run.server == "jellyfin"
    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "pending"
    assert (await session.execute(select(MetadataWrite.status))).scalar_one() == "pending"


async def test_a_second_catch_up_is_409_with_the_sentence(client, auth_headers, session):
    await _rendered_item(session)
    await client.post("/api/servers/jellyfin/catch-up", headers=auth_headers, json={})

    response = await client.post(
        "/api/servers/jellyfin/catch-up", headers=auth_headers, json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "a catch-up for jellyfin is already in flight"


async def test_a_catch_up_on_a_down_server_is_409(client, auth_headers, session):
    client._transport.app.state.server_health = {"jellyfin": SimpleNamespace(healthy=False)}

    response = await client.post(
        "/api/servers/jellyfin/catch-up", headers=auth_headers, json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "jellyfin is not reachable right now; try again once it is back"
    )


async def test_progress_is_served_and_is_null_before_the_first_run(
    client, auth_headers, session
):
    assert (await client.get(
        "/api/servers/jellyfin/catch-up", headers=auth_headers
    )).json() == {"run": None}

    await _rendered_item(session)
    await client.post("/api/servers/jellyfin/catch-up", headers=auth_headers, json={})

    body = (await client.get("/api/servers/jellyfin/catch-up", headers=auth_headers)).json()
    assert body["server"] == "jellyfin" and body["status"] == "running"
    assert body["due"] == 2 and body["done"] == 0 and body["failed"] == 0


async def test_cancelling_closes_the_run_and_is_409_when_there_is_none(
    client, auth_headers, session
):
    await _rendered_item(session)
    await client.post("/api/servers/jellyfin/catch-up", headers=auth_headers, json={})

    body = (await client.delete(
        "/api/servers/jellyfin/catch-up", headers=auth_headers
    )).json()
    assert body["removed"] == 2 and body["restored"] == 0

    again = await client.delete("/api/servers/jellyfin/catch-up", headers=auth_headers)
    assert again.status_code == 409
    assert again.json()["detail"] == "no catch-up for jellyfin is in flight"


async def test_retry_failed_re_arms_that_servers_failed_rows(client, auth_headers, session):
    item, render = await _rendered_item(session)
    await deliveries.record(session, render.id, "jellyfin", "failed", detail="error: X")
    await deliveries.record_metadata(session, item.id, "jellyfin", "failed", detail="error: X")
    await session.commit()

    body = (await client.post(
        "/api/servers/jellyfin/retry-failed", headers=auth_headers
    )).json()

    assert body == {"server": "jellyfin", "artwork": 1, "metadata": 1}
    assert (await session.execute(select(RenderDelivery.status))).scalar_one() == "pending"


async def test_an_unknown_server_is_a_404_on_every_route_of_this_router(
    client, auth_headers
):
    """One module, one vocabulary for one typo: these four routes sit on the
    same router as the six the Servers tab adds, which answer 404 and the
    probe's one sentence. Two of these used to answer 200 for a name this
    service manages no server by."""
    for method, path in (
        ("POST", "/api/servers/emby/catch-up"),
        ("GET", "/api/servers/emby/catch-up"),
        ("DELETE", "/api/servers/emby/catch-up"),
        ("POST", "/api/servers/emby/retry-failed"),
    ):
        response = await client.request(
            method, path, headers=auth_headers, json={}
        )
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["detail"] == NOT_A_SERVER


async def test_a_catch_up_with_the_scheduler_off_is_409_and_marks_nothing(
    scheduler_off_client, session
):
    """With the scheduler off the drain job is never registered, so a catch-up
    started here would mark the whole library and be drained by nothing --
    and the run it left open would refuse every later catch-up for that
    server. The button says so instead."""
    await _rendered_item(session)
    token = (await scheduler_off_client.post(
        "/api/login", json={"password": PASSWORD}
    )).json()["token"]

    response = await scheduler_off_client.post(
        "/api/servers/jellyfin/catch-up",
        headers={"Authorization": f"Bearer {token}"}, json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "the scheduler is off; a catch-up needs it to drain"
    assert (await session.execute(select(Run))).first() is None
    assert (await session.execute(select(RenderDelivery))).first() is None
