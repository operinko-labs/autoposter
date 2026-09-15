"""The mounted file's new job: a drift report.

The file is no longer a source of truth, so the one thing it can still say is
"the configuration in git is not the configuration that is running". The
notice offers Import and Export, both of which already exist.

The round trip at the bottom is the other half of the same change: with
`overridden_paths` gone, the page seeds its pending document from the whole
served configuration and sends the whole thing back. That is only safe if what
comes back is what was there, so this file proves it against a real store
rather than against the page's own idea of one.
"""
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from autoposter.api import routes
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import build_config
from autoposter.config.overrides import (
    STORE_FORMAT,
    document_paths,
    document_revision,
    load_effective_config,
    load_store,
    seed_store,
)
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def config_file(tmp_path) -> Path:
    path = tmp_path / "autoposter.yaml"
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    # A real notification URL, where the example ships an empty string. The
    # server redacts a non-empty string and nothing else, so with the example's
    # `""` every claim below about the keep sentinel would pass vacuously --
    # there would be nothing redacted for the page to ask back.
    document["notifications"]["url"] = "http://n8n.example:5678/webhook/t0ken"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


@pytest_asyncio.fixture
async def app(session_factory, config_file):
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    application = create_app(build_config(document), session_factory, _secrets())
    application.state.config_path = config_file
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()
    return application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.mark.asyncio
async def test_a_seeded_store_matching_its_file_reports_no_drift(
    client, auth_headers, config_file
):
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is True
    assert body["differs"] is False
    assert body["paths"] == []
    # The file's own document never crosses the wire: it can carry a push
    # token, and this route is read by an open page with no operator intent
    # behind it. A content hash is what the import needs and all it gets.
    assert "document" not in body
    assert body["file_revision"] == document_revision(
        yaml.safe_load(config_file.read_text(encoding="utf-8"))
    )


@pytest.mark.asyncio
async def test_an_edited_file_reports_the_paths_that_differ(
    client, auth_headers, config_file
):
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["workers"] = document["workers"] + 1
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")

    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["differs"] is True
    assert body["paths"] == ["workers"]
    assert "document" not in body
    # The hash of the document this comparison was made against, so the import
    # can be refused if the file moves between the two reads.
    assert body["file_revision"] == document_revision(document)


@pytest.mark.asyncio
async def test_the_drift_report_names_the_file_it_read(client, auth_headers, config_file):
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["path"] == str(config_file)


@pytest.mark.asyncio
async def test_a_file_that_no_longer_builds_is_drift_with_no_paths(
    client, auth_headers, config_file
):
    """A file the schema refuses cannot be walked against anything, but "the
    file and the store agree" would be a false sentence and the operator wants
    to know. Reported as a difference with an empty path list."""
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["workers"] = "lots"
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")

    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is True
    assert body["differs"] is True
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_no_file_is_not_drift(client, auth_headers, config_file):
    """A deployment whose store is seeded needs no file. Reporting a removed
    ConfigMap as drift would turn the intended end state into a permanent
    warning."""
    config_file.unlink()
    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is False
    assert body["differs"] is False
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_the_drift_route_needs_a_session(client):
    assert (await client.get("/api/config/drift")).status_code == 401


# --- importing the file -----------------------------------------------------
#
# The notice's Import names no document: it asks for the file, and the server
# reads it. Everything after that read is the ordinary import path, which is
# what these tests are really pinning -- the drop cap especially, because an
# import is the largest drop this service can be asked for.


async def _edit_the_file(config_file: Path, **changes) -> dict:
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document.update(changes)
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")
    return document


@pytest.mark.asyncio
async def test_importing_the_file_stores_it(
    client, auth_headers, config_file, session_factory
):
    document = await _edit_the_file(config_file, workers=9)
    served = (await client.get("/api/config", headers=auth_headers)).json()

    response = await client.post(
        "/api/config/drift/import",
        headers=auth_headers,
        json={"expected_revision": served["overrides_revision"], "confirm": False},
    )

    assert response.status_code == 200, response.text
    async with session_factory() as session:
        stored, _meta = await load_store(session)
    assert stored["workers"] == 9
    # And the report the notice re-reads now says the two agree.
    after = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert after["differs"] is False
    assert document["workers"] == 9


@pytest.mark.asyncio
async def test_importing_a_file_that_drops_settings_needs_the_confirm(
    client, auth_headers, config_file
):
    """The drop cap is the whole reason the notice has a tick. The store holds
    the whole configuration, so a file that has stopped mentioning a couple of
    sections drops every setting under them -- fourteen leaves here, well past
    the cap of three."""
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    del document["badges"]
    del document["notifications"]
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")
    served = (await client.get("/api/config", headers=auth_headers)).json()

    refused = await client.post(
        "/api/config/drift/import",
        headers=auth_headers,
        json={"expected_revision": served["overrides_revision"], "confirm": False},
    )
    assert refused.status_code == 422
    assert "send confirm: true" in refused.json()["detail"][0]["message"]

    allowed = await client.post(
        "/api/config/drift/import",
        headers=auth_headers,
        json={"expected_revision": served["overrides_revision"], "confirm": True},
    )
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_importing_refuses_a_file_that_moved_since_the_report(
    client, auth_headers, config_file
):
    """What the operator agreed to import was the document the notice
    described. A file edited between the GET and the POST is a different one,
    and importing it would store something they were never shown."""
    await _edit_the_file(config_file, workers=9)
    report = (await client.get("/api/config/drift", headers=auth_headers)).json()
    served = (await client.get("/api/config", headers=auth_headers)).json()
    await _edit_the_file(config_file, workers=11)

    response = await client.post(
        "/api/config/drift/import",
        headers=auth_headers,
        json={
            "expected_revision": served["overrides_revision"],
            "expected_file_revision": report["file_revision"],
            "confirm": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == routes.FILE_CHANGED_REFUSAL


@pytest.mark.asyncio
async def test_importing_with_no_file_is_refused(client, auth_headers, config_file):
    config_file.unlink()

    response = await client.post(
        "/api/config/drift/import", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == routes.NO_FILE_TO_IMPORT


@pytest.mark.asyncio
async def test_the_import_route_names_no_document(client, auth_headers):
    """The point of the route: a client cannot hand this endpoint a document,
    so there is no arm where one is written without having been read off the
    deployment's own file."""
    response = await client.post(
        "/api/config/drift/import",
        headers=auth_headers,
        json={"confirm": True, "document": {"workers": 9}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_import_route_needs_a_session(client):
    assert (await client.post("/api/config/drift/import", json={})).status_code == 401


@pytest.mark.asyncio
async def test_the_served_config_no_longer_carries_overridden_paths(
    client, auth_headers
):
    """The provenance loses `overridden_paths` and the page shows no pill. The
    question a settings row answers is "what is this set to", and the answer is
    the field beside it."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert "overridden_paths" not in body
    assert body["restart_paths"] == []


# The keys `GET /api/config` adds that are not configuration, exactly as
# `frontend/src/api/overrides.ts` spells them. Written out here rather than
# imported from anywhere, because what is being proved is that the PAGE's rule
# for building a document is lossless -- a shared constant would prove only
# that this file and that file agree.
PROVENANCE_KEYS = frozenset(
    {
        "frozen_paths",
        "redacted_paths",
        "keep_sentinel",
        "field_descriptions",
        "field_types",
        "computed_paths",
        "live_paths",
        "overrides_revision",
        "restart_paths",
        "secrets",
    }
)

_ABSENT = object()


def _read(document: dict, path: str) -> object:
    current: object = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _ABSENT
        current = current[part]
    return current


def _write(document: dict, path: str, value: object) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def _document_from_config(served: dict) -> dict:
    """What `documentFromConfig` builds in the browser, in Python.

    The whole served configuration minus the provenance keys and `secrets`,
    with the keep sentinel at exactly the paths this response says it redacted
    and nowhere else. No predicate of its own: the page cannot tell what was
    reduced from what was served, and a copy of the server's rule here would
    be one more thing that has to agree about the same set.
    """
    document = {
        key: value for key, value in served.items() if key not in PROVENANCE_KEYS
    }
    for path in served["redacted_paths"]:
        if _read(document, path) is _ABSENT:
            continue
        _write(document, path, served["keep_sentinel"])
    return document


async def _save_as_the_page_would(client, auth_headers) -> dict:
    """Read the config back and PUT it the way the settings page does.

    The whole served configuration minus the keys that are not settings, with
    the keep sentinel where the response redacted a value. Answers the document
    it sent.
    """
    served = (await client.get("/api/config", headers=auth_headers)).json()
    document = _document_from_config(served)
    # The page's own rule for the one redacted path, as a precondition: a
    # caller would pass vacuously if the sentinel never got written.
    assert _read(document, "notifications.url") == served["keep_sentinel"]

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": document, "expected_revision": served["overrides_revision"]},
    )
    assert response.status_code == 200, response.text
    return document


@pytest.mark.asyncio
async def test_a_store_the_page_has_saved_still_agrees_with_its_own_file(
    client, auth_headers
):
    """The notice must not light itself on the first save.

    What the page stores is a whole model dump; what seeded the store is the
    file's sparse YAML, which states what its author cared about and lets the
    schema default the rest. Those are the same configuration written two ways,
    and a drift report that walked the raw documents would report a difference
    at every setting the file does not mention -- plus `version`, which neither
    of them owns -- on every deployment, forever, with Import offered as the
    remedy for a difference the operator never made.
    """
    await _save_as_the_page_would(client, auth_headers)

    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["file_present"] is True
    assert body["differs"] is False, body["paths"]
    assert body["paths"] == []


@pytest.mark.asyncio
async def test_a_saved_store_still_reports_a_real_edit_of_the_file(
    client, auth_headers, config_file
):
    """The other half of the one above: the normalisation must not flatten a
    difference that is really there."""
    await _save_as_the_page_would(client, auth_headers)

    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["workers"] = document["workers"] + 1
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")

    body = (await client.get("/api/config/drift", headers=auth_headers)).json()
    assert body["differs"] is True
    assert body["paths"] == ["workers"]


@pytest.mark.asyncio
async def test_the_page_round_trips_the_whole_store_without_truncating_it(
    client, auth_headers, config_file, session_factory
):
    """Seed, serve, save the served document back, and the store is unharmed.

    This is the proof the page cannot truncate the store. The pending document
    is no longer a delta rebuilt from a provenance list -- it is the whole
    configuration -- so a save that lost anything on the way out and back would
    quietly delete settings nobody touched, and would do it on the very first
    save after a deployment upgraded.

    Two claims, and the second is the one that matters at boot: every leaf the
    seed put in the store is still there with the same value (the redacted one
    included, which survives as the keep sentinel and is resolved back
    server-side), and the configuration a restart would build from the store is
    the configuration that was running before the save.
    """
    seeded = yaml.safe_load(config_file.read_text(encoding="utf-8"))

    await _save_as_the_page_would(client, auth_headers)

    async with session_factory() as session:
        stored, meta = await load_store(session)
    assert meta["format"] == STORE_FORMAT
    lost = {
        path: _read(seeded, path)
        for path in document_paths(seeded)
        if _read(stored, path) != _read(seeded, path)
    }
    assert lost == {}

    async with session_factory() as session:
        rebuilt = await load_effective_config(config_file, session)
    assert rebuilt.model_dump(mode="json") == build_config(seeded).model_dump(mode="json")


@pytest.mark.asyncio
async def test_the_page_saves_against_a_store_that_has_no_notifications_section(
    tmp_path, session_factory
):
    """A mounted file with no `notifications:` block must still be savable.

    `seed_store` writes the file's raw, sparse document, so such a store has no
    `notifications.url` key at all. The served configuration still carries one
    -- the schema defaults it to `""` -- so a page that put the keep sentinel
    wherever the endpoint redacts *in general* would be asking the server to
    keep a stored value that does not exist. The answer is a 422 naming the
    path, on every Save, Preview and Apply from every panel that builds its
    body this way, and the operator cannot clear it from the UI: the only thing
    that would put the key into the store is the save being refused.

    What keeps the sentinel out is the response itself: nothing was redacted
    here, so `redacted_paths` is empty and the page has nothing to mark.
    """
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document.pop("notifications")
    path = tmp_path / "autoposter.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    application = create_app(build_config(document), session_factory, _secrets())
    application.state.config_path = path
    async with session_factory() as session:
        stored = await seed_store(session, document)
        await session.commit()
    assert "notifications" not in stored

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        served = (await client.get("/api/config", headers=headers)).json()
        # Served from the defaulted configuration, not from the store: the
        # path the page is about to decide on is present and empty.
        assert served["notifications"]["url"] == ""
        assert served["redacted_paths"] == [], "nothing was withheld from this body"

        body = _document_from_config(served)
        assert _read(body, "notifications.url") == ""
        assert served["keep_sentinel"] not in yaml.safe_dump(body)

        response = await client.put(
            "/api/config/overrides",
            headers=headers,
            json={
                "document": body,
                "expected_revision": served["overrides_revision"],
            },
        )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_a_stored_url_the_reduction_shortens_to_nothing_survives_a_page_save(
    tmp_path, session_factory
):
    """The served value cannot be the page's evidence about what was withheld.

    `_host_only` answers `""` for any URL it cannot find a host in, and nothing
    validates the setting -- a relative `/webhook/t0ken` is stored and served
    happily. What the page then sees at that path is a bare `""`, exactly what
    it sees when nothing is stored there at all. A page that decided from the
    value would send the `""` back and the save would write it over the token,
    silently: the corruption the keep marker exists to prevent, arrived by the
    other door.

    `redacted_paths` is what makes the two distinguishable, because it is
    collected by the loop that does the redacting rather than asserted about
    the path in general.
    """
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["notifications"]["url"] = "/webhook/t0ken"
    path = tmp_path / "autoposter.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    application = create_app(build_config(document), session_factory, _secrets())
    application.state.config_path = path
    async with session_factory() as session:
        await seed_store(session, document)
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        login = await client.post("/api/login", json={"password": PASSWORD})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        served = (await client.get("/api/config", headers=headers)).json()
        assert served["notifications"]["url"] == "", "the reduction found no host"
        assert served["redacted_paths"] == ["notifications.url"], (
            "the response has to say it withheld this one"
        )

        body = _document_from_config(served)
        assert _read(body, "notifications.url") == served["keep_sentinel"]

        response = await client.put(
            "/api/config/overrides",
            headers=headers,
            json={
                "document": body,
                "expected_revision": served["overrides_revision"],
            },
        )
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        stored, _meta = await load_store(session)
    assert stored["notifications"]["url"] == "/webhook/t0ken", "the save ate the token"
