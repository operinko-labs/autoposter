"""The config editor's endpoints: save, preview and apply.

Three rules are load-bearing here and each has a test whose failure message
says which one broke.

1. **Never half-apply.** An invalid document must leave no overrides row, no
   swapped generation and no audit event. Everything that can reject the
   document runs before anything that writes.
2. **Unknown keys are errors.** Pydantic ignores them, so without an explicit
   check a typo'd setting is stored, merged, validated clean, and does nothing
   for as long as the operator believes it is in force.
3. **``null`` is a value, not an eraser.** Reverting a setting to the mounted
   file's is expressed by leaving the key out of the document; writing null
   asks for a null value and is rejected like any other bad value.
"""
import json
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from autoposter.api.auth import hash_password
from autoposter.api.routes import KEEP_SENTINEL
from autoposter.app import create_app
from autoposter.config.loader import build_config
from autoposter.config.schema import Secrets
from autoposter.db.models import ConfigOverride, EventLog, Job, MediaItem, Render
from autoposter.plex.client import ResolvedItem
from autoposter.queue.jobs import enqueue
from autoposter.render.pipeline import compute_fingerprint, gather_fingerprint_inputs

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
    """The example config, written where the endpoints will read it from.

    A real file rather than the repository's own, because these endpoints
    merge onto the *document* and the font and overlay roots have to point at
    files that exist for the impact walk to hash anything.
    """
    fonts, overlays = tmp_path / "fonts", tmp_path / "overlays"
    fonts.mkdir()
    overlays.mkdir()
    (fonts / "Comfortaa-Medium.ttf").write_bytes(b"the configured font")
    for name in ("overlay.png", "bottom-up-fade.png", "bottom-up-fade-background.png"):
        (overlays / name).write_bytes(b"overlay " + name.encode())
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["fonts_root"] = str(fonts)
    document["overlays_root"] = str(overlays)
    path = tmp_path / "autoposter.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


@pytest_asyncio.fixture
async def app(session_factory, config_file):
    config = build_config(yaml.safe_load(config_file.read_text(encoding="utf-8")))
    application = create_app(config, session_factory, _secrets())
    # create_app publishes the deployment's own path; this app was built from
    # a different file, so it says so.
    application.state.config_path = config_file
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


TEXT_EDIT = {"artwork": {"title_card": {"season_label": "Kausi"}}}


async def _seed_library(session, config) -> None:
    """One movie poster and one episode title card, correctly fingerprinted."""
    rows = [
        (
            ResolvedItem(
                rating_key="rk1", library="Movies", kind="movie", title="A Movie",
                year=1999, season_number=None, episode_number=None,
                root_folder="A Movie (1999)", file_path=None, art_url=None,
                tmdb_id=550, tvdb_id=None, imdb_id=None,
            ),
            "poster",
        ),
        (
            ResolvedItem(
                rating_key="rk2", library="TV Shows", kind="episode", title="Pilot",
                year=1999, season_number=1, episode_number=1,
                root_folder="A Show (1999)", file_path=None, art_url=None,
                tmdb_id=1399, tvdb_id=None, imdb_id=None,
            ),
            "title_card",
        ),
    ]
    for item, art_kind in rows:
        row = MediaItem(
            rating_key=item.rating_key, library=item.library, kind=item.kind,
            title=item.title, year=item.year, tmdb_id=item.tmdb_id,
            season_number=item.season_number, episode_number=item.episode_number,
            root_folder=item.root_folder,
        )
        session.add(row)
        await session.flush()
        text_inputs, asset_hashes = await gather_fingerprint_inputs(config, item, art_kind)
        session.add(
            Render(
                item_id=row.id, art_kind=art_kind, status="rendered",
                asset_path=f"/assets/{item.title}/{art_kind}.jpg",
                source_url=f"https://example/{art_kind}", base_sha256="a" * 64,
                fingerprint=compute_fingerprint(
                    config.version, art_kind, f"https://example/{art_kind}", "a" * 64,
                    text_inputs, asset_hashes,
                ),
            )
        )
    await session.commit()


async def _seed_logo_poster(session, config) -> None:
    """A poster whose real render composited a clearlogo.

    The impact walk cannot know that (config/impact.py's approximation), so
    this row reads as affected by *any* edit. It is what makes the endpoints'
    "this edit cannot change a rendered image" short-circuit observable: drop
    it, and a scheduler tweak re-renders every logo'd poster in the library.
    """
    item = ResolvedItem(
        rating_key="rk9", library="Movies", kind="movie", title="Logo Movie",
        year=1999, season_number=None, episode_number=None,
        root_folder="Logo Movie (1999)", file_path=None, art_url=None,
        tmdb_id=680, tvdb_id=None, imdb_id=None,
    )
    row = MediaItem(
        rating_key=item.rating_key, library=item.library, kind=item.kind,
        title=item.title, year=item.year, tmdb_id=item.tmdb_id, root_folder=item.root_folder,
    )
    session.add(row)
    await session.flush()
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, item, "poster", draw_text=False, logo_sha="deadbeef"
    )
    session.add(
        Render(
            item_id=row.id, art_kind="poster", status="rendered",
            asset_path="/assets/Logo Movie/poster.jpg",
            source_url="https://example/poster", base_sha256="a" * 64,
            fingerprint=compute_fingerprint(
                config.version, "poster", "https://example/poster", "a" * 64,
                text_inputs, asset_hashes,
            ),
        )
    )
    await session.commit()


# --- authentication ---


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/api/config/overrides"),
        ("POST", "/api/config/preview"),
        ("POST", "/api/config/apply"),
    ],
)
async def test_the_editor_endpoints_require_a_session(client, method, path):
    """Also swept structurally by test_api_login.py; named here so a reader of
    this file can see the answer without going to find it."""
    assert (await client.request(method, path, json={"document": {}})).status_code == 401


# --- GET /api/config enrichment ---


async def test_get_config_reports_which_paths_are_overridden(client, auth_headers):
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"workers": 9, **TEXT_EDIT}},
    )
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overridden_paths"] == ["artwork.title_card.season_label", "workers"]


async def test_get_config_reports_no_overridden_paths_before_any_edit(client, auth_headers):
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overridden_paths"] == []


async def test_get_config_carries_the_frozen_paths_and_their_reasons(client, auth_headers):
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert "workers" in body["frozen_paths"]
    assert body["frozen_paths"]["workers"], "a restart flag with no reason is a shrug"
    assert "artwork" not in body["frozen_paths"]


async def test_get_config_still_redacts(client, auth_headers):
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert set(body["secrets"].values()) == {"***REDACTED***"}


def _leaf_paths(body: dict, prefix: str = "") -> list[str]:
    """Every dotted path the settings page renders as a row.

    A dict recurses; anything else -- scalar, list, null -- is a leaf, which is
    exactly the rule ``Settings.tsx``'s ``ConfigNode`` applies to decide
    between a subsection and a row.
    """
    leaves: list[str] = []
    for key, value in body.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            leaves.extend(_leaf_paths(value, f"{path}."))
        else:
            leaves.append(path)
    return leaves


PROVENANCE_KEYS = {
    "overridden_paths",
    "frozen_paths",
    "redacted_paths",
    "keep_sentinel",
    "field_descriptions",
    "computed_paths",
    "live_paths",
}


async def test_get_config_describes_every_path_it_serves(client, auth_headers):
    """The completeness guard's endpoint half: a row the page renders with no
    description is a setting whose only documentation is the YAML file the
    operator does not have open."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    descriptions = body["field_descriptions"]
    served = _leaf_paths({
        key: value for key, value in body.items() if key not in PROVENANCE_KEYS
    })
    assert served, "the config response serves no settings at all"
    undescribed = [path for path in served if path not in descriptions]
    assert undescribed == [], f"served with no description: {undescribed}"


async def test_get_config_describes_a_sample_of_settings_in_words(client, auth_headers):
    """Presence is checked wholesale above; this is the spot-check that the
    entries are sentences rather than placeholders."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    descriptions = body["field_descriptions"]
    for path in ("workers", "plex.url", "collections.max_deletes"):
        assert descriptions[path].strip(), f"{path} is described by nothing"
        assert len(descriptions[path]) > 20, f"{path}'s description is a stub"


async def test_get_config_names_the_paths_it_computes(client, auth_headers):
    """``version`` is derived from the settings, not set by the operator, so an
    override on it is inert. Served as data (roadmap row 112) rather than left
    for the editor to hard-code, which is how the two sides drift."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["computed_paths"] == ["version"]


async def test_get_config_names_the_live_exceptions(client, auth_headers):
    """A path inside a frozen section that is nonetheless read per use. Without
    it the editor marks ``plex.resolve_max_attempts`` "restart to apply" while
    the save response correctly omits it -- the two halves of one endpoint
    disagreeing about the same path (roadmap row 112)."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["live_paths"] == ["plex.resolve_max_attempts"]
    # It is inside a frozen prefix: that is the whole reason it has to be said.
    assert "plex" in body["frozen_paths"]


async def test_get_config_reports_a_corrupt_overrides_row_as_500_with_detail(
    client, auth_headers, session_factory
):
    """A config_overrides row hand-edited to hold a non-dict document -- the
    case load_overrides_document's ValueError guards -- must not escape as a
    bare 500. The operator needs to know what is wrong and how to fix it."""
    async with session_factory() as session:
        stmt = insert(ConfigOverride).values(id=1, document=["not", "a", "dict"])
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_={"document": stmt.excluded.document})
        await session.execute(stmt)
        await session.commit()

    response = await client.get("/api/config", headers=auth_headers)
    assert response.status_code == 500
    assert response.json()["detail"] == (
        "config overrides row is corrupt (not a JSON object); fix or delete it"
    )


# --- rejection: nothing is half-applied ---


async def test_an_invalid_value_is_a_422_naming_the_path(client, auth_headers):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"artwork": {"poster": {"text": {"min_point_size": "huge"}}}}},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [e["path"] for e in detail] == ["artwork.poster.text.min_point_size"]
    assert detail[0]["message"]


async def test_a_rejected_document_changes_nothing(client, auth_headers, session, app):
    before = app.state.config.version
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"workers": -1, "artwork": {"poster": {"border_width": "wide"}}}},
    )
    assert response.status_code == 422

    assert (await session.execute(select(ConfigOverride))).scalars().all() == [], (
        "the overrides row was written before the config was validated, so an "
        "invalid document is now the stored configuration"
    )
    assert (await session.execute(select(EventLog))).scalars().all() == [], (
        "an audit row claims an update that did not happen"
    )
    assert app.state.config.version == before, "the running generation was swapped"
    assert app.state.config_holder.current.version == before
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overridden_paths"] == []


async def test_an_unknown_key_is_a_422_at_full_depth(client, auth_headers, session):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"artwork": {"poster": {"text": {"font_size": 12}}}}},
    )
    assert response.status_code == 422, (
        "pydantic ignores unknown keys, so this typo was stored and merged and "
        "will do nothing while the operator believes it took effect"
    )
    assert [e["path"] for e in response.json()["detail"]] == ["artwork.poster.text.font_size"]
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


async def test_an_unknown_top_level_key_is_a_422(client, auth_headers):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"wrokers": 9}}
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["detail"]] == ["wrokers"]


async def test_a_secrets_key_anywhere_is_refused(client, auth_headers, session):
    """Secrets are environment-only; the document must never carry one."""
    for document in ({"secrets": {"plex_token": "leaked"}}, {"plex": {"secrets": {"t": "x"}}}):
        response = await client.put(
            "/api/config/overrides", headers=auth_headers, json={"document": document}
        )
        assert response.status_code == 422, document
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


# --- null semantics ---


async def test_writing_null_is_a_value_not_an_unset(client, auth_headers, session):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": None}}
    )
    assert response.status_code == 422, (
        "null was treated as 'revert this setting'. It is not: it asks for a "
        "config whose workers is null. Reverting is expressed by omitting the key"
    )
    assert [e["path"] for e in response.json()["detail"]] == ["workers"]
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


async def test_omitting_a_key_reverts_it_to_the_file(client, auth_headers, app):
    await client.put("/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}})
    assert app.state.config.workers == 9

    await client.put("/api/config/overrides", headers=auth_headers, json={"document": {}})
    assert app.state.config.workers == 5, "the example config's value did not come back"
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overridden_paths"] == []


# --- saving ---


async def test_a_save_persists_the_document_and_swaps_the_generation(
    client, auth_headers, session, app
):
    before = app.state.config.version
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version_before"] == before
    assert body["version_after"] != before, "an artwork edit must move the render version"

    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.id == 1
    assert row.document == TEXT_EDIT
    assert app.state.config.artwork.title_card.season_label == "Kausi"
    assert app.state.config_holder.current is app.state.config
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "artwork"
    ]["title_card"]["season_label"] == "Kausi"


async def test_a_second_save_replaces_the_document_rather_than_adding_a_row(
    client, auth_headers, session
):
    await client.put("/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}})
    await client.put("/api/config/overrides", headers=auth_headers, json={"document": {"workers": 7}})
    rows = (await session.execute(select(ConfigOverride))).scalars().all()
    assert len(rows) == 1 and rows[0].document == {"workers": 7}


async def test_a_scheduler_edit_leaves_the_render_version_alone(client, auth_headers):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"scheduler": {"drift_days": 3}}},
    )
    body = response.json()
    assert body["version_after"] == body["version_before"]


async def test_a_frozen_path_is_reported_as_needing_a_restart(client, auth_headers):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )
    assert response.json()["restart_required"] == ["workers"]


async def test_a_live_path_needs_no_restart(client, auth_headers):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.json()["restart_required"] == []


async def test_api_docs_enabled_is_reported_as_inert_not_restart_required(
    client, auth_headers
):
    """``api_docs_enabled`` is frozen (FastAPI builds the docs routes into the
    application object before the overrides are read), but unlike every other
    frozen path a restart does not fix it either -- only editing the mounted
    file does. Folding it into ``restart_required`` would tell the operator a
    restart will apply a change it never can; it belongs in ``inert``
    instead."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"api_docs_enabled": True}},
    )
    body = response.json()
    assert "api_docs_enabled" not in body["restart_required"]
    assert body["inert"] == ["api_docs_enabled"]


async def test_a_save_writes_one_audit_event_carrying_no_settings(
    client, auth_headers, session, app
):
    before = app.state.config.version
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"artwork": {"title_card": {"season_label": "Kausi"}}}},
    )
    events = (await session.execute(select(EventLog))).scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.source == "config"
    assert event.event_type == "overrides_updated"
    assert event.outcome.startswith(f"version {before} -> ")
    serialised = repr(event.payload) + repr(event.outcome)
    assert "Kausi" not in serialised and "season_label" not in serialised, (
        "the audit row carries the document's contents; it must carry only "
        "which versions the config moved between"
    )


# --- preview ---


async def test_a_preview_reports_the_affected_rows(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    response = await client.post(
        "/api/config/preview", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.status_code == 200
    impact = response.json()["impact"]
    assert impact["of_total"] == 2
    assert impact["affected"] == 2
    assert impact["by_art_kind"] == {"poster": 1, "title_card": 1}


async def test_a_preview_of_a_gated_off_kind_leaves_it_out(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"artwork": {"poster": {"enabled": False}, **TEXT_EDIT["artwork"]}}},
    )
    impact = response.json()["impact"]
    assert impact["by_art_kind"] == {"title_card": 1}
    assert impact["of_total"] == 1


async def test_a_preview_of_a_non_render_edit_reports_no_impact(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    # Plus a row the walk would report as affected by anything at all, so this
    # is a real short-circuit rather than a walk that happens to find nothing.
    await _seed_logo_poster(session, app.state.config)
    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"scheduler": {"drift_days": 3}}},
    )
    assert response.json()["impact"] is None, (
        "a cadence edit cannot change a rendered image, and the impact walk's "
        "logo approximation would report a number made entirely of noise"
    )


async def test_a_preview_of_an_empty_document_reports_no_impact(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    assert (
        await client.post("/api/config/preview", headers=auth_headers, json={"document": {}})
    ).json()["impact"] is None


async def test_a_preview_persists_nothing_and_queues_nothing(
    client, auth_headers, session, app
):
    await _seed_library(session, app.state.config)
    before_renders = (
        await session.execute(
            select(Render.id, Render.fingerprint, Render.status).order_by(Render.id)
        )
    ).all()
    before_version = app.state.config.version

    response = await client.post(
        "/api/config/preview", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.status_code == 200

    assert (await session.execute(select(ConfigOverride))).scalars().all() == [], (
        "the preview stored the document it was only asked about"
    )
    assert (await session.execute(select(Job))).scalars().all() == [], (
        "the preview enqueued work"
    )
    assert (
        await session.execute(
            select(Render.id, Render.fingerprint, Render.status).order_by(Render.id)
        )
    ).all() == before_renders, "the preview mutated renders"
    assert app.state.config.version == before_version, "the preview swapped the generation"


async def test_a_preview_rejects_an_invalid_document_the_same_way(client, auth_headers):
    response = await client.post(
        "/api/config/preview", headers=auth_headers, json={"document": {"wrokers": 9}}
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["detail"]] == ["wrokers"]


async def test_a_preview_of_api_docs_enabled_reports_it_as_inert_not_restart_required(
    client, auth_headers
):
    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"api_docs_enabled": True}},
    )
    body = response.json()
    assert "api_docs_enabled" not in body["restart_required"]
    assert body["inert"] == ["api_docs_enabled"]


# --- apply ---


async def test_an_apply_saves_swaps_and_enqueues_the_affected_items(
    client, auth_headers, session, app
):
    await _seed_library(session, app.state.config)
    response = await client.post(
        "/api/config/apply", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 2 and body["skipped"] == 0
    assert body["version_after"] != body["version_before"]

    assert (await session.execute(select(ConfigOverride))).scalar_one().document == TEXT_EDIT
    assert app.state.config.artwork.title_card.season_label == "Kausi"
    jobs = (await session.execute(select(Job))).scalars().all()
    assert {job.kind for job in jobs} == {"process_item"}
    assert sorted(job.dedupe_key for job in jobs) == [
        "process_item:episode:tmdb1399:s01e01",
        "process_item:movie:tmdb550",
    ]
    # Each row's own Plex rating key rides along, the same way the full pass
    # and reprocess carry it (api/routes.py) -- without it, an apply over
    # adopted episode rows would enqueue jobs that resolve by episode-level
    # external ids instead, exactly the incident fix/guid-type-collision fixed.
    assert {job.dedupe_key: job.payload["rating_key"] for job in jobs} == {
        "process_item:episode:tmdb1399:s01e01": "rk2",
        "process_item:movie:tmdb550": "rk1",
    }


async def test_an_apply_respects_the_pending_dedupe(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    await enqueue(
        session, "process_item", {"kind": "movie", "title": "A Movie", "tmdb_id": 550},
        dedupe_key="process_item:movie:tmdb550",
    )
    body = (
        await client.post("/api/config/apply", headers=auth_headers, json={"document": TEXT_EDIT})
    ).json()
    assert body["queued"] == 1, "the already-pending movie was queued a second time"
    assert body["skipped"] == 1
    assert len((await session.execute(select(Job))).scalars().all()) == 2


async def test_an_apply_of_a_non_render_edit_queues_nothing(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    body = (
        await client.post(
            "/api/config/apply", headers=auth_headers,
            json={"document": {"scheduler": {"drift_days": 3}}},
        )
    ).json()
    assert body["queued"] == 0 and body["skipped"] == 0
    assert (await session.execute(select(Job))).scalars().all() == []
    assert (await session.execute(select(ConfigOverride))).scalar_one().document == {
        "scheduler": {"drift_days": 3}
    }


async def test_an_apply_of_a_non_render_edit_queues_nothing_even_for_logo_renders(
    client, auth_headers, session, app
):
    """The short-circuit, where it is actually observable.

    Without it, the walk's logo approximation reports this poster as affected
    by a scheduler cadence edit -- and apply would enqueue a re-render of every
    logo'd poster in the library on a change that cannot touch a pixel.
    """
    await _seed_logo_poster(session, app.state.config)
    body = (
        await client.post(
            "/api/config/apply", headers=auth_headers,
            json={"document": {"scheduler": {"collections_hours": 12}}},
        )
    ).json()
    assert body["queued"] == 0, (
        "a cadence edit queued a re-render, on the strength of an approximation "
        "the walk itself documents as an overcount"
    )
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_a_rejected_apply_neither_saves_nor_queues(client, auth_headers, session, app):
    await _seed_library(session, app.state.config)
    response = await client.post(
        "/api/config/apply", headers=auth_headers, json={"document": {"workers": "many"}}
    )
    assert response.status_code == 422
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []
    assert (await session.execute(select(Job))).scalars().all() == []


# --- redacted values and the keep sentinel ---
#
# `GET /api/config` serves `notifications.url` as its bare host. An editor
# that seeds its overrides document from the *served* values -- which is the
# only thing the page has -- would re-submit that host as the override on the
# operator's next unrelated save, and the stored URL (token and all) would be
# gone with nothing failing. Omitting the path instead would silently drop the
# override. The sentinel is the third option: the page says "keep", and the
# server, which still holds the stored value, resolves it.


def _seed_document(body: dict) -> dict:
    """The overrides document `api/overrides.ts::documentFromConfig` builds.

    Mirrored here rather than imagined, because the corruption this section
    guards against is a property of that seeding meeting this response. Kept
    in step with the TypeScript by hand -- there is one rule and it is two
    lines long: an overridden path contributes its served value, unless the
    response says the value was redacted, in which case it contributes the
    sentinel.
    """
    redacted = body.get("redacted_paths", [])
    sentinel = body.get("keep_sentinel")
    document: dict = {}
    for path in body["overridden_paths"]:
        if path in redacted and isinstance(sentinel, str):
            value = sentinel
        else:
            value = body
            for part in path.split("."):
                value = value[part]
        target = document
        parts = path.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return document


WEBHOOK_URL = "https://kuma.example.com/api/push/s3cr3tPushToken?status=up"


async def test_an_unrelated_save_does_not_destroy_a_redacted_override(
    client, auth_headers, session, app
):
    """The corruption repro, end to end and with no mocking.

    Store a notifications URL, read the config back the way the page does,
    build the page's seed document from that response, change one unrelated
    field, and save. The stored URL must come back byte-identical. Without
    the sentinel this stores `kuma.example.com` -- a valid string for a
    `str`-typed field, so nothing anywhere reports a problem -- and the push
    token is unrecoverable from the service.
    """
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"enabled": True, "url": WEBHOOK_URL}}},
    )
    assert app.state.config.notifications.url == WEBHOOK_URL

    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["notifications"]["url"] == "kuma.example.com", "the redaction stopped"

    document = _seed_document(body)
    document["workers"] = 9
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": document}
    )
    assert response.status_code == 200, response.json()

    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored["notifications"]["url"] == WEBHOOK_URL, (
        "the editor's redacted view of the URL was stored as the override; the "
        "push token is gone and the webhook now posts to a host with no path"
    )
    assert app.state.config.notifications.url == WEBHOOK_URL
    assert app.state.config.workers == 9, "the edit the operator actually made"


async def test_the_sentinel_never_reaches_storage_or_the_running_config(
    client, auth_headers, session, app
):
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"url": WEBHOOK_URL}}},
    )
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"url": KEEP_SENTINEL}}},
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert KEEP_SENTINEL not in json.dumps(stored)
    assert app.state.config.notifications.url == WEBHOOK_URL


async def test_get_config_advertises_what_it_redacted_and_the_marker_to_send_back(
    client, auth_headers
):
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["redacted_paths"] == ["notifications.url"]
    assert body["keep_sentinel"] == KEEP_SENTINEL
    assert body["keep_sentinel"], "a client with no marker cannot keep anything"


async def test_the_sentinel_at_a_path_that_was_never_redacted_is_a_422(
    client, auth_headers, session
):
    """Nothing was withheld at `plex.url`, so there is nothing to ask back for
    -- and resolving it would mean guessing which stored value was meant."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"plex": {"url": KEEP_SENTINEL}}},
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["detail"]] == ["plex.url"]
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


async def test_the_sentinel_inside_a_list_is_a_422_at_the_lists_own_path(
    client, auth_headers
):
    """A list is merged whole, so a sentinel in one slot has no stored scalar
    to resolve against; half-resolving it would store the marker."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"plex": {"excluded_libraries": ["Photos", KEEP_SENTINEL]}}},
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["detail"]] == ["plex.excluded_libraries"]


async def test_the_sentinel_with_no_stored_override_is_a_422(client, auth_headers, session):
    """"Keep what is stored" when nothing is stored. Answering it with the
    mounted file's value would quietly turn today's file value into an
    override and freeze it against every future change to the YAML."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"url": KEEP_SENTINEL}}},
    )
    assert response.status_code == 422
    assert [e["path"] for e in response.json()["detail"]] == ["notifications.url"]
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


async def test_the_sentinel_resolves_on_preview_and_apply_too(client, auth_headers, session):
    """The three write-path endpoints share one validator; a sentinel that
    worked only on PUT would make Preview lie about the save it previews."""
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"url": WEBHOOK_URL}}},
    )
    document = {"notifications": {"url": KEEP_SENTINEL}, "scheduler": {"drift_days": 3}}

    preview = await client.post("/api/config/preview", headers=auth_headers, json={"document": document})
    assert preview.status_code == 200, preview.json()

    applied = await client.post("/api/config/apply", headers=auth_headers, json={"document": document})
    assert applied.status_code == 200, applied.json()
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored["notifications"]["url"] == WEBHOOK_URL
