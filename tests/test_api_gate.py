"""Spec §9 through the real ASGI app: a Plex-less deployment boots, refuses
the Plex-only routes by sentence, leaves the routes that do not touch Plex
untouched, and registers none of the Plex-only scheduled jobs.

Every route here is exercised through ``create_app`` + ``ASGITransport`` --
no mock of ``require_plex`` or of ``app.state.servers`` -- because the thing
under test is the wiring itself: whether ``Depends(require_plex)`` actually
sits on the route it should.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import build_config, read_config_document
from autoposter.config.schema import Secrets
from autoposter.servers.registry import PLEX_REQUIRED
from conftest import EXAMPLE_CONFIG

PASSWORD = "correct horse battery staple"

# The full set of gated routes: 15 routes across 5 files. Each entry is
# (method, path, body-or-None) -- a body only for the routes whose Pydantic
# model has no field that defaults, so a POST with no body would 422 before
# the dependency even runs.
PLEX_ONLY_ROUTES: list[tuple[str, str, dict | None]] = [
    ("POST", "/api/artwork-modes/backup", None),
    ("POST", "/api/metadata-backup", None),
    ("POST", "/api/artwork-modes/restore", {}),
    ("POST", "/api/artwork-modes/revert", {}),
    ("POST", "/api/artwork-modes/reset", {}),
    ("POST", "/api/artwork-modes/logo", {}),
    ("POST", "/api/artwork-modes/logo-revert", {}),
    ("GET", "/api/id-mismatches", None),
    ("POST", "/api/playlists/preview", {}),
    ("POST", "/api/playlists/ops/delete", {"title": "x", "confirm": True}),
    ("POST", "/api/collections/preview", {}),
    ("POST", "/api/collections/ops/blank", {"library": "Movies", "title": "x"}),
    ("POST", "/api/collections/ops/delete", {"library": "Movies", "title": "x", "confirm": True}),
    (
        "POST", "/api/collections/ops/mass-mode",
        {"library": "Movies", "title": "x", "mode": "showItems"},
    ),
    ("DELETE", "/api/items/1/metadata-overrides/title", None),
]

# spec §9's "deliberately not gated" list, except the `/api/setup/plex/*`
# group, which is not included: those routes are not even mounted on this
# application (they belong to the separate first-start-wizard app `boot.py`
# builds, which has no `app.state.setup` here at all), so there is nothing on
# THIS app for that group to assert against.
NOT_GATED_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/collections/catalog", None),
    ("GET", "/api/collections/definitions", None),
    ("POST", "/api/collections/parse-source", {"url": "https://trakt.tv/lists/1"}),
    ("GET", "/api/playlists/definitions", None),
    ("GET", "/api/items/999999/metadata-overrides", None),  # 404, not 409: not gated
]

# The six jobs skipped from registration when Plex is not configured, by the
# exact names api/routes.py's SCHEDULED_JOB_NAMES serves.
PLEX_ONLY_JOB_NAMES = (
    "collections_reconcile", "credits_scan", "plex_maintenance",
    "plex_prune", "plex_merge", "arr_sync",
)


@pytest.fixture
async def plexless(session_factory):
    """A fully-booted app for a config with a ``jellyfin:`` block and no
    ``plex:`` block, copied from
    ``tests/test_app.py::test_a_plex_less_app_boots_and_serves_status``
    (there is no ``secrets_factory`` fixture anywhere in this suite; that
    test builds ``Secrets`` directly, and this fixture does the same).
    """
    doc = read_config_document(EXAMPLE_CONFIG)
    doc.pop("plex")
    doc["jellyfin"] = {"url": "https://jf"}
    config = build_config(doc)
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        tmdb_token="x", tvdb_apikey="x", fanart_apikey="x", webhook_secret="x",
        jellyfin_api_key="k", admin_password_hash=hash_password(PASSWORD),
    )
    app = create_app(config, session_factory, secrets)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = (await client.post("/api/login", json={"password": PASSWORD})).json()["token"]
        yield app, client, {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("method,path,body", PLEX_ONLY_ROUTES)
async def test_every_plex_only_route_refuses_with_the_sentence(plexless, method, path, body):
    _, client, headers = plexless
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    response = await client.request(method, path, **kwargs)
    assert response.status_code == 409, (
        f"{method} {path} answered {response.status_code} ({response.text}), "
        "not the 409 require_plex should raise first"
    )
    assert response.json()["detail"] == PLEX_REQUIRED


@pytest.mark.parametrize("method,path,body", NOT_GATED_ROUTES)
async def test_not_gated_routes_never_answer_409_on_a_plex_less_app(plexless, method, path, body):
    _, client, headers = plexless
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    response = await client.request(method, path, **kwargs)
    assert response.status_code != 409, (
        f"{method} {path} answered 409 on a Plex-less app; it is not supposed "
        "to be behind require_plex at all"
    )
    # A 4xx validation answer is fine (not what this is ruling out); a 5xx
    # would otherwise pass the check above just as silently as a 409 would.
    assert response.status_code < 500, (
        f"{method} {path} answered {response.status_code} ({response.text})"
    )


async def test_status_says_which_servers_exist(plexless):
    _, client, headers = plexless
    body = (await client.get("/api/status", headers=headers)).json()
    assert body["capabilities"] == {"plex": False, "jellyfin": True}
    registered = {job["name"] for job in body["scheduled_jobs"]}
    assert not registered & set(PLEX_ONLY_JOB_NAMES)
