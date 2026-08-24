"""``POST /api/collections/preview``: what a pass would do, and nothing else.

Three properties this endpoint lives or dies by:

- it is a **dry run whatever the config says**. ``apply_to_plex: true`` is the
  state a working deployment is in, and a preview that reconciled the library
  would be the worst possible bug here -- so the test below runs with writes
  switched on and proves nothing was written.
- it **answers with counts**, which is the whole reason an operator presses the
  button: how many members would arrive, how many would go, what the library
  does not own.
- it **never echoes a URL**. The poster step reports the source it could not
  fetch, and a provider URL routinely carries credentials.
"""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.collections.builders import REGISTRY, BuilderResult, register
from autoposter.config.loader import load_config
from autoposter.config.schema import CollectionDefinition, Secrets
from autoposter.db.models import ManagedCollection

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
LABEL = "autoposter"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    def __init__(self, key, guids=()):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]


class FakeCollection:
    def __init__(self, title, items=(), labels=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.summary = None
        self.deleted = False
        self.writes = 0
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return []

    def reload(self, **kw):
        self._cache = list(self._live)
        self._labels = self._real_labels

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self.writes += 1
        self._live.extend(items)

    def removeItems(self, items):
        self.writes += 1

    def moveItem(self, item, after=None):
        self.writes += 1

    def sortUpdate(self, sort=None):
        self.writes += 1

    def query(self, key, method=None, **kwargs):
        self.writes += 1

    def addLabel(self, labels, locked=True):
        self.writes += 1

    def delete(self):
        self.deleted = True


class FakeSection:
    def __init__(self, items=(), existing=(), section_type="movie"):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._existing = {c.title: c for c in existing}
        self.type = section_type
        self.created = []

    def all(self):
        return list(self._items)

    def listFilterChoices(self, field, libtype=None):
        return []

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        self.created.append(title)
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection


class FakeServer:
    def __init__(self, sections):
        self.library = SimpleNamespace(section=lambda name: sections[name])


class _Listing:
    def __init__(self, type_name, ids):
        self.type_name = type_name
        self.ids = list(ids)

    async def build(self, ctx) -> BuilderResult:
        return BuilderResult(ids=list(self.ids))


class _PosterFailing:
    """A builder whose action strings will carry a URL: naming a poster the
    hosted default cannot serve is what puts one in the report."""

    type_name = "preview_poster"

    async def build(self, ctx) -> BuilderResult:
        return BuilderResult(
            ids=[("imdb", "tt1")], poster_kind="chart", poster_key="nonesuch"
        )


@pytest.fixture
def registry_entry():
    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add

    for type_name in registered:
        del REGISTRY[type_name]


@pytest.fixture
def section():
    """A Movies library owning two items, with one collection already built
    from one of them -- so a preview has an add, a remove and a miss to
    count."""
    live = FakeCollection("Charted", [FakeItem("m2", ["imdb://tt2"])], labels=[LABEL])
    return FakeSection(
        [("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])], existing=[live]
    )


@pytest_asyncio.fixture
async def app(session_factory, section, registry_entry):
    registry_entry(_Listing("preview_listing", [("imdb", "tt1"), ("imdb", "tt404")]))
    config = load_config(EXAMPLE)
    # Writes on: the state a live deployment is in, and the one where a
    # preview that forgot to force dry_run would reconcile the library.
    config.collections.apply_to_plex = True
    config.collections.charts = False
    config.collections.awards = False
    config.collections.separators = False
    config.collections.posters = False
    config.collections.libraries = ["Movies"]
    config.collections.definitions = [
        CollectionDefinition(title="Charted", builder="preview_listing")
    ]
    app = create_app(config, session_factory, _secrets())
    app.state.plex_server_factory = lambda: FakeServer({"Movies": section})
    yield app


@pytest_asyncio.fixture
async def client(app):
    asgi = ASGITransport(app=app)
    async with AsyncClient(transport=asgi, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_the_preview_requires_a_session(client):
    assert (await client.post("/api/collections/preview", json={})).status_code == 401


async def test_a_replica_without_plex_answers_503(app, client, auth_headers):
    app.state.plex_server_factory = None

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 503


async def test_the_preview_reports_the_counts_per_definition(
    client, auth_headers, section
):
    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 200
    charted = [d for d in response.json()["definitions"] if d["title"] == "Charted"]
    assert charted == [{
        "title": "Charted", "library": "Movies",
        # tt1 is owned and not in the collection; m2 is in it and no longer
        # named; tt404 is a title this library does not own.
        "adding": 1, "removing": 1, "deleting": 0, "unresolved": 1,
        "failed": False, "skipped": False,
        "actions": ["would update 'Charted' with 1 item(s)"],
    }]


async def test_the_preview_writes_nothing_even_with_writes_switched_on(
    client, auth_headers, section, session
):
    """The property the endpoint is only safe to exist because of."""
    from sqlalchemy import select

    await client.post("/api/collections/preview", json={}, headers=auth_headers)

    assert section.created == []
    assert [c.writes for c in section._existing.values()] == [0]
    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert rows == [], "a preview must leave no managed_collections row behind"


async def test_the_preview_reports_a_failed_definition_as_failed(
    app, client, auth_headers, registry_entry
):
    class _Dead:
        type_name = "preview_dead"

        async def build(self, ctx):
            raise RuntimeError("the source is down")

    registry_entry(_Dead())
    app.state.config_holder.current.collections.definitions = [
        CollectionDefinition(title="Dead", builder="preview_dead")
    ]

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    dead = [d for d in response.json()["definitions"] if d["title"] == "Dead"]
    assert dead[0]["failed"] is True
    assert dead[0]["skipped"] is True
    assert not any("down" in action for action in dead[0]["actions"]), (
        "whatever a builder raised stays in the log"
    )


async def test_the_preview_never_carries_a_provider_url(
    app, client, auth_headers, registry_entry, section, session
):
    """The poster step reports the source it could not fetch, and that source
    is a URL. It reaches an existing collection with a managed row -- which is
    also the only shape where the poster step runs at all in a dry run."""
    registry_entry(_PosterFailing())
    postered = FakeCollection("Postered", [FakeItem("m2", ["imdb://tt2"])], labels=[LABEL])
    section._existing[postered.title] = postered
    session.add(ManagedCollection(
        library="Movies", title="Postered", kind="manual",
        plex_rating_key="c-Postered", definition_hash="seed",
    ))
    await session.commit()
    config = app.state.config_holder.current
    config.collections.posters = True
    config.collections.definitions = [
        CollectionDefinition(title="Postered", builder="preview_poster")
    ]

    async def handle(request):
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        app.state.http = http
        response = await client.post(
            "/api/collections/preview", json={}, headers=auth_headers
        )

    body = response.text
    assert "could not fetch a usable poster" in body, body
    assert "http" not in body, body
    assert "<url>" in body, "the action was reported, with the URL taken out"


async def test_a_title_filter_previews_only_that_definition(
    app, client, auth_headers, registry_entry
):
    registry_entry(_Listing("preview_other", [("imdb", "tt2")]))
    config = app.state.config_holder.current
    config.collections.definitions = [
        CollectionDefinition(title="Charted", builder="preview_listing"),
        CollectionDefinition(title="Other", builder="preview_other"),
    ]

    response = await client.post(
        "/api/collections/preview", json={"title": "Other"}, headers=auth_headers
    )

    assert [d["title"] for d in response.json()["definitions"]] == ["Other"]


async def test_an_unknown_library_filter_previews_nothing(client, auth_headers):
    response = await client.post(
        "/api/collections/preview", json={"library": "Nope"}, headers=auth_headers
    )

    assert response.json() == {"definitions": [], "actions": []}


async def test_the_preview_reports_what_the_sweep_would_delete(
    app, client, auth_headers, section, session
):
    """A collection no definition builds, previewed: reported as a deletion,
    not performed. ``delete_unconfigured`` is on -- with it off the preview
    reports the same collection as a leftover instead, which is the sweep's
    own behaviour and not this endpoint's business."""
    from sqlalchemy import select

    orphan = FakeCollection("Retired", [FakeItem("m1")], labels=[LABEL])
    section._existing[orphan.title] = orphan
    session.add(ManagedCollection(
        library="Movies", title="Retired", kind="manual",
        plex_rating_key="c-Retired", definition_hash="seed",
    ))
    await session.commit()
    app.state.config_holder.current.collections.delete_unconfigured = True

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    retired = [d for d in response.json()["definitions"] if d["title"] == "Retired"]
    assert retired[0]["deleting"] == 1
    assert retired[0]["actions"] == ["would delete 'Retired': no definition builds it"]
    assert orphan.deleted is False
    rows = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Retired")
    )).scalars().all()
    assert len(rows) == 1, "a previewed deletion must not remove the row"
