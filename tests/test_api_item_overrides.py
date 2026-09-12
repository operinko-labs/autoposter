"""GET/PUT/DELETE /api/items/{id}/metadata-overrides -- roadmap row 99's C10.

Three routes and no fourth. There is no bulk PUT and no importer: YAML import
is refused by the locked scope decision at the roadmap's own head
("Kometa/Posterizarr YAML is never parsed and no importer is planned") and by
row 99's own cell ("not YAML compatibility").

The two rules most of this file is about:

* **nothing here ever seeds a row from a provider or from Plex.** The PUT
  body is the only source of a row's value. That is the freezing hazard
  (frontend/src/api/overrides.ts:13-21) in this row's vocabulary, and it has a
  named test rather than a convention.
* **no refusal echoes what the operator typed.** A value may be anything --
  a summary with a URL in it, a pasted token -- so a 422 serves the exception
  CLASS NAME. The DELETE's Plex-failure 503 is class-name-only for the same
  reason (row 213's ruling): ``redact_urls`` only matches a URL scheme, and
  the commonest Plex failure -- a connection error -- carries none.
"""
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ItemMetadataOverride, Job
from autoposter.redact import redact_urls

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"


class RecordingPlexItem:
    """The minimum plexapi surface the DELETE's unlock touches."""

    def __init__(self, raises=None, labels=None):
        self.edits = []
        self.saved = 0
        self._raises = raises
        self.labels = labels or []

    def batchEdits(self):
        pass

    def edit(self, **kwargs):
        if self._raises is not None:
            raise self._raises
        self.edits.append(kwargs)

    def saveEdits(self):
        self.saved += 1


class FakePlex:
    def __init__(self, plex_item):
        self._plex_item = plex_item
        self.asked = []

    async def fetch_item(self, rating_key):
        self.asked.append(rating_key)
        return self._plex_item


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest_asyncio.fixture
async def app(session_factory):
    config = load_config(EXAMPLE)
    config.operations.item_overrides_enabled = True
    application = create_app(config, session_factory, _secrets())
    application.state.plex = FakePlex(RecordingPlexItem())
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _item(session, kind: str = "movie") -> int:
    item = await seed_media_item(
        session, "12345", library="Movies", kind=kind, title="Heat",
        year=1995, tmdb_id=949,
    )
    return item.id


# --- GET -------------------------------------------------------------------


async def test_the_listing_requires_a_session(client, session):
    item_id = await _item(session)
    assert (
        await client.get(f"/api/items/{item_id}/metadata-overrides")
    ).status_code == 401


async def test_an_unknown_item_is_404(client, auth_headers):
    response = await client.get(
        "/api/items/999999/metadata-overrides", headers=auth_headers
    )
    assert response.status_code == 404


async def test_the_listing_reports_the_gate_the_kind_and_the_writable_fields(
    client, auth_headers, session
):
    item_id = await _item(session)
    body = (
        await client.get(
            f"/api/items/{item_id}/metadata-overrides", headers=auth_headers
        )
    ).json()

    assert body["enabled"] is True
    assert body["kind"] == "movie"
    assert body["overrides"] == []
    assert "tagline" in body["writable"]
    assert body["writable"] == sorted(body["writable"])


async def test_the_listing_reports_the_gate_as_off_rather_than_hiding_the_panel(
    client, app, auth_headers, session
):
    """C8: off is READ-ONLY, not absent. The panel needs the flag to render a
    banner naming the key -- an operator whose overrides silently stopped
    applying, with no panel to explain it, would have nothing to go on."""
    item_id = await _item(session)
    app.state.config_holder.current.operations.item_overrides_enabled = False

    body = (
        await client.get(
            f"/api/items/{item_id}/metadata-overrides", headers=auth_headers
        )
    ).json()
    assert body["enabled"] is False
    assert body["writable"] != []


async def test_the_listing_serves_the_canonical_value_and_a_timestamp(
    client, auth_headers, session
):
    item_id = await _item(session)
    session.add(ItemMetadataOverride(
        item_id=item_id, field="critic_rating", value="8.7",
    ))
    await session.commit()

    body = (
        await client.get(
            f"/api/items/{item_id}/metadata-overrides", headers=auth_headers
        )
    ).json()
    assert body["overrides"] == [
        {"field": "critic_rating", "value": "8.7",
         "updated_at": body["overrides"][0]["updated_at"]}
    ]
    assert body["overrides"][0]["updated_at"] is not None


# --- PUT -------------------------------------------------------------------


async def test_a_put_stores_the_canonical_form_and_re_enqueues(
    client, auth_headers, session
):
    item_id = await _item(session)

    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/critic_rating",
        json={"value": "8.65"}, headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"
    assert response.json()["value"] == "8.7"
    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    assert row.value == "8.7", "the form the writer's diff is made in"
    assert (await session.execute(select(Job))).scalars().all() != []


async def test_a_second_put_replaces_rather_than_appends(
    client, auth_headers, session
):
    """UNIQUE(item_id, field) makes this an upsert. Two rows for one field
    would make "the override for this field" unanswerable."""
    item_id = await _item(session)
    for value in ("A24", "MGM"):
        await client.put(
            f"/api/items/{item_id}/metadata-overrides/studio",
            json={"value": value}, headers=auth_headers,
        )

    row = (await session.execute(select(ItemMetadataOverride))).scalar_one()
    assert row.value == "MGM"


async def test_a_field_the_kind_cannot_carry_is_422_with_a_class_name_only_detail(
    client, auth_headers, session
):
    """C3, both halves: a field not in ``WRITABLE_BY_KIND[kind]`` is a 422,
    and the detail is CLASS-NAME-ONLY. A season has no tagline in Plex, so
    storing one would be a row that can never be written.

    The form is pinned, not just the status. An earlier draft served a fixed
    sentence here -- safe, because it was a module constant that echoed
    nothing, but a different FORM from the one C3 and C10 name, and the whole
    point of a class-name-only rule is that every refusal about an
    operator-supplied value looks the same."""
    item_id = await _item(session, kind="season")
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/tagline",
        json={"value": "x"}, headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "OverrideValueError"
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_an_unknown_field_is_422_and_is_not_echoed(client, auth_headers, session):
    """``added_at`` is roadmap row 227's, STOP-and-filed. And the refusal does
    not name the field: it came from the URL path, so echoing it would reflect
    operator input straight back into a served string. With the detail reduced
    to a class name, that property holds by construction rather than by the
    wording of a sentence."""
    item_id = await _item(session)
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/added_at",
        json={"value": "2026-01-01"}, headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "OverrideValueError"
    assert "added_at" not in response.text


async def test_a_non_string_value_is_refused_by_us_not_by_the_framework(
    client, auth_headers, session
):
    """The handler takes the body as a plain ``dict`` and type-checks
    ``value`` itself, so a JSON number -- the realistic wrong shape from a
    hand-rolled client -- gets THIS module's class-name-only refusal rather
    than FastAPI's own validator, whose 422 echoes the submitted input.

    (A body that is not a JSON object at all is still FastAPI's to refuse; no
    panel sends that shape and it carries no field value. Named in the module
    docstring rather than defended against.)"""
    item_id = await _item(session)
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/critic_rating",
        json={"value": 8.7}, headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "OverrideValueError"
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_an_unparsable_value_is_422_with_a_class_name_only_detail(
    client, auth_headers, session
):
    """Row 213's law: a served refusal about an operator-typed value carries
    the exception CLASS NAME and nothing else -- the same rule
    ``queue/worker.py::_served_reason`` applies to ``job.last_error``."""
    item_id = await _item(session)
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/critic_rating",
        json={"value": "eleven"}, headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "OverrideValueError"
    assert "eleven" not in response.text


async def test_a_refusal_never_echoes_a_url_the_operator_typed(
    client, auth_headers, session
):
    """``redact_urls`` used as the ORACLE rather than as a call: the module
    exports ``URL_PATTERN`` precisely so a consumer needing to DETECT a URL
    does not compile a second copy. A response equal to its own redaction
    contains no URL at all."""
    item_id = await _item(session)
    secret = "https://plex.example/library?X-Plex-Token=abcd1234"
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/critic_rating",
        json={"value": secret}, headers=auth_headers,
    )
    assert response.status_code == 422
    assert redact_urls(response.text) == response.text
    assert "abcd1234" not in response.text


async def test_a_put_while_the_gate_is_off_is_409_naming_the_key(
    client, app, auth_headers, session
):
    """C8. A config KEY is not an operator value, so naming it is safe -- and
    it is the only way the refusal can say what to change."""
    item_id = await _item(session)
    app.state.config_holder.current.operations.item_overrides_enabled = False

    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/studio",
        json={"value": "A24"}, headers=auth_headers,
    )
    assert response.status_code == 409
    assert "operations.item_overrides_enabled" in response.json()["detail"]
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_a_put_requires_a_session(client, session):
    item_id = await _item(session)
    response = await client.put(
        f"/api/items/{item_id}/metadata-overrides/studio", json={"value": "A24"}
    )
    assert response.status_code == 401


# --- the freezing hazard ---------------------------------------------------


async def test_no_endpoint_seeds_a_row_from_a_provider_or_plex_value(
    client, app, auth_headers, session
):
    """THE NAMED TEST (C5). The freezing hazard, in this row's vocabulary:
    "Round-tripping the config would store today's values as overrides,
    freezing them against every future change to the git-owned YAML.
    Reverting a field is its key going *away*."

    Read onto row 99: an override row holds ONLY what an operator typed. The
    obvious panel affordance -- "seed this item's overrides from what Plex or
    TMDb currently says" -- is exactly the hazard, so no route creates a row
    except the PUT, and the PUT's body is the only source of its value.

    Proven by exhaustion rather than by inspection: an item with facts, with
    a Plex object and with a GET and a DELETE against it ends with ZERO rows.
    The precedent is ``test_collection_definitions_api.py:129`` and
    ``test_api_config_editor.py:878``, which forbid the same thing for
    collection definitions."""
    from autoposter.db.models import ItemFacts

    item_id = await _item(session)
    session.add(ItemFacts(
        item_id=item_id, critic_rating=8.7, audience_rating=6.3,
        content_rating="R", studio="Warner",
    ))
    await session.commit()

    await client.get(f"/api/items/{item_id}/metadata-overrides", headers=auth_headers)
    await client.delete(
        f"/api/items/{item_id}/metadata-overrides/critic_rating", headers=auth_headers
    )
    await client.get(f"/api/items/{item_id}/metadata-overrides", headers=auth_headers)

    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_the_listing_never_fills_a_writable_field_with_a_current_value(
    client, auth_headers, session
):
    """The same law from the panel's side. ``writable`` is a list of NAMES.
    Serving each name pre-filled with what Plex or the facts row currently
    holds would put today's values one Save away from being frozen as
    overrides -- which is the hazard with an extra click, not without it."""
    from autoposter.db.models import ItemFacts

    item_id = await _item(session)
    session.add(ItemFacts(item_id=item_id, studio="Warner"))
    await session.commit()

    body = (
        await client.get(
            f"/api/items/{item_id}/metadata-overrides", headers=auth_headers
        )
    ).json()
    assert all(isinstance(name, str) for name in body["writable"])
    assert "Warner" not in str(body)


# --- DELETE ----------------------------------------------------------------


async def test_a_delete_unlocks_the_field_in_plex_and_removes_the_row(
    client, app, auth_headers, session
):
    """C9's option (b), both halves. ONE write, ``{field}.locked = 0``, and no
    value write: everything this service writes is LOCKED, so a row that just
    went away would leave a locked field nothing will ever refill."""
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    plex_item = app.state.plex._plex_item

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cleared"
    assert plex_item.edits == [{"studio.locked": 0}]
    assert plex_item.saved == 1
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []
    assert (await session.execute(select(Job))).scalars().all() != []


async def test_deleting_an_override_that_is_not_there_is_404(
    client, auth_headers, session
):
    """Not a silent success: the panel only offers Clear for a field that has
    one, so being asked without one means the panel and the table disagree."""
    item_id = await _item(session)
    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )
    assert response.status_code == 404


async def test_a_plex_failure_leaves_the_row_in_place(client, app, auth_headers, session):
    """``clear_manual_override``'s ordering law, verbatim: if the first write
    fails, nothing else happens. A deleted row and a still-locked field would
    be an endpoint claiming to have cleared something it did not."""
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    app.state.plex = FakePlex(RecordingPlexItem(raises=OSError("boom")))

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )

    assert response.status_code == 503
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() != []


async def test_a_plex_failure_detail_is_the_class_name_only(
    client, app, auth_headers, session
):
    """Row 213's ruling, extended to this 503 too: the class name only, never
    ``str(exc)``. ``redact_urls`` is scheme-anchored and would not have
    caught this shape anyway -- the commonest Plex failure is a connection
    error naming the internal host and port, not a URL string."""
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    app.state.plex = FakePlex(RecordingPlexItem(
        raises=OSError(
            "HTTPConnectionPool(host='plex.internal', port=32400): Max "
            "retries exceeded with url: /library/metadata/12345 (Caused by "
            "NewConnectionError('Failed to establish a new connection'))"
        )
    ))

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "OSError"
    assert "plex.internal" not in response.text
    assert "32400" not in response.text
    assert "/library" not in response.text


async def test_a_delete_on_an_exempt_item_skips_the_plex_write(
    client, app, auth_headers, session
):
    """Row 35's ``exemption_reason`` is the single gate on every Plex
    metadata write this service makes, and the DELETE's unlock is a write
    like any other. An exempt item's row is still cleared, but nothing is
    sent to Plex, and the response says so rather than claiming an unlock
    that never happened."""
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    app.state.config_holder.current.operations.ignore_labels = ["no-poster"]
    plex_item = RecordingPlexItem(labels=["no-poster"])
    app.state.plex = FakePlex(plex_item)

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json()["plex"] == "skipped (exempt)"
    assert "unlocked" not in response.json()
    assert plex_item.edits == []
    assert plex_item.saved == 0
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() == []


async def test_a_delete_without_a_plex_connection_is_503_and_keeps_the_row(
    client, app, auth_headers, session
):
    """A replica running without the background services has ``state.plex``
    unset. Deleting the row anyway would leave the field locked in Plex with
    nothing left to say it should be unlocked."""
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    app.state.plex = None

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )

    assert response.status_code == 503
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() != []


async def test_a_delete_while_the_gate_is_off_is_409(client, app, auth_headers, session):
    item_id = await _item(session)
    session.add(ItemMetadataOverride(item_id=item_id, field="studio", value="A24"))
    await session.commit()
    app.state.config_holder.current.operations.item_overrides_enabled = False

    response = await client.delete(
        f"/api/items/{item_id}/metadata-overrides/studio", headers=auth_headers
    )
    assert response.status_code == 409
    assert (await session.execute(select(ItemMetadataOverride))).scalars().all() != []


async def test_a_delete_requires_a_session(client, session):
    item_id = await _item(session)
    assert (
        await client.delete(f"/api/items/{item_id}/metadata-overrides/studio")
    ).status_code == 401


async def test_a_library_that_disables_the_gate_refuses_its_items(
    app, client, auth_headers, session_factory,
):
    """Roadmap row 92. The pipeline honours a library's own
    ``operations.item_overrides_enabled``; an endpoint that did not would let
    an operator store a row for an item in that library and then never write
    it -- an override that looks saved and does nothing, which is the exact
    silence this row exists to end.

    The global gate stays ON here, so this is about the library's value and
    nothing else.
    """
    from autoposter.config.loader import build_config, read_config_document

    document = read_config_document(EXAMPLE)
    document.setdefault("operations", {})["item_overrides_enabled"] = True
    document["libraries"] = {
        "Movies": {"operations": {"item_overrides_enabled": False}},
    }
    swapped = build_config(document)
    # The two attributes `create_app` sets to the same object, rebound the way
    # `config/live.py`'s `swap_config` rebinds them -- without its scheduler
    # interval refresh, which this app has no jobs for.
    app.state.config_holder.swap(swapped)
    app.state.config = swapped

    async with session_factory() as session:
        item_id = await _item(session)

    listed = await client.get(
        f"/api/items/{item_id}/metadata-overrides", headers=auth_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["enabled"] is False, (
        "the library's item_overrides_enabled: false was ignored"
    )

    refused = await client.put(
        f"/api/items/{item_id}/metadata-overrides/tagline",
        headers=auth_headers, json={"value": "A Los Angeles crime saga"},
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "item_overrides_enabled" in detail
    # Task 3 review, Important 2: a library-off item gets its OWN sentence --
    # naming no library and no value -- rather than the global gate's, which
    # would read as false here (the global key is still `true`).
    assert detail == (
        "operations.item_overrides_enabled is off for this item's library; "
        "existing overrides are left in place and ignored, and nothing can "
        "be changed until it is on"
    )
    assert "is off;" not in detail


async def test_a_library_that_enables_the_gate_while_the_global_disables_it_is_allowed(
    app, client, auth_headers, session_factory,
):
    """Branch review Important 1, the mirror direction. The global gate had
    tightened only: a library that turned the setting OFF was refused even
    with the global ON, but a library that turned it ON while the global was
    OFF was refused too, by a bare pre-gate that ran before the item -- and
    so before the library -- was even known. The pipeline honours the
    library's own ``True`` here (``render/pipeline.py``'s rebound config
    reads), so the API must accept the write it would otherwise never write.
    """
    from autoposter.config.loader import build_config, read_config_document

    document = read_config_document(EXAMPLE)
    document.setdefault("operations", {})["item_overrides_enabled"] = False
    document["libraries"] = {
        "Movies": {"operations": {"item_overrides_enabled": True}},
    }
    swapped = build_config(document)
    app.state.config_holder.swap(swapped)
    app.state.config = swapped

    async with session_factory() as session:
        item_id = await _item(session)

    listed = await client.get(
        f"/api/items/{item_id}/metadata-overrides", headers=auth_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["enabled"] is True, (
        "the library's item_overrides_enabled: true was ignored"
    )

    allowed = await client.put(
        f"/api/items/{item_id}/metadata-overrides/tagline",
        headers=auth_headers, json={"value": "A Los Angeles crime saga"},
    )
    assert allowed.status_code == 200
