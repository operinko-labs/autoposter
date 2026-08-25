"""The collections router: the preview, and the lifecycle ops.

``POST /api/collections/preview`` lives or dies by three properties:

- it is a **dry run whatever the config says**. ``apply_to_plex: true`` is the
  state a working deployment is in, and a preview that reconciled the library
  would be the worst possible bug here -- so the test below runs with writes
  switched on and proves nothing was written.
- it **answers with counts**, which is the whole reason an operator presses the
  button: how many members would arrive, how many would go, what the library
  does not own.
- it **never echoes a URL**. The poster step reports the source it could not
  fetch, and a provider URL routinely carries credentials.

The ``ops/*`` endpoints (roadmap row 28) are the opposite kind of thing: they
write, and unlike everything else in this service they write whatever
``apply_to_plex`` says. So what is tested about them is the guards that stand
in its place -- the ownership label, a managed row, a protected label winning
over both, and ``confirm: true`` for the one that deletes.
"""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.collections.builders import REGISTRY, BuilderResult, register
from autoposter.config.loader import load_config
from autoposter.config.schema import CollectionDefinition, Secrets
from autoposter.db.models import EventLog, ManagedCollection

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
    def __init__(self, title, items=(), labels=(), mode=-1):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.summary = None
        self.titleSort = None
        self.collectionMode = mode
        self.mode_set: list[str] = []
        self.deleted = False
        self.writes = 0
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    def modeUpdate(self, mode=None):
        self.writes += 1
        self.mode_set.append(mode)

    def label_names(self):
        return [tag.tag for tag in self._real_labels]

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
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def delete(self):
        self.deleted = True


class FakeSection:
    def __init__(self, items=(), existing=(), section_type="movie"):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._existing = {c.title: c for c in existing}
        self.type = section_type
        self.created = []
        self.key = "42"
        self.blank_posts = []
        # Stands in for ``section._server`` -- ``create_blank_collection``
        # issues its POST through it, the way the separator's does.
        self._server = self
        self._session = type("Sess", (), {"post": "POST-SENTINEL"})()

    def all(self):
        return list(self._items)

    def listFilterChoices(self, field, libtype=None):
        return []

    def collections(self, **kw):
        """``label=`` is a SERVER-side filter in plexapi (pinned in the
        contract test), so the fake has to filter too: the ops endpoint's
        ownership narrowing is that filter, and a fake that ignored it would
        make an endpoint that forgot the keyword look correct."""
        label = kw.get("label")
        return [
            collection for collection in self._existing.values()
            if label is None or label in collection.label_names()
        ]

    def collection(self, title):
        return self._existing[title]

    def createCollection(self, title, items=None, smart=False, **kw):
        self.created.append(title)
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection

    def _uriRoot(self):
        return "server://abc/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        """The raw POST ``create_blank_collection`` makes. The collection it
        creates has to exist afterwards, because the helper fetches it back
        through ``section.collection(title)``."""
        self.blank_posts.append(key)
        title = parse_qs(urlsplit(key).query)["title"][0]
        self._existing[title] = FakeCollection(title)
        self.created.append(title)


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
        "filtered": 0,
        "failed": False, "skipped": False,
        "actions": ["would update 'Charted' with 1 item(s)"],
    }]


async def test_the_preview_writes_nothing_even_with_writes_switched_on(
    client, auth_headers, section, session
):
    """The property the endpoint is only safe to exist because of."""
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


# --- the Task-4 follow-ups: the enabled gate and per-library containment ----


async def test_the_preview_refuses_when_collections_are_disabled(
    app, client, auth_headers
):
    """``collections.enabled: false`` is what an operator switches off to stop
    this service touching collections at all. Without this check the preview
    would keep connecting to Plex and reading the library regardless."""
    app.state.config_holder.current.collections.enabled = False

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 503
    assert "disabled" in response.json()["detail"]


async def test_one_unreadable_library_does_not_take_the_whole_preview_down(
    app, client, auth_headers, section
):
    """A section name that no longer names a section becomes that library's
    own failed entry. A 500 for the whole preview would tell the operator
    nothing about the libraries that are fine -- and a real pass contains a
    failing library exactly this way."""
    def sections(name):
        if name == "Gone":
            raise RuntimeError("https://plex.local/library?token=SECRET is 404")
        return section

    app.state.plex_server_factory = lambda: SimpleNamespace(
        library=SimpleNamespace(section=sections)
    )
    app.state.config_holder.current.collections.libraries = ["Gone", "Movies"]

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    gone = [d for d in body["definitions"] if d["library"] == "Gone"]
    assert gone == [{
        "title": "(library)", "library": "Gone",
        "adding": 0, "removing": 0, "deleting": 0, "unresolved": 0,
        "filtered": 0,
        "failed": True, "skipped": True,
        "actions": ["Gone: could not be previewed (RuntimeError)"],
    }]
    assert "SECRET" not in response.text, "the exception's text stays in the log"
    assert any(d["title"] == "Charted" for d in body["definitions"]), (
        "the library that could be read was still previewed"
    )


async def test_a_bundle_construction_failure_is_a_library_failure_not_a_500(
    client, auth_headers, monkeypatch
):
    """Fix round F4: the source bundle used to be built once, above the
    per-library loop and its ``try``, so a raise there took down the whole
    preview with a 500 -- the exact thing this endpoint's own docstring
    promises never happens to one library's failure (see
    ``test_one_unreadable_library_does_not_take_the_whole_preview_down``)."""
    def _boom(config, secrets, http, cache):
        raise RuntimeError("the bundle blew up")

    monkeypatch.setattr(
        "autoposter.api.collections_builders.build_source_clients", _boom
    )

    response = await client.post(
        "/api/collections/preview", json={}, headers=auth_headers
    )

    assert response.status_code == 200
    movies = [d for d in response.json()["definitions"] if d["library"] == "Movies"]
    assert movies == [{
        "title": "(library)", "library": "Movies",
        "adding": 0, "removing": 0, "deleting": 0, "unresolved": 0,
        "filtered": 0,
        "failed": True, "skipped": True,
        "actions": ["Movies: could not be previewed (RuntimeError)"],
    }]


# --- roadmap row 28: the lifecycle operations ------------------------------


BLANK = "/api/collections/ops/blank"
DELETE = "/api/collections/ops/delete"
MASS_MODE = "/api/collections/ops/mass-mode"


@pytest.mark.parametrize(
    "url,body",
    [
        (BLANK, {"library": "Movies", "title": "Divider"}),
        (DELETE, {"library": "Movies", "title": "Charted", "confirm": True}),
        (MASS_MODE, {"library": "Movies", "mode": "hide"}),
    ],
)
async def test_every_op_requires_a_session(client, url, body):
    assert (await client.post(url, json=body)).status_code == 401


@pytest.mark.parametrize(
    "url,body",
    [
        (BLANK, {"library": "Movies", "title": "Divider"}),
        (DELETE, {"library": "Movies", "title": "Charted", "confirm": True}),
        (MASS_MODE, {"library": "Movies", "mode": "hide"}),
    ],
)
async def test_every_op_refuses_a_library_this_instance_does_not_manage(
    client, auth_headers, url, body
):
    """These endpoints run the collections service's own operations by hand.
    Which libraries it runs against is a config decision, not something a
    request body gets to choose."""
    response = await client.post(
        url, json={**body, "library": "Someone Else's"}, headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "url,body",
    [
        (BLANK, {"library": "Movies", "title": "Divider"}),
        (DELETE, {"library": "Movies", "title": "Charted", "confirm": True}),
        (MASS_MODE, {"library": "Movies", "mode": "hide"}),
    ],
)
async def test_every_op_refuses_when_collections_are_disabled(
    app, client, auth_headers, url, body
):
    app.state.config_holder.current.collections.enabled = False

    assert (await client.post(url, json=body, headers=auth_headers)).status_code == 503


async def test_a_blank_collection_is_created_labelled_and_recorded(
    client, auth_headers, section, session
):
    """Empty collections cannot be made through plexapi's normal API, which is
    why this is an endpoint at all -- it goes through the same raw POST the
    Common Sense separator does."""
    response = await client.post(
        BLANK, json={"library": "Movies", "title": "Divider"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "actions": ["created the empty collection 'Divider' in 'Movies'"]
    }
    assert len(section.blank_posts) == 1
    assert section._existing["Divider"].label_names() == [LABEL]
    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Divider")
    )).scalar_one()
    assert row.kind == "operator"
    event = (await session.execute(
        select(EventLog).where(EventLog.event_type == "collection_blanked")
    )).scalar_one()
    assert event.payload == {"library": "Movies", "title": "Divider"}
    assert "http" not in event.outcome


async def test_a_blank_collection_resets_a_stale_managed_row_to_operator_kind(
    client, auth_headers, section, session
):
    """Fix round item 1: a ``managed_collections`` row can outlive the Plex
    collection it describes -- rows are never reaped when the object vanishes
    straight out of Plex rather than through this service. If a definition
    built "Divider" once, an operator later hand-deleted it in Plex, and an
    operator now blanks the same title, the pre-existing row must not keep
    its stale "manual" kind and hash: that combination is exactly what
    ``engine._sweep`` reads as "no definition builds this any more", so the
    very next ``delete_unconfigured`` pass would delete the blank the
    operator just made -- the same class of bug the "operator" kind exists to
    prevent, arriving through the row surviving instead of never existing.
    """
    session.add(ManagedCollection(
        library="Movies", title="Divider", kind="manual",
        plex_rating_key="stale-key", definition_hash="stale-hash",
    ))
    await session.commit()

    response = await client.post(
        BLANK, json={"library": "Movies", "title": "Divider"}, headers=auth_headers
    )

    assert response.status_code == 200
    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Divider")
    )).scalar_one()
    assert row.kind == "operator"
    assert row.definition_hash == ""


async def test_a_blank_collection_never_overwrites_an_existing_title(
    client, auth_headers, section
):
    """Claiming the existing one would be an adoption, which has its own
    rules, its own config flag, and is not this button."""
    response = await client.post(
        BLANK, json={"library": "Movies", "title": "Charted"}, headers=auth_headers
    )

    assert response.status_code == 409
    assert section.blank_posts == []


async def test_a_delete_without_confirmation_is_refused(
    client, auth_headers, section, session
):
    """The mutation proof of the confirmation guard: the same request that
    deletes below does nothing without ``confirm: true``."""
    session.add(ManagedCollection(
        library="Movies", title="Charted", kind="manual",
        plex_rating_key="c-Charted", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        DELETE, json={"library": "Movies", "title": "Charted"}, headers=auth_headers
    )

    assert response.status_code == 422
    assert "confirm" in response.json()["detail"]
    assert section._existing["Charted"].deleted is False


async def test_a_confirmed_delete_removes_the_collection_the_row_and_logs_it(
    client, auth_headers, section, session
):
    session.add(ManagedCollection(
        library="Movies", title="Charted", kind="manual",
        plex_rating_key="c-Charted", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        DELETE,
        json={"library": "Movies", "title": "Charted", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"actions": ["deleted 'Charted' from 'Movies'"]}
    assert section._existing["Charted"].deleted is True
    assert (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Charted")
    )).scalars().all() == []
    event = (await session.execute(
        select(EventLog).where(EventLog.event_type == "collection_deleted")
    )).scalar_one()
    assert event.payload == {"library": "Movies", "title": "Charted"}
    assert "http" not in event.outcome


async def test_a_collection_without_a_managed_row_is_not_ours_to_delete(
    client, auth_headers, section
):
    """The sweep's guard, applied here for the sweep's reason: a row alone can
    name a collection somebody else recreated under that title, and a label
    alone is one an operator applied by hand."""
    response = await client.post(
        DELETE,
        json={"library": "Movies", "title": "Charted", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert "managed_collections row" in response.json()["detail"]
    assert section._existing["Charted"].deleted is False


async def test_a_collection_without_the_ownership_label_is_not_ours_to_delete(
    client, auth_headers, section, session
):
    theirs = FakeCollection("Theirs", labels=["Kometa"])
    section._existing[theirs.title] = theirs
    session.add(ManagedCollection(
        library="Movies", title="Theirs", kind="manual",
        plex_rating_key="c-Theirs", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        DELETE,
        json={"library": "Movies", "title": "Theirs", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert theirs.deleted is False


async def test_a_protected_collection_is_never_deleted(
    app, client, auth_headers, section, session
):
    protected = app.state.config_holder.current.collections.protect_labels[0]
    theirs = FakeCollection("Deleted Soon", labels=[LABEL, protected])
    section._existing[theirs.title] = theirs
    session.add(ManagedCollection(
        library="Movies", title="Deleted Soon", kind="manual",
        plex_rating_key="c-Deleted Soon", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        DELETE,
        json={"library": "Movies", "title": "Deleted Soon", "confirm": True},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert protected in response.json()["detail"]
    assert theirs.deleted is False


async def test_mass_mode_only_touches_collections_this_service_owns(
    client, auth_headers, section, session
):
    """Kometa's own version sets the mode on every collection in the library.
    This one is narrowed twice -- the ownership label, then a managed row --
    so the operator's own collections and Plex's franchise collections are
    never touched, and each skip is reported rather than dropped."""
    theirs = FakeCollection("Their Franchise")
    labelled_only = FakeCollection("Half Ours", labels=[LABEL])
    section._existing[theirs.title] = theirs
    section._existing[labelled_only.title] = labelled_only
    session.add(ManagedCollection(
        library="Movies", title="Charted", kind="manual",
        plex_rating_key="c-Charted", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        MASS_MODE, json={"library": "Movies", "mode": "hideItems"}, headers=auth_headers
    )

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert actions[0] == "set the display mode of 1 collection(s) in 'Movies' to 'hideItems'"
    assert section._existing["Charted"].mode_set == ["hideItems"]
    assert theirs.mode_set == [], "no ownership label, so it was never even a candidate"
    assert labelled_only.mode_set == []
    assert any("Half Ours" in action and "no managed row" in action for action in actions)
    event = (await session.execute(
        select(EventLog).where(EventLog.event_type == "collection_mode_set")
    )).scalar_one()
    assert event.payload == {"library": "Movies", "title": "hideItems"}


async def test_mass_mode_skips_a_protected_collection(
    app, client, auth_headers, section, session
):
    protected = app.state.config_holder.current.collections.protect_labels[0]
    theirs = FakeCollection("Deleted Soon", labels=[LABEL, protected])
    section._existing[theirs.title] = theirs
    session.add(ManagedCollection(
        library="Movies", title="Deleted Soon", kind="manual",
        plex_rating_key="c-Deleted Soon", definition_hash="seed",
    ))
    await session.commit()

    response = await client.post(
        MASS_MODE, json={"library": "Movies", "mode": "hide"}, headers=auth_headers
    )

    assert theirs.mode_set == []
    assert any(protected in action for action in response.json()["actions"])


async def test_mass_mode_refuses_a_mode_plex_does_not_have(client, auth_headers):
    response = await client.post(
        MASS_MODE, json={"library": "Movies", "mode": "hide_items"}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_the_ops_ignore_apply_to_plex(app, client, auth_headers, section):
    """Deliberate, and the reason every guard above exists instead. That flag
    gates the unattended scheduled pass; these run because an operator pressed
    a button naming one library and one collection, and a button that did
    nothing because of a setting somewhere else would be worse than no
    button."""
    app.state.config_holder.current.collections.apply_to_plex = False

    response = await client.post(
        BLANK, json={"library": "Movies", "title": "Divider"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert len(section.blank_posts) == 1
