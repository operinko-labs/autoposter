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
import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from autoposter.api.auth import hash_password
from autoposter.api.routes import (
    KEEP_SENTINEL,
    STORE_CONVERTED_REFUSAL,
    _render_affecting,
)
from autoposter.app import create_app
from autoposter.config.loader import build_config, read_config_document, render_version_for
from autoposter.config.overrides import (
    DELTA_WITHOUT_FILE,
    EMPTY_DOCUMENT_REVISION,
    OVERRIDES_INSERT_LOCK_KEY,
    STORE_FORMAT,
    load_effective_config,
    load_store,
    merge_overrides,
    seed_store,
)
from autoposter.config.schema import Secrets
from autoposter.db.models import (
    ConfigOverride,
    ConfigOverrideSnapshot,
    EventLog,
    Job,
    ManagedCollection,
    Render,
)
from autoposter.plex.client import ResolvedItem
from autoposter.queue.jobs import enqueue
from autoposter.render.pipeline import compute_fingerprint, gather_fingerprint_inputs

from conftest import seed_media_item

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


@pytest.fixture
def file_document(config_file) -> dict:
    """The mounted file's document: the whole configuration, which is also
    exactly what a seeded store holds."""
    return yaml.safe_load(config_file.read_text(encoding="utf-8"))


def _whole(base: dict, patch: dict) -> dict:
    """``patch`` over a whole configuration document.

    A store that says it holds the whole configuration validates what arrives
    on its own, without the mounted file under it -- so a body carrying only
    the leaves a test cares about would be refused for every required setting
    it left out, and no page can produce one either. Tests that set up such a
    store build their documents through here.
    """
    return merge_overrides(base, patch)


async def _seed_library(session, config) -> None:
    """One movie poster and one episode title card, correctly fingerprinted."""
    rows = [
        (
            ResolvedItem(
                server="plex", native_id="rk1", library="Movies", kind="movie", title="A Movie",
                year=1999, season_number=None, episode_number=None,
                root_folder="A Movie (1999)", file_path=None, art_url=None,
                tmdb_id=550, tvdb_id=None, imdb_id=None,
            ),
            "poster",
        ),
        (
            ResolvedItem(
                server="plex", native_id="rk2", library="TV Shows", kind="episode", title="Pilot",
                year=1999, season_number=1, episode_number=1,
                root_folder="A Show (1999)", file_path=None, art_url=None,
                tmdb_id=1399, tvdb_id=None, imdb_id=None,
            ),
            "title_card",
        ),
    ]
    for item, art_kind in rows:
        row = await seed_media_item(
            session, item.native_id, library=item.library, kind=item.kind,
            title=item.title, year=item.year, tmdb_id=item.tmdb_id,
            season_number=item.season_number, episode_number=item.episode_number,
            root_folder=item.root_folder,
        )
        text_inputs, asset_hashes = await gather_fingerprint_inputs(config, item, art_kind)
        session.add(
            Render(
                item_id=row.id, art_kind=art_kind, status="rendered",
                asset_path=f"/assets/{item.title}/{art_kind}.jpg",
                source_url=f"https://example/{art_kind}", base_sha256="a" * 64,
                fingerprint=compute_fingerprint(
                    render_version_for(art_kind, config), art_kind,
                    f"https://example/{art_kind}", "a" * 64, text_inputs, asset_hashes,
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
        server="plex", native_id="rk9", library="Movies", kind="movie", title="Logo Movie",
        year=1999, season_number=None, episode_number=None,
        root_folder="Logo Movie (1999)", file_path=None, art_url=None,
        tmdb_id=680, tvdb_id=None, imdb_id=None,
    )
    row = await seed_media_item(
        session, item.native_id, library=item.library, kind=item.kind,
        title=item.title, year=item.year, tmdb_id=item.tmdb_id, root_folder=item.root_folder,
    )
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, item, "poster", draw_text=False, logo_sha="deadbeef"
    )
    session.add(
        Render(
            item_id=row.id, art_kind="poster", status="rendered",
            asset_path="/assets/Logo Movie/poster.jpg",
            source_url="https://example/poster", base_sha256="a" * 64,
            fingerprint=compute_fingerprint(
                render_version_for("poster", config), "poster",
                "https://example/poster", "a" * 64, text_inputs, asset_hashes,
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


async def test_get_config_serves_a_saved_value_rather_than_marking_it(
    client, auth_headers
):
    """The response carries no provenance any more, and it does not need to:
    the stored document IS the configuration, so the answer to "what is this
    set to" is the value on the row and nothing beside it."""
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"workers": 9, **TEXT_EDIT}},
    )
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert "overridden_paths" not in body
    assert body["workers"] == 9
    assert body["artwork"]["title_card"]["season_label"] == "Kausi"


async def test_get_config_carries_an_empty_restart_list_by_default(client, auth_headers):
    """``restart_paths`` comes off the store's own metadata, so a store nobody
    has written a restart list into serves an empty one rather than no key --
    the page renders one shape whatever the row says."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["restart_paths"] == []


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
    "restart_paths",
    "frozen_paths",
    "redacted_paths",
    "keep_sentinel",
    "field_descriptions",
    "computed_paths",
    "live_paths",
    # The eighth, and provenance in the same sense as the rest: a content hash
    # of the stored document, not a setting anybody edits.
    "overrides_revision",
}


async def test_provenance_keys_names_exactly_the_keys_the_response_adds(app, client, auth_headers):
    """Roadmap row 237. Set equality gives both directions for free.

    ``GET /api/config`` serves the config's own fields plus ``secrets`` plus
    eight provenance keys the handler adds (``api/routes.py:1621-1632``).
    Subtracting the settings side leaves exactly the provenance keys, so an
    added provenance key lands on the left of this equality and a key dropped
    from the set lands on the right -- the same two-way shape row 107 gave
    ``SCHEDULED_JOB_NAMES``.

    ``secrets`` is named explicitly because it is not a ``Config`` field: the
    handler adds it from the separate ``Secrets`` model at ``routes.py:1609``.
    """
    body = (await client.get("/api/config", headers=auth_headers)).json()
    settings = set(app.state.config.model_dump(mode="json")) | {"secrets"}

    assert set(body) - settings == PROVENANCE_KEYS, (
        "the keys GET /api/config adds on top of the settings and "
        "PROVENANCE_KEYS have drifted: a provenance key this set does not "
        "name renders as an editable field the API is bound to reject"
    )


def test_the_frontends_provenance_keys_match_the_python_set():
    """The half a Python-only guard cannot reach: the row's own symptom
    ("renders as an editable field") is a FRONTEND symptom, and
    ``api/overrides.ts`` filters both the rendered sections and the document
    every page saves by its own copy of this set.

    Reads the source file as text and regexes out the string literals rather
    than parsing TypeScript, so the pin survives reformatting -- the idiom
    ``tests/test_action_flags.py:472-495`` already uses against
    ``ActionCenter.tsx``'s ``ART_KINDS``.

    One difference from that idiom, and it is load-bearing: the array's own
    comments may contain quoted phrases, so ``//`` line comments are stripped
    before the literals are read. Without that the comment's words would join
    the set and this test would pass on a broken array.

    ``secrets`` is the one key on the TypeScript side that is not on this one,
    and it is not an oversight either way: it is not a provenance key the
    handler adds on top of the settings (this set's subject), it is the
    separate ``Secrets`` model -- and the document a page sends must drop it
    all the same, because ``merge_overrides`` refuses the key outright.
    """
    import re

    frontend = (
        Path(__file__).parent.parent / "frontend" / "src" / "api" / "overrides.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"const PROVENANCE_KEYS = \[([^\]]*)\];", frontend)
    assert match is not None, "api/overrides.ts no longer declares a const PROVENANCE_KEYS = [...]"
    literals = set(re.findall(r'"([^"]*)"', re.sub(r"//[^\n]*", "", match.group(1))))

    assert literals == PROVENANCE_KEYS | {"secrets"}


def _wildcarded(path: str) -> str:
    """A served path with its library name replaced by the map's wildcard.

    ``field_descriptions`` describes the per-library shape ONCE, under
    ``libraries.{}.``, because a Plex library name is data and a schema walk
    cannot enumerate it (roadmap row 92). Substituting here rather than
    exempting the section keeps the guard's promise intact: every served leaf
    still has to have a description, including these.
    """
    segments = path.split(".")
    if len(segments) > 2 and segments[0] == "libraries":
        return ".".join(["libraries", "{}", *segments[2:]])
    return path


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
    undescribed = [
        path for path in served if _wildcarded(path) not in descriptions
    ]
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
    # The example config's value, not the -1 the refused document asked for.
    # Read off the response rather than off `app.state.config`, which is what
    # the response was dumped from and so cannot disagree with it.
    assert body["workers"] == 5


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


async def test_an_empty_object_override_is_a_422(client, auth_headers, session):
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"artwork": {}}}
    )
    assert response.status_code == 422, (
        "an empty object is not a leaf document_paths can report honestly -- "
        "it would seed the editor into storing the whole artwork section "
        "wholesale on the next save"
    )
    assert [e["path"] for e in response.json()["detail"]] == ["artwork"]
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []


def _toggled_leaf(value):
    """A same-typed, different value for one leaf, or ``None`` if this walk
    does not know how to change the type safely (yields no candidate for the
    caller to try)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "-drift-probe"
    if isinstance(value, list) and value:
        return value + [value[-1]]
    return None


def _document_leaves(value, path=""):
    """Every (dotted_path, value) pair at a non-dict leaf, depth-first."""
    if isinstance(value, dict):
        for key, sub in value.items():
            where = f"{path}.{key}" if path else str(key)
            yield from _document_leaves(sub, where)
    else:
        yield path, value


def _nested_override(dotted_path: str, value):
    """The minimal override document that sets exactly one dotted leaf."""
    result = value
    for part in reversed(dotted_path.split(".")):
        result = {part: result}
    return result


def test_render_affecting_matches_render_versions_own_input_set():
    """``_render_affecting`` is a hand-maintained enumeration -- its own
    docstring says so -- of exactly what ``render_version`` hashes plus
    ``skip_tba``. This does not repeat that enumeration by name: for every
    top-level section the example config actually has, it changes one real
    leaf and asks ``render_version`` itself whether that moved the hash, then
    checks ``_render_affecting`` agrees. A future field that starts (or
    stops) feeding either one without the other being updated fails here
    instead of silently under- or over-reporting impact.

    No behaviour change accompanies this test -- ``_render_affecting`` is
    unedited. Confirmed to already hold for every one of the example
    config's 29 top-level sections before this test existed: this is a
    regression guard, not a bug fix.

    A section whose example value is an empty mapping is skipped by
    CONDITION rather than by name (roadmap row 92): an
    empty mapping has no leaf at all for this generic mechanism to toggle,
    whatever the section happens to be called. Today that is only
    ``libraries`` (roadmap row 92; the shipped default is `{}`), so this
    self-repairs if that ever changes -- a `libraries:` example gaining real
    content would be walked like any other section instead of staying
    silently exempted forever. ``section == {}`` rather than ``not
    section``: ``skip_tba`` is a top-level scalar whose example value is
    ``False``, which ``not section`` would wrongly skip too.

    ``libraries``' own render-non-effect is proven directly instead, by
    ``test_library_overrides.py::test_the_libraries_section_moves_no_render_version``,
    which sets a representative leaf of each of the three whitelisted
    sections -- 9 of the 27 available leaves -- across the 2 configured
    libraries and checks all five versions directly. Not exhaustive over
    every leaf; the wholesale-hash argument
    (``test_library_overrides.py::test_render_version_hashes_exactly_six_named_inputs``)
    is what makes a representative leaf enough.
    """
    base = read_config_document(EXAMPLE)
    before = build_config(base)

    checked = 0
    for key, section in base.items():
        if section == {}:
            continue
        for dotted, leaf in _document_leaves(section, key):
            candidate = _toggled_leaf(leaf)
            if candidate is None or candidate == leaf:
                continue
            try:
                after = build_config(merge_overrides(base, _nested_override(dotted, candidate)))
            except (ValidationError, ValueError):
                continue
            checked += 1
            version_changed = before.version != after.version
            expected = version_changed or key == "skip_tba"
            assert _render_affecting(before, after) is expected, (
                f"{dotted}: render_version changed={version_changed}, "
                f"_render_affecting()={_render_affecting(before, after)}"
            )
            break  # one leaf is enough to place this section
        else:
            continue

    expected_checked = sum(1 for section in base.values() if section != {})
    assert checked == expected_checked, (
        f"only {checked} of {expected_checked} top-level sections had a leaf "
        "this walk could safely mutate -- every non-empty section in the "
        "example config was expected to have one"
    )


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

    # Clearing every override is still the documented revert-everything -- it
    # is now a *deliberate* one (OVERRIDE_DROP_CAP's empty-document arm).
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )
    assert app.state.config.workers == 5, "the example config's value did not come back"
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["workers"] == 5, "the served config still shows the cleared value"


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
    application object before the overrides are read), and unlike every other
    frozen path not even the lifespan's own merge reaches it: it is settled
    before a single override is read. ``restart_required`` is the paths that
    merge applies, so this one is reported beside it rather than in it -- two
    lists because they land at two moments, not because one of them is
    hopeless. The restart list on the row carries both."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"api_docs_enabled": True}},
    )
    body = response.json()
    assert "api_docs_enabled" not in body["restart_required"]
    assert body["inert"] == ["api_docs_enabled"]


def _settings_of(body: dict) -> dict:
    """The editable settings out of a ``GET /api/config`` response.

    Everything the response adds on top of the configuration, plus the secrets
    it redacts wholesale -- which ``merge_overrides`` refuses outright -- comes
    back off, leaving the document a save sends.
    """
    return {
        key: value
        for key, value in body.items()
        if key not in PROVENANCE_KEYS and key != "secrets"
    }


@pytest_asyncio.fixture
async def seeded_store(session_factory, config_file):
    """The store as the first boot leaves it: the whole configuration.

    The tests below send the document back the way the settings page does --
    whole, every key it was served -- and that is only a legal save against a
    store already holding a whole document. A delta-era store would refuse it,
    and rightly: ``{}`` spelled in the mounted file is an unset optional
    section, not an override of one.
    """
    async with session_factory() as session:
        await seed_store(session, read_config_document(config_file))
        await session.commit()


async def test_a_frozen_save_is_remembered_across_a_reload(
    client, auth_headers, seeded_store
):
    """The restart list is kept in the document's metadata, so it survives a
    reload and shows to another admin -- the notice outlives the page that
    caused it."""
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["workers"] = document["workers"] + 1
    response = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["restart_required"] == ["workers"]

    reloaded = (await client.get("/api/config", headers=auth_headers)).json()
    assert reloaded["restart_paths"] == ["workers"]


async def test_two_frozen_saves_both_stay_on_the_list(
    client, auth_headers, seeded_store
):
    """Both paths differ from the booted generation, so a replacement keeps
    both: the second save's set is computed from the same place the first
    save's was and carries the first save's path as well as its own."""
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["workers"] = document["workers"] + 1
    first = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["scheduler"]["poll_seconds"] = document["scheduler"]["poll_seconds"] + 1
    second = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert second.status_code == 200, second.text
    reloaded = (await client.get("/api/config", headers=auth_headers)).json()
    assert reloaded["restart_paths"] == ["scheduler.poll_seconds", "workers"]


async def test_a_live_save_neither_adds_to_the_list_nor_forgets_it(
    client, auth_headers, seeded_store
):
    """A live save changes nothing a restart would apply -- and it must not
    drop the claim an earlier frozen save left standing either, which is the
    one thing a list rewritten on every save could get wrong."""
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["workers"] = document["workers"] + 1
    frozen = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert frozen.status_code == 200, frozen.text

    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["artwork"]["title_card"]["season_label"] = "Kausi"
    live = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert live.status_code == 200, live.text
    assert live.json()["restart_required"] == []
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == ["workers"]


async def test_a_frozen_setting_put_back_comes_off_the_list(
    client, auth_headers, seeded_store
):
    """The list answers one question -- would a restart change anything? -- so
    a setting edited and then set back to the value this process booted on has
    to come off it. Measured against the previous save instead, the path would
    go on a second time and nothing would ever take it off again."""
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    booted_workers = seed["workers"]
    document = _settings_of(seed)
    document["workers"] = booted_workers + 1
    away = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert away.status_code == 200, away.text
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == ["workers"]

    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["workers"] = booted_workers
    back = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert back.status_code == 200, back.text
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == [], "the banner would ask for a restart that would change nothing"


async def test_an_inert_save_goes_on_the_list_too(client, auth_headers):
    """It is reported apart from ``restart_required`` because it lands at a
    different moment of the boot, but a restart is what applies it -- the boot
    builds the application object from the stored document -- so an operator
    who changed it has exactly one thing to do, and the list has to say so."""
    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"api_docs_enabled": True}},
    )
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == ["api_docs_enabled"]


async def test_an_inert_change_is_measured_against_the_constructed_generation(
    app, client, auth_headers
):
    """The one setting on the list that a merge never settles.

    ``api_docs_enabled`` is read when the application OBJECT is built, from
    the document ``create_app`` was handed -- and on the one-time
    delta-conversion boot, or on any boot whose bounded store read timed out,
    that document is the mounted file while the generation the lifespan then
    merges and records as ``booted_config`` is file-plus-overrides. Measured
    against the merged one, the save that really does turn the docs on comes
    back "nothing waiting", and the operator's only remedy is the one thing
    nothing tells them to do.

    A boot of exactly that shape below: the object was built with the docs
    off, and the merge that ran after it had them on.
    """
    app.state.booted_config = app.state.booted_config.model_copy(
        update={"api_docs_enabled": True}
    )

    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"api_docs_enabled": True}},
    )
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == ["api_docs_enabled"], (
        "the running application object has the docs off and the stored "
        "document now asks for them on; only a restart closes that gap"
    )

    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"api_docs_enabled": False}},
    )
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == [], "back to what this application object was built with, so nothing waits"


async def test_a_restore_puts_its_frozen_changes_on_the_list(
    client, auth_headers, seeded_store
):
    """A restore is a save: it goes through the same write, so a frozen
    setting it brings back waits for a restart exactly as one typed into the
    page does. Recovery is the moment an operator most needs to be told.

    Set away and then back first, so the store holds a snapshot that differs
    from the running configuration and the list is empty to start from.
    """
    seed = (await client.get("/api/config", headers=auth_headers)).json()
    booted_workers = seed["workers"]
    document = _settings_of(seed)
    document["workers"] = booted_workers + 1
    away = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert away.status_code == 200, away.text

    seed = (await client.get("/api/config", headers=auth_headers)).json()
    document = _settings_of(seed)
    document["workers"] = booted_workers
    back = await client.put(
        "/api/config/overrides",
        json={"document": document, "expected_revision": seed["overrides_revision"]},
        headers=auth_headers,
    )
    assert back.status_code == 200, back.text
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == [], "precondition: nothing is waiting for a restart"

    # Newest first, so this is the document the revert above displaced.
    snapshots = (await client.get("/api/config/snapshots", headers=auth_headers)).json()
    response = await client.post(
        f"/api/config/snapshots/{snapshots[0]['id']}/restore",
        json={}, headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/api/config", headers=auth_headers)).json()[
        "restart_paths"
    ] == ["workers"]


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
    """Roadmap row 111 at the endpoint: a title-card edit reports the title
    card, not the poster beside it. `of_total` stays 2 because the poster was
    still EXAMINED -- it is in the population the operator is being told a
    fraction of."""
    await _seed_library(session, app.state.config)
    response = await client.post(
        "/api/config/preview", headers=auth_headers, json={"document": TEXT_EDIT}
    )
    assert response.status_code == 200
    impact = response.json()["impact"]
    assert impact["of_total"] == 2
    assert impact["affected"] == 1
    assert impact["by_art_kind"] == {"title_card": 1}


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


async def test_a_preview_refuses_a_credential_bearing_param_without_echoing_it(
    client, auth_headers
):
    """The builder-params refusal through a real served surface. `_validated_generation`
    is shared by save, preview and apply, so pinning it here pins all three --
    and the served `detail[…]["message"]` is the composed `msg`, which is the
    string the validator's own sentence lands in."""
    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"collections": {"definitions": [
            {
                "title": "Leaky",
                "builder": "mdblist_list",
                "params": {"list": "https://mdblist.com/lists/a/b?apikey=SECRET"},
            },
        ]}}},
    )

    assert response.status_code == 422
    assert "SECRET" not in response.text, "the preview handed the pasted key back"
    detail = response.json()["detail"]
    # The definition's own `model_validator` raises at the model's location, one
    # level below the freezing guard's hardcoded "collections.definitions" (see
    # `test_the_preview_refuses_the_same_document_the_save_does`) -- this error
    # comes from the list item itself, so its `loc` carries the index.
    assert detail[0]["path"] == "collections.definitions.0"
    assert "not an MDBList list" in detail[0]["message"]


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


async def test_a_preview_counts_the_collection_posters_a_row_105_edit_moves(
    client, auth_headers, session, app
):
    """Row 105's honesty row, through the endpoint that serves it.

    A collections edit moves no render fingerprint -- ``render_version``
    excludes the whole section -- so ``impact`` is null here, which is exactly
    the case the count exists for: null impact beside a real cost.
    """
    session.add(
        ManagedCollection(
            library="Movies", title="A Collection", kind="smart",
            definition_hash="d" * 64,
        )
    )
    await session.commit()

    response = await client.post(
        "/api/config/preview", headers=auth_headers,
        json={"document": {"collections": {"poster_title": {"enabled": True}}}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["impact"] is None
    assert body["collection_posters"] == 1


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
    # Row 111: a title-card edit enqueues the episode and leaves the movie
    # alone. Before it, this was 2 -- a poster re-render charged to an edit
    # that could not touch a poster.
    assert body["queued"] == 1 and body["skipped"] == 0
    assert body["version_after"] != body["version_before"]

    assert (await session.execute(select(ConfigOverride))).scalar_one().document == TEXT_EDIT
    assert app.state.config.artwork.title_card.season_label == "Kausi"
    jobs = (await session.execute(select(Job))).scalars().all()
    assert {job.kind for job in jobs} == {"process_item"}
    assert sorted(job.dedupe_key for job in jobs) == [
        "process_item:episode:tmdb1399:s01e01",
    ]
    # Each row's own Plex rating key rides along, the same way the full pass
    # and reprocess carry it (api/routes.py) -- without it, an apply over
    # adopted episode rows would enqueue jobs that resolve by episode-level
    # external ids instead, exactly the incident fix/guid-type-collision fixed.
    assert {job.dedupe_key: job.payload["refs"]["plex"] for job in jobs} == {
        "process_item:episode:tmdb1399:s01e01": "rk2",
    }


async def test_an_apply_respects_the_pending_dedupe(client, auth_headers, session, app):
    """The edit is a SHARED input (`artwork.output_quality`, read by
    build_base_argv on every kind) rather than the title-card edit this used
    to take: since row 111 a title-card edit no longer reaches the movie at
    all, and the dedupe this pins is about an item that IS affected and is
    already queued."""
    await _seed_library(session, app.state.config)
    await enqueue(
        session, "process_item", {"kind": "movie", "title": "A Movie", "tmdb_id": 550},
        dedupe_key="process_item:movie:tmdb550",
    )
    body = (
        await client.post(
            "/api/config/apply", headers=auth_headers,
            json={"document": {"artwork": {"output_quality": "88%"}}},
        )
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
    lines long: the whole served configuration minus the keys that are not
    settings, and the sentinel wherever the response says the value it served
    was redacted.
    """
    sentinel = body.get("keep_sentinel")
    document = deepcopy(
        {key: value for key, value in body.items() if key not in PROVENANCE_KEYS | {"secrets"}}
    )
    if not isinstance(sentinel, str):
        return document
    for path in body.get("redacted_paths", []):
        parts = path.split(".")
        target = document
        for part in parts[:-1]:
            if not isinstance(target, dict) or part not in target:
                target = None
                break
            target = target[part]
        if isinstance(target, dict) and parts[-1] in target:
            target[parts[-1]] = sentinel
    return document


WEBHOOK_URL = "https://kuma.example.com/api/push/s3cr3tPushToken?status=up"


async def test_an_unrelated_save_does_not_destroy_a_redacted_override(
    client, auth_headers, session, app, file_document
):
    """The corruption repro, end to end and with no mocking.

    Store a notifications URL, read the config back the way the page does,
    build the page's seed document from that response, change one unrelated
    field, and save. The stored URL must come back byte-identical. Without
    the sentinel this stores `kuma.example.com` -- a valid string for a
    `str`-typed field, so nothing anywhere reports a problem -- and the push
    token is unrecoverable from the service.

    The store is seeded first, because the seam this guards is the page's:
    what a page sends is the whole configuration, which only a store that
    holds one ever receives.
    """
    async with app.state.session_factory() as setup:
        await seed_store(setup, file_document)
        await setup.commit()

    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={
            "document": _whole(
                file_document,
                {"notifications": {"enabled": True, "url": WEBHOOK_URL}},
            )
        },
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
    """What THIS body withheld, not what the endpoint withholds in general.

    A client cannot re-derive the list from the served values: the reduction
    answers `""` both for a stored URL it can find no host in and for a setting
    that was never set, so from outside the two are the same string. The list
    is collected as the redaction happens, and it moves when the setting does.
    """
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["redacted_paths"] == [], "nothing is stored at that path yet"
    assert body["keep_sentinel"] == KEEP_SENTINEL
    assert body["keep_sentinel"], "a client with no marker cannot keep anything"

    await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": {"notifications": {"url": WEBHOOK_URL}}},
    )

    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["redacted_paths"] == ["notifications.url"]
    assert body["notifications"]["url"] != WEBHOOK_URL, "and it really was reduced"


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


# --- The definitions guard (row 138) -------------------------------------
#
# The panel refuses to CREATE while any listed definition comes from the
# mounted file, because an overrides list replaces the file's WHOLESALE: the
# first stored list would silently stop every file row from being built, and
# copying the file's rows in to "preserve" them is the freezing hazard. That
# refusal was UI-only. These pin it in the API, where a UI cannot be bypassed.
#
# The predicate is the FIRST such store, not "the file lists definitions":
# once an override is stored the file's list is already shadowed, which is the
# state the panel edits in, and refusing there would brick the editor.


def _with_file_definitions(config_file: Path) -> None:
    """Give the mounted file a non-empty ``collections.definitions``."""
    document = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    document["collections"]["definitions"] = [
        {"title": "Hand Picked", "builder": "plex_id", "params": {"ids": ["12345"]}}
    ]
    config_file.write_text(yaml.safe_dump(document), encoding="utf-8")


AN_OVERRIDE_LIST = {
    "collections": {
        "definitions": [
            {"title": "Star Wars", "builder": "tmdb_collection", "params": {"id": 10}}
        ]
    }
}


async def _store(session_factory, document: dict) -> None:
    """Put a stored overrides document in place, the way a prior save would."""
    async with session_factory() as session:
        stmt = insert(ConfigOverride).values(id=1, document=document)
        stmt = stmt.on_conflict_do_update(index_elements=["id"], set_={"document": document})
        await session.execute(stmt)
        await session.commit()


async def test_the_first_definitions_override_is_refused_while_the_file_lists_some(
    client, auth_headers, config_file, session_factory
):
    """The freezing guard, API-side. The refusal names the path so the panel's
    `fieldErrors` renders it against `collections.definitions`."""
    _with_file_definitions(config_file)

    response = await client.put(
        "/api/config/overrides", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [item["path"] for item in detail] == ["collections.definitions"]
    assert "replaces" in detail[0]["message"]

    # Never half-apply: no row was written.
    async with session_factory() as session:
        assert (await session.execute(select(ConfigOverride))).scalars().first() is None


async def test_the_preview_refuses_the_same_document_the_save_does(
    client, auth_headers, config_file
):
    """Check must not answer "the server would accept this" about a document
    the server refuses -- the panel's Check button offers exactly that."""
    _with_file_definitions(config_file)

    response = await client.post(
        "/api/config/preview", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "collections.definitions"


async def test_apply_refuses_the_same_document_the_save_does(
    client, auth_headers, config_file
):
    """`POST /api/config/apply` shares `_validated_generation` with the save
    and the preview, so the guard is not something each caller re-implements
    -- pinned directly rather than trusted by inference from the other two."""
    _with_file_definitions(config_file)

    response = await client.post(
        "/api/config/apply", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "collections.definitions"


async def test_editing_a_stored_definitions_override_is_allowed_while_the_file_lists_some(
    client, auth_headers, config_file, session_factory
):
    """The state the editor lives in. The file's list is already shadowed by
    the stored one, so the destructive transition has already happened and
    refusing here would brick every subsequent edit."""
    _with_file_definitions(config_file)
    await _store(session_factory, AN_OVERRIDE_LIST)
    edited = {
        "collections": {
            "definitions": [
                {
                    "title": "Star Wars",
                    "builder": "tmdb_collection",
                    "params": {"id": 10},
                    "limit": 25,
                }
            ]
        }
    }

    response = await client.put(
        "/api/config/overrides", json={"document": edited}, headers=auth_headers
    )

    assert response.status_code == 200


async def test_the_first_definitions_override_is_allowed_when_the_file_lists_none(
    client, auth_headers
):
    """The migrated deployment: an empty `definitions:` in the YAML is the
    second way forward the panel's guard note names."""
    response = await client.put(
        "/api/config/overrides", json={"document": AN_OVERRIDE_LIST}, headers=auth_headers
    )

    assert response.status_code == 200


async def test_an_unrelated_save_is_untouched_while_the_file_lists_definitions(
    client, auth_headers, config_file
):
    """The guard is about one key. An operator editing a text setting must not
    be refused because their YAML happens to define collections."""
    _with_file_definitions(config_file)

    response = await client.put(
        "/api/config/overrides", json={"document": TEXT_EDIT}, headers=auth_headers
    )

    assert response.status_code == 200


# --- A token-bearing `smart_url` PUT is refused end to end -------------------
#
# The whole security posture for a pasted Plex Web URL rests on
# `_validated_generation` (routes.py) projecting only `_dotted(e["loc"])` and
# `e["msg"]` from the `ValidationError` `build_config` raises, never `input`.
# `tests/test_builder_smart_url.py` pins that at the model; this pins it
# through the real `PUT /api/config/overrides` endpoint, so a later edit that
# starts serving `str(exc)` or `e["input"]` fails here instead of shipping a
# live Plex token to a browser and a reverse-proxy access log.

_TOKEN_BEARING_URL = (
    "http://192.168.1.50:32400/web/index.html#!/server/abc123/"
    "com.plexapp.plugins.library"
    "?key=%2Flibrary%2Fsections%2F1%2Fall%3Ftype%3D1%26sort%3Drandom%26genre%3D1138"
    "&X-Plex-Token=SEKRIT"
)

TOKEN_BEARING_DEFINITIONS = {
    "collections": {
        "definitions": [
            {"title": "Pasted", "builder": "smart_url", "params": {"url": _TOKEN_BEARING_URL}}
        ]
    }
}


async def test_a_token_bearing_smart_url_override_is_refused_with_a_token_free_body(
    client, auth_headers, session_factory
):
    """PUT a `smart_url` definition whose `params.url` carries both a live
    `X-Plex-Token` and the operator's own intranet host. The response must be
    422 with neither the token, the host nor the raw URL anywhere in the
    body; the refusal sentence must be the one actually served; and nothing
    may be stored -- the mounted config's `collections.definitions` is empty,
    so the definitions guard (above) does not intercept this before
    `build_config` gets to it."""
    response = await client.put(
        "/api/config/overrides",
        json={"document": TOKEN_BEARING_DEFINITIONS},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert "SEKRIT" not in response.text
    assert "192.168.1.50" not in response.text
    assert _TOKEN_BEARING_URL not in response.text
    detail = response.json()["detail"]
    assert any("params.url carries a Plex token" in item["message"] for item in detail)

    # Never half-apply: no override row was written.
    async with session_factory() as session:
        assert (await session.execute(select(ConfigOverride))).scalars().first() is None
    served = (await client.get("/api/config", headers=auth_headers)).json()
    assert served["collections"]["definitions"] == [], "the refused definition is served"


# --- The wholesale-replace law (row 138's editor is built on it) ----------
#
# CHARACTERIZATION, not RED-first: these pin behavior `merge_overrides` and
# `GET /api/config` already have. They exist so the day the panel's splice is
# rewritten as a rebuild, something says so out loud.

THREE_DEFINITIONS = {
    "collections": {
        "definitions": [
            {"title": "First", "builder": "tmdb_collection", "params": {"id": 1}},
            {
                "title": "Second",
                "builder": "mdblist_list",
                "params": {"list": "someone/weekly"},
                "summary": "What the household watched.",
                "schedule": {"every_n_runs": 3},
                "changes_webhook": "https://hooks.example/T0K3N/path",
                "item_label": ["Weekly"],
            },
            {"title": "Third", "builder": "tmdb_collection", "params": {"id": 3}},
        ]
    }
}


async def test_editing_one_definition_does_not_perturb_its_siblings(
    client, auth_headers
):
    """The negative assertion. A whole-list write that changed
    ONLY the entry the operator edited is the whole contract; entries either
    side must come back byte-identical -- the WHOLE dict, not just the two
    fields this test used to sample, since a sibling also carries `builder`
    and `params` that a careless splice could just as easily disturb."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )
    before = (await client.get("/api/config", headers=auth_headers)).json()
    sibling_first = before["collections"]["definitions"][0]
    sibling_third = before["collections"]["definitions"][2]

    entries = deepcopy(THREE_DEFINITIONS["collections"]["definitions"])
    entries[1] = {**entries[1], "limit": 25}

    response = await client.put(
        "/api/config/overrides",
        json={"document": {"collections": {"definitions": entries}}},
        headers=auth_headers,
    )
    assert response.status_code == 200

    served = (await client.get("/api/config", headers=auth_headers)).json()
    definitions = served["collections"]["definitions"]
    assert definitions[0] == sibling_first
    assert definitions[2] == sibling_third
    assert definitions[1]["limit"] == 25


async def test_an_edited_definition_keeps_every_field_the_edit_did_not_reach(
    client, auth_headers
):
    """The loss-free law. `summary`, `schedule`, `item_label` and
    `changes_webhook` are none of them in the editor's curated subset -- an
    entry rewritten around them has to bring them through untouched, which is
    what `{...storedEntry, ...edits}` buys and what a rebuild from the
    seven-field listing would destroy."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )
    entries = deepcopy(THREE_DEFINITIONS["collections"]["definitions"])
    entries[1] = {**entries[1], "limit": 25}

    await client.put(
        "/api/config/overrides",
        json={"document": {"collections": {"definitions": entries}}},
        headers=auth_headers,
    )

    served = (await client.get("/api/config", headers=auth_headers)).json()
    second = served["collections"]["definitions"][1]
    assert second["summary"] == "What the household watched."
    assert second["schedule"] == {"every_n_runs": 3, "months": None}
    assert second["item_label"] == ["Weekly"]
    assert second["changes_webhook"] == "https://hooks.example/T0K3N/path"


async def test_the_served_config_round_trips_a_definition_the_editor_reads_back(
    client, auth_headers
):
    """What the panel's `documentFromConfig` seeds from. The served
    `collections.definitions` IS the stored array, so an editor seeded from the
    GET writes back what it was given -- every key, at full depth."""
    await client.put(
        "/api/config/overrides", json={"document": THREE_DEFINITIONS}, headers=auth_headers
    )

    served = (await client.get("/api/config", headers=auth_headers)).json()

    assert len(served["collections"]["definitions"]) == 3
    stored_second = THREE_DEFINITIONS["collections"]["definitions"][1]
    served_second = served["collections"]["definitions"][1]
    for key, value in stored_second.items():
        if key == "schedule":
            continue  # pydantic fills the model's own unset field, checked above
        assert served_second[key] == value


# --- Config safety: the shape guard and the destructiveness guards -------
#
# These reproduce the 2026-09-01 incident.
# Defect D1: a PUT whose JSON body is a well-formed object that does NOT carry
# a top-level `document` key was silently read as "the operator's complete set
# of deltas is now empty" -- pydantic v2's default extra="ignore" matched it
# against zero declared fields and threw 757 bytes away -- and the unconditional
# upsert wrote {} over seventeen stored overrides with a 200.

THE_INCIDENT_DOCUMENT = {
    "workers": 9,
    "artwork": {
        "title_card": {"season_label": "Kausi"},
        "poster": {"text": {"min_point_size": 22}},
    },
    "plex": {"resolve_max_attempts": 7},
    "badges": {"enabled": False, "upload_to_plex": False},
    "notifications": {"enabled": False},
    "collections": {
        "separator_style": "sand",
        "ownership_label": "Autoposter",
        "delete_unconfigured": False,
        "max_deletes": 100,
    },
    "scheduler": {"enabled": False},
}


async def _put_document(client, auth_headers, document: dict) -> None:
    """Put a document in the store the ordinary way, and insist it landed."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": document}
    )
    assert response.status_code == 200, response.text


async def test_an_unwrapped_document_body_is_refused_not_silently_emptied(
    client, auth_headers, session
):
    """The incident's first PUT: the document sent bare, at top level.

    Before the fix this answered 200 and wiped the store. `document` defaulted
    to {}, {} is a fully valid document (it is the *documented* revert-
    everything), and _persist_and_swap wrote it unconditionally.
    """
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )

    assert response.status_code == 422, (
        "an unwrapped body was accepted. It binds to document={} and wipes the "
        "store with a 200 -- this is the incident"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT, "the refused request still wrote"


async def test_the_incident_restore_body_is_refused_rather_than_written_as_empty(
    client, auth_headers, session, app
):
    """The incident's second PUT: the same mistake over an already-empty row.

    757 bytes of perfectly good JSON, a 200, and version_before ==
    version_after *by construction* -- the merged config was the file alone,
    which was already what was running. Nothing landed and nothing said so.
    """
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )

    assert response.status_code == 422
    # FastAPI's request-validation shape, which the frontend's `fieldErrors`
    # already renders: the extras are named by `loc`.
    named = {tuple(entry["loc"]) for entry in response.json()["detail"]}
    assert ("body", "workers") in named, response.text
    assert (await session.execute(select(ConfigOverride))).scalars().all() == []
    assert app.state.config.workers == 5, "a refused body must not swap anything"


async def test_a_correctly_wrapped_body_is_untouched_by_the_shape_guard(
    client, auth_headers, session
):
    """The negative: the SPA's own body shape still saves. It sends exactly one
    key, so forbidding extras on the envelope cannot reach it."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_the_preview_refuses_the_unwrapped_body_the_same_way(
    client, auth_headers
):
    """All three arms share the model, so all three harden together."""
    response = await client.post(
        "/api/config/preview", headers=auth_headers, json=THE_INCIDENT_DOCUMENT
    )
    assert response.status_code == 422


# --- the request-body 422 does not echo the body back ------------------------
#
# FastAPI's default handler returns `jsonable_encoder(exc.errors())`, and
# pydantic puts the rejected `input` in every entry -- for the `missing` arm
# that input is the WHOLE body, so one refused paste hands back every
# credential-capable field the operator had in the document. `GET /api/config`
# reduces `notifications.url` to its host; this 422 used to hand the same class
# of value back in full. The handler in `app.py` keeps `type`/`loc`/`msg` (what
# `fieldErrors` renders) and drops the rest.

A_TOKEN_BEARING_DOCUMENT = {
    "plex": {"url": "http://plex.lan:32400/?X-Plex-Token=SEKRIT"},
    "scheduler": {},
}


async def test_an_unwrapped_body_is_refused_without_echoing_the_document(
    client, auth_headers
):
    """The incident's own body shape, carrying a token. The refusal must still
    name the missing field and the extras -- that is what the editor renders --
    without repeating one character of the document."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json=A_TOKEN_BEARING_DOCUMENT
    )

    assert response.status_code == 422
    assert "SEKRIT" not in response.text, "the 422 handed the operator's token back"
    detail = response.json()["detail"]
    locs = [entry["loc"] for entry in detail]
    assert ["body", "document"] in locs, response.text
    assert ["body", "plex"] in locs, response.text
    assert all(entry["msg"] for entry in detail)
    assert all("input" not in entry for entry in detail)
    assert all("url" not in entry for entry in detail)


async def test_a_string_document_is_refused_without_echoing_the_paste(
    client, auth_headers
):
    """The second repro: `document` sent as pasted YAML rather than an object.
    One entry, whose `input` was the whole paste."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers,
        json={"document": "plex:\n  url: http://plex.lan:32400/?X-Plex-Token=SEKRIT\n"},
    )

    assert response.status_code == 422
    assert "SEKRIT" not in response.text
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["body", "document"]
    assert "valid dictionary" in detail[0]["msg"]
    assert "input" not in detail[0]


async def test_emptying_a_non_empty_store_needs_confirm(
    client, auth_headers, session, app
):
    """`{}` is a legal document and the documented revert-everything. What was
    missing was any way to tell a deliberate clear-all apart from a request
    that *degenerated* into one."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "path": "document",
            "message": (
                "this would clear all 12 stored overrides; send confirm: true "
                "to do it deliberately"
            ),
        }
    ]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT
    assert app.state.config.workers == 9, "a refused write must not swap"


async def test_emptying_a_non_empty_store_is_allowed_with_confirm(
    client, auth_headers, session, app
):
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )

    assert response.status_code == 200
    assert (await session.execute(select(ConfigOverride))).scalar_one().document == {}
    assert app.state.config.workers == 5, "the example config's value did not come back"


async def test_confirm_alone_cannot_stand_in_for_a_document(
    client, auth_headers, session
):
    """The last silent-empty hole. ``document`` defaulting to ``{}`` meant a
    body carrying only ``confirm`` bound ``document={}`` *and* the confirm
    suppressed ``_drop_refusal`` -- a 200 that wiped the store exactly like
    the incident, just spelled with one field instead of zero. ``document``
    is required now, so this body is refused before either guard runs."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"confirm": True}
    )

    assert response.status_code == 422, (
        "confirm alone was accepted. It binds document={} and, unlike a bare "
        "{}, skips the drop refusal outright -- a 200 that wipes the store"
    )
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT, "the refused request still wrote"


async def test_an_empty_document_over_an_empty_store_stays_a_legal_no_op(
    client, auth_headers
):
    """What a fresh deployment's first save looks like. There is nothing to
    destroy, so there is nothing to confirm."""
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {}}
    )
    assert response.status_code == 200


async def test_a_write_that_drops_more_than_the_cap_is_refused(
    client, auth_headers, session
):
    """The drop cap. A normal edit drops 0 or 1 path; the incident dropped 17.

    `document_paths` counts the leaves the stored document actually sets, and
    the refusal names those same paths, so the operator and the refusal count
    the same things.
    """
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )

    assert response.status_code == 422
    [detail] = response.json()["detail"]
    assert detail["path"] == "document"
    assert detail["message"].startswith("this would drop 11 stored overrides")
    # Named, not just counted: an operator cannot judge a refusal they cannot see.
    assert "collections.separator_style" in detail["message"]
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT


async def test_a_drop_over_the_cap_is_allowed_with_confirm(
    client, auth_headers, session
):
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {"workers": 9}, "confirm": True},
    )

    assert response.status_code == 200
    assert (await session.execute(select(ConfigOverride))).scalar_one().document == {
        "workers": 9
    }


async def test_a_normal_one_field_edit_never_trips_the_drop_cap(
    client, auth_headers, session
):
    """The blast-radius negative, and the one that matters most: the guards are
    worthless if the ordinary save has to learn about them."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await _put_document(client, auth_headers, edited)

    # And clearing exactly one override -- the drop cap's other boundary.
    reduced = deepcopy(edited)
    del reduced["scheduler"]
    await _put_document(client, auth_headers, reduced)
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert "scheduler" not in stored


async def test_dropping_exactly_the_cap_is_allowed_and_one_more_is_not(
    client, auth_headers
):
    """The boundary, both sides of it. OVERRIDE_DROP_CAP is 3: three dropped
    paths pass, four do not."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    three_fewer = deepcopy(THE_INCIDENT_DOCUMENT)
    del three_fewer["badges"]              # 2 paths
    del three_fewer["scheduler"]           # 1 path
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": three_fewer}
    )
    assert response.status_code == 200, response.text

    four_fewer = deepcopy(three_fewer)
    del four_fewer["notifications"]        # 1 path
    del four_fewer["plex"]                 # 1 path
    del four_fewer["artwork"]              # 2 paths  -> 4 dropped, one over the cap
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": four_fewer}
    )
    assert response.status_code == 422


async def test_the_preview_is_never_refused_for_being_destructive(
    client, auth_headers
):
    """`_validated_generation` promises every failure mode is reached before
    the first write, and the preview's whole job is answering "what would this
    do". A preview that refused to describe a destructive edit would be
    refusing the one question worth asking about it."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    for document in ({}, {"workers": 9}):
        response = await client.post(
            "/api/config/preview", headers=auth_headers, json={"document": document}
        )
        assert response.status_code == 200, (document, response.text)


async def test_the_preview_ignores_confirm_rather_than_rejecting_it(
    client, auth_headers
):
    """One body shape across all three arms -- the property Settings.tsx's
    `submit()` docstring says the three actions must never drift on."""
    response = await client.post(
        "/api/config/preview",
        headers=auth_headers,
        json={"document": {"workers": 9}, "confirm": True},
    )
    assert response.status_code == 200


async def test_the_audit_row_records_the_path_counts(
    client, auth_headers, session
):
    """The incident would have been visible in the audit log as `12 -> 0`
    rather than requiring an investigation. Counts, not settings: the payload's
    "versions, never the document" rule is about what an operator changed, and
    how many is not that."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {}, "confirm": True},
    )

    rows = (
        (
            await session.execute(
                select(EventLog)
                .where(EventLog.event_type == "overrides_updated")
                .order_by(EventLog.id)
            )
        )
        .scalars()
        .all()
    )
    assert [row.payload["paths_before"] for row in rows] == [0, 12]
    assert [row.payload["paths_after"] for row in rows] == [12, 0]
    assert [row.payload["reason"] for row in rows] == ["save", "save"]
    assert "document" not in rows[-1].payload


async def test_the_apply_arm_records_reason_apply_on_the_same_audit_row(
    client, auth_headers, session
):
    """`POST /api/config/apply` funnels through the same `_persist_and_swap`,
    passing `reason="apply"` -- the only thing distinguishing an apply's audit
    row from a plain save's."""
    response = await client.post(
        "/api/config/apply", headers=auth_headers, json={"document": {"workers": 9}}
    )
    assert response.status_code == 200, response.text

    row = (
        await session.execute(
            select(EventLog).where(EventLog.event_type == "overrides_updated")
        )
    ).scalar_one()
    assert row.payload["reason"] == "apply"


# --- Config safety: the revision token and the loud 409 ------------------
#
# Defect D2: four pages each seed the WHOLE
# document at mount and PUT the whole result. The seed is refreshed only by
# that page's own save, so a page holding a mount-time seed writes its stale
# document over everything a different page added since -- with a 200. That is
# the operator's own observation: a Settings save of
# `collections.separator_style: sand` did not stick, and the identical second
# save did, because nothing stale followed it.


async def _revision(client, auth_headers) -> str:
    """What a page seeding from GET /api/config carries away with the seed."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    return body["overrides_revision"]


async def test_the_two_page_stale_save_is_refused_instead_of_clobbering(
    client, auth_headers, session
):
    """Page A mounts. Page B saves separator_style. Page A saves again.

    Before the fix, page A's second save answered 200 and separator_style was
    gone -- silently, with nothing in the response to show the operator what
    had just been undone. It must now be a 409 that says what happened.
    """
    # The store as both pages find it: the incident document WITHOUT the
    # separator style, because that is the setting the operator was about to
    # save. Seeding `sand` and then having page B "save" `sand` again would be
    # a no-op write -- no content moves, so there would be nothing to clobber
    # and nothing to refuse.
    seed = deepcopy(THE_INCIDENT_DOCUMENT)
    del seed["collections"]["separator_style"]
    await _put_document(client, auth_headers, seed)

    # Page A mounts and seeds. It is now holding the document as of now.
    page_a_seed = deepcopy(seed)
    page_a_revision = await _revision(client, auth_headers)

    # Page B, mounted from the same state, saves one field.
    page_b = deepcopy(seed)
    page_b["collections"]["separator_style"] = "sand"
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": page_b, "expected_revision": page_a_revision},
    )
    assert response.status_code == 200, response.text

    # Page A saves anything at all, against the seed it took when it mounted.
    # It drops exactly one path, which is inside OVERRIDE_DROP_CAP -- so the
    # destructiveness guard does not fire here and cannot be what refuses it.
    page_a_seed["workers"] = 11
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": page_a_seed, "expected_revision": page_a_revision},
    )

    assert response.status_code == 409, (
        "a stale whole-document write was accepted. It silently deletes every "
        "override added since the writer mounted -- this is the incident's UI half"
    )
    detail = response.json()["detail"]
    assert detail["current_revision"] == await _revision(client, auth_headers)
    assert "collections.separator_style" in detail["changed_paths"]

    # And the field is still there, which is the whole point.
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored["collections"]["separator_style"] == "sand"


async def test_a_save_against_a_fresh_seed_is_untouched_by_the_revision_check(
    client, auth_headers
):
    """"UI saves generally working": neither defect fires when one page saves
    against a seed nothing has moved under."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    revision = await _revision(client, auth_headers)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": revision},
    )
    assert response.status_code == 200, response.text


async def test_a_client_that_sends_no_revision_is_not_broken_by_the_upgrade(
    client, auth_headers
):
    """A scripted client predates the token. Absent means "proceed" -- the
    frontend is held to sending it by its own tests, not by this endpoint."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": edited}
    )
    assert response.status_code == 200, response.text


async def test_the_revision_is_a_content_hash_not_a_timestamp(client, auth_headers):
    """Two writers that independently produced the same document are not in
    conflict, and a no-op rewrite does not invalidate anybody's seed.

    `updated_at` is the obvious candidate and is wrong twice over: it moves on
    a no-op rewrite, and this project has a recorded environment whose
    container clock steps *backwards*, which would make a timestamp token go
    backwards.
    """
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    first = await _revision(client, auth_headers)

    # The same document written again -- a real write, a new updated_at.
    await _put_document(client, auth_headers, deepcopy(THE_INCIDENT_DOCUMENT))
    assert await _revision(client, auth_headers) == first

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": first},
    )
    assert await _revision(client, auth_headers) != first


async def test_the_empty_store_serves_the_empty_document_revision(client, auth_headers):
    """A page that mounts against a fresh deployment gets a real token, not a
    null the four pages would each have to special-case."""
    body = (await client.get("/api/config", headers=auth_headers)).json()
    assert body["overrides_revision"] == EMPTY_DOCUMENT_REVISION


async def test_the_save_response_carries_the_revision_it_just_wrote(
    client, auth_headers
):
    """So a page can re-seed from its own write even if the follow-up GET
    fails -- and so the two never disagree about what was stored."""
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": THE_INCIDENT_DOCUMENT},
    )
    assert response.json()["overrides_revision"] == await _revision(
        client, auth_headers
    )


async def test_the_apply_arm_checks_the_revision_too(client, auth_headers):
    """Both write arms funnel through _persist_and_swap, so one insertion
    covers both -- and a test says so, because "both" is the claim."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    stale = EMPTY_DOCUMENT_REVISION

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/apply",
        headers=auth_headers,
        json={"document": edited, "expected_revision": stale},
    )
    assert response.status_code == 409


async def test_the_preview_accepts_the_revision_and_ignores_it(client, auth_headers):
    """All three arms take one body shape. A preview that 409'd would be
    refusing to answer "what would this do" for the one case where the
    operator most needs to know."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    response = await client.post(
        "/api/config/preview",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )
    assert response.status_code == 200, response.text


async def test_a_stale_save_writes_nothing_at_all(client, auth_headers, session, app):
    """Never half-apply, on the 409 path too: no row change, no swap, no audit
    event for the refused write."""
    await _put_document(client, auth_headers, THE_INCIDENT_DOCUMENT)
    events_before = len(
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "overrides_updated")
            )
        )
        .scalars()
        .all()
    )

    edited = deepcopy(THE_INCIDENT_DOCUMENT)
    edited["workers"] = 11
    await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": edited, "expected_revision": EMPTY_DOCUMENT_REVISION},
    )

    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == THE_INCIDENT_DOCUMENT
    assert app.state.config.workers == 9
    events_after = len(
        (
            await session.execute(
                select(EventLog).where(EventLog.event_type == "overrides_updated")
            )
        )
        .scalars()
        .all()
    )
    assert events_after == events_before


async def _blocked_on_the_insert_lock(probe) -> bool:
    """Wait until somebody is *waiting* for the first-save advisory lock.

    A condition, not a duration: it polls ``pg_locks`` -- which is shared
    memory rather than an MVCC relation, so a waiter is visible the instant it
    exists -- and returns as soon as one appears. The bound exists only so a
    version that never takes the lock fails in five seconds with a sentence
    instead of hanging the suite forever.

    Scoped to this connection's own database, because the whole cluster's
    advisory locks are in one view and xdist gives each worker its own
    database.
    """
    waiting = text(
        "SELECT count(*) FROM pg_locks "
        " WHERE locktype = 'advisory' AND NOT granted "
        "   AND database = (SELECT oid FROM pg_database "
        "                    WHERE datname = current_database())"
    )
    for _ in range(500):
        await probe.rollback()
        if await probe.scalar(waiting):
            return True
        await asyncio.sleep(0.01)
    return False


async def test_two_simultaneous_first_ever_saves_cannot_both_win(
    client, auth_headers, session, session_factory
):
    """The fresh-deploy race: with no row, FOR UPDATE has nothing to lock.

    Every other case in this file is covered by the row lock. This one is not,
    and it is the case with the *least* bounded loss: `_drop_refusal` returns
    None the moment the stored document is empty ("nothing to destroy"), so
    both racing writers sail past the drop cap too. The loser's entire
    first-ever save is replaced wholesale, after it has already been told 200.

    Writer A is a stand-in rather than a second HTTP request, and deliberately
    so: what has to be held still is the *middle* of A's transaction -- lock
    taken, row inserted, not yet committed -- and an in-flight request cannot
    be paused there without monkeypatching the thing under test. It takes the
    same advisory lock on the same key and inserts the same row, which is
    exactly the state a real `_persist_and_swap` is in at that moment.
    """
    a_document = {"workers": 3, "badges": {"enabled": True}}
    b_document = {"collections": {"separator_style": "sand"}}

    async with session_factory() as writer_a:
        # A is mid-save: it holds the insert lock and its row is written but
        # invisible to everybody else.
        await writer_a.execute(select(func.pg_advisory_xact_lock(OVERRIDES_INSERT_LOCK_KEY)))
        await writer_a.execute(
            insert(ConfigOverride).values(id=1, document=a_document)
        )

        # B is a genuine first-ever save through the real endpoint, carrying
        # the token a page that mounted against the empty store would hold.
        b = asyncio.create_task(
            client.put(
                "/api/config/overrides",
                headers=auth_headers,
                json={
                    "document": b_document,
                    "expected_revision": EMPTY_DOCUMENT_REVISION,
                },
            )
        )
        blocked = await _blocked_on_the_insert_lock(session)

        # Whatever B is waiting on, releasing A frees it.
        await writer_a.commit()
        response = await b

    assert blocked, (
        "B never waited for the first-save lock. With no row to lock, "
        "SELECT ... FOR UPDATE locked nothing, so B read stored == {}, found "
        "its EMPTY_DOCUMENT_REVISION current, and walked straight into the "
        "upsert -- the fresh-deploy lost update"
    )
    assert response.status_code == 409, response.text

    # A's save is what stands, whole. B was told nothing was saved, which is
    # the truth.
    await session.rollback()
    stored = (await session.execute(select(ConfigOverride))).scalar_one().document
    assert stored == a_document


async def test_an_unknown_field_on_the_body_is_still_refused(client, auth_headers):
    """The envelope's forbid-extras survives the two new fields -- a typo'd
    `expected_version` must not be silently ignored, which would put the
    sender straight back in the incident."""
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {"workers": 9}, "expected_version": "whatever"},
    )
    assert response.status_code == 422


async def test_the_deployment_url_is_served_as_an_editable_leaf(client, auth_headers):
    """A new top-level scalar has to reach the Settings page as a described,
    non-frozen row -- which is the whole reason it is a config key rather than
    an environment variable."""
    response = await client.get("/api/config", headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "public_url" in body
    assert body["field_descriptions"]["public_url"].strip() != ""
    assert "public_url" not in body["frozen_paths"]


# --- the row's metadata ---


async def test_the_stored_format_says_how_the_document_was_validated(
    client, auth_headers, session
):
    """A save against a store with no row is validated by merging over the
    mounted file -- there is nothing else it could be a statement about -- so
    the row it inserts is labelled a delta, and the next boot merges it over
    that same file and lands on the same configuration.

    Labelling it a whole document instead would be the disagreement this pair
    exists to prevent: the save would run one configuration and the restart
    after it would try to build another out of a fragment, failing on the
    first required setting the fragment does not carry.

    An empty store is not reachable from a booted deployment -- boot seeds the
    store from the file or serves the setup wizard -- so this is the shape of
    the rule rather than a state an operator can be in.
    """
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 9}}
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.meta.get("format", 1) < STORE_FORMAT


async def test_a_first_save_with_no_mounted_file_stores_a_whole_document(
    client, auth_headers, session, app, file_document, tmp_path
):
    """The deployment this store exists to make possible: no mounted file.

    An empty store with nothing underneath it has no document for a save to be
    a statement ABOUT, so what arrives can only be the configuration itself. It
    is validated as one and the row is labelled as one, which is what makes the
    next boot able to load it -- a row labelled a delta would send that boot
    looking for the file this deployment does not have, and it would refuse to
    start.
    """
    app.state.config_path = tmp_path / "there-is-no-config-here.yaml"

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": file_document}
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.meta["format"] == STORE_FORMAT

    rebuilt = await load_effective_config(app.state.config_path, session)
    assert rebuilt.model_dump(mode="json") == app.state.config.model_dump(mode="json")


async def test_a_delta_store_with_no_mounted_file_is_refused_not_crashed(
    client, auth_headers, session, app, tmp_path
):
    """A delta is a statement about a file. Without that file it cannot be
    merged and it cannot be promoted either -- promoting it would silently
    default every key the file used to carry. The save says so in the same
    words the boot loader uses, rather than surfacing a read error as a 500.
    """
    app.state.config_path = tmp_path / "there-is-no-config-here.yaml"
    await session.execute(
        insert(ConfigOverride).values(id=1, document={"workers": 9}, meta={"format": 1})
    )
    await session.commit()

    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={"document": {"workers": 8}},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["message"] == DELTA_WITHOUT_FILE


async def test_a_save_keeps_the_metadata_it_did_not_write(
    client, auth_headers, session, file_document
):
    """The restart list lives in the same column as the format, and the format
    is not a save's to change.

    The list is: every save rewrites it with the frozen paths that still
    differ from what this process booted on, so the seeded ``plex`` -- a path
    the running configuration matches -- does not survive a save, and the
    ``workers`` this one changes takes its place."""
    await session.execute(
        insert(ConfigOverride).values(
            id=1,
            document=_whole(file_document, {"workers": 9}),
            meta={"format": STORE_FORMAT, "restart_paths": ["plex"]},
        )
    )
    await session.commit()

    saved = _whole(file_document, {"workers": 8})
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": saved}
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.document == saved
    assert row.meta == {"format": STORE_FORMAT, "restart_paths": ["workers"]}

    snapshot = (
        await session.execute(
            select(ConfigOverrideSnapshot).order_by(ConfigOverrideSnapshot.id.desc())
        )
    ).scalars().first()
    assert snapshot.format == STORE_FORMAT, "the save was on a whole document"


async def test_a_delta_save_is_refused_when_the_row_becomes_a_document_under_it(
    client, auth_headers, session, session_factory, file_document, monkeypatch
):
    """The arm is chosen from an unlocked read; the format can move after it.

    Another process's one-time conversion, or a CLI holding its own session,
    can turn the row into a whole document between the read that says "this is
    a delta" and the locked read that writes. What is in hand by then is a
    fragment -- validated by merging it over the mounted file -- and the row
    says its contents are the whole configuration. Writing it through would
    stamp the fragment as whole, and the next boot would hand it to
    `build_config` alone and die on the first required setting it does not
    carry, with the editor that could repair the row behind an application that
    will not start.

    The mirror of the case the format stamp already closes: that one stops a
    save RAISING the format of a delta; this one stops the format being raised
    UNDER the save.
    """
    from autoposter.api import routes

    await session.execute(
        insert(ConfigOverride).values(id=1, document={"workers": 9}, meta={"format": 1})
    )
    await session.commit()

    converted = _whole(file_document, {"workers": 7})
    converted_meta = {"format": STORE_FORMAT}
    validate = routes._validated_generation

    async def convert_between_the_two_reads(*args, **kwargs):
        result = await validate(*args, **kwargs)
        async with session_factory() as other:
            statement = insert(ConfigOverride).values(
                id=1, document=converted, meta=converted_meta
            )
            await other.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={"document": converted, "meta": converted_meta},
                )
            )
            await other.commit()
        return result

    monkeypatch.setattr(routes, "_validated_generation", convert_between_the_two_reads)

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 8}}
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["message"] == STORE_CONVERTED_REFUSAL

    # Nothing written: the converted row stands exactly as the other writer
    # left it, and no snapshot was taken of a write that did not happen.
    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.document == converted
    assert row.meta == converted_meta
    snapshots = (
        await session.execute(select(func.count()).select_from(ConfigOverrideSnapshot))
    ).scalar_one()
    assert snapshots == 0


@pytest.mark.parametrize(
    ("stale_document", "stale_meta"),
    [
        ({"workers": 9}, {}),
        ({"workers": 9}, {"format": 1}),
        ({"version_check": {"project": "operinko-labs"}}, {}),
    ],
    ids=["delta", "delta-with-an-explicit-format", "only-sections-that-left-the-schema"],
)
async def test_a_save_does_not_raise_the_format_of_a_delta(
    client, auth_headers, session, stale_document, stale_meta
):
    """A deployment that has not been converted yet still stores a delta, and
    the editor still composes one -- from the very paths that delta made
    overridden. Stamping this document as whole would be a lie the next boot
    pays for: it would build a configuration out of a fragment and die on the
    first required setting the fragment does not carry, with the editor that
    could repair the row sitting behind the application that will not start.

    The third row shape is the one that cannot be told apart by what it says:
    every section in it has left the schema, so it reads as an empty document
    and looks exactly like a store that was never written. Whether a row
    exists is the only question that separates them."""
    await session.execute(
        insert(ConfigOverride).values(id=1, document=stale_document, meta=stale_meta)
    )
    await session.commit()

    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": {"workers": 8}}
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    # The format alone: the save also puts the frozen path it changed on the
    # row's restart list, which is the rest of this column's business.
    assert row.meta.get("format") == stale_meta.get("format"), (
        "the save relabelled a delta as a whole document"
    )

    snapshot = (
        await session.execute(
            select(ConfigOverrideSnapshot).order_by(ConfigOverrideSnapshot.id.desc())
        )
    ).scalars().first()
    if snapshot is not None:
        # The third row shape's outgoing document strips to {} -- nothing to
        # snapshot, and capture_snapshot skips an empty document -- so only
        # the first two shapes reach this assertion.
        assert snapshot.format == 1, "the snapshot of the outgoing delta must not be raised"

    # The proof that matters: the next boot still starts, because the delta is
    # still merged over the mounted file.
    config = await load_effective_config(EXAMPLE, session)
    assert config.workers == 8


async def test_restoring_a_delta_era_snapshot_re_runs_the_merge(
    client, auth_headers, app, session_factory, config_file
):
    """spec §8: every existing snapshot stays restorable.

    A format-1 snapshot is a DELTA. Restoring it as though it were a document
    would store `{"workers": 9}` as the whole configuration and fail
    validation on eight required fields; the restore merges it over the file
    instead, exactly as the delta era did.

    The store is seeded first, because that is the only state such a snapshot
    can be restored from: a delta-era store is converted to a whole document by
    the first boot that reads it, and the format-1 snapshot this restores is
    what that conversion left behind.
    """
    async with session_factory() as session:
        await seed_store(session, read_config_document(config_file))
        session.add(
            ConfigOverrideSnapshot(
                document={"workers": 9}, path_count=1, reason="migrate", format=1
            )
        )
        await session.commit()
        snapshot_id = (
            await session.execute(select(func.max(ConfigOverrideSnapshot.id)))
        ).scalar_one()

    response = await client.post(
        f"/api/config/snapshots/{snapshot_id}/restore", json={}, headers=auth_headers
    )
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        document, meta = await load_store(session)
    assert document["workers"] == 9
    assert meta["format"] == 2
    assert "plex" in document, "the restore must produce a whole document"


async def test_a_snapshot_reports_which_format_it_is(
    client, auth_headers, session_factory
):
    async with session_factory() as session:
        session.add(
            ConfigOverrideSnapshot(
                document={"workers": 9}, path_count=1, reason="migrate", format=1
            )
        )
        await session.commit()

    listing = await client.get("/api/config/snapshots", headers=auth_headers)
    assert listing.json()[0]["format"] == 1


async def test_restoring_a_delta_keeps_an_empty_object_the_file_spells_out(
    client, auth_headers, app, session_factory, config_file
):
    """The mounted file is free to spell an unset section out as `{}`
    (``artwork.poster.text.newline_words: {}`` in the example config) -- the
    same value leaving the key out entirely validates to. The delta-restore
    merge carries that spelling in from the file verbatim, and it must not be
    dropped to satisfy the editor's `{}`-leaf guard: that guard exists for a
    typed-by-hand mistake, not for the file's own way of saying "nothing
    here", and dropping it would silently change what got restored.
    """
    async with session_factory() as session:
        session.add(
            ConfigOverrideSnapshot(
                document={"workers": 9}, path_count=1, reason="migrate", format=1
            )
        )
        await session.commit()
        snapshot_id = (
            await session.execute(select(func.max(ConfigOverrideSnapshot.id)))
        ).scalar_one()

    response = await client.post(
        f"/api/config/snapshots/{snapshot_id}/restore", json={}, headers=auth_headers
    )
    assert response.status_code == 200, response.text

    async with session_factory() as session:
        document, _meta = await load_store(session)
    assert document["artwork"]["poster"]["text"]["newline_words"] == {}


async def test_a_save_on_a_delta_store_with_an_empty_object_leaf_is_still_refused(
    client, auth_headers
):
    """The `{}`-leaf guard stays on for editor input ON A DELTA STORE -- the
    only arm it has ever run on, because a delta is where `{}` would be
    reported as an override of the whole section. A save that hand-carries a
    `{}` leaf there is refused exactly as it always was."""
    response = await client.put(
        "/api/config/overrides",
        headers=auth_headers,
        json={
            "document": {
                "workers": 8,
                "artwork": {"poster": {"text": {"newline_words": {}}}},
            }
        },
    )
    assert response.status_code == 422, response.text
    assert any(
        item["path"] == "artwork.poster.text.newline_words"
        for item in response.json()["detail"]
    )


async def test_a_save_on_a_whole_document_store_keeps_an_empty_object_leaf(
    client, auth_headers, session, file_document
):
    """The other arm, which is every seeded deployment, and where the guard is
    deliberately off.

    In a whole document `{}` is the value the model holds rather than a
    section a path walk would misreport -- the example file spells
    `newline_words` exactly that way, so the seed puts it in the store and the
    page sends it straight back. Refusing it here would refuse every save on
    every seeded deployment, and dropping it would change what an explicitly
    empty mapping means.
    """
    assert file_document["artwork"]["poster"]["text"]["newline_words"] == {}, (
        "the fixture no longer carries the leaf this test is about"
    )
    await session.execute(
        insert(ConfigOverride).values(
            id=1, document=file_document, meta={"format": STORE_FORMAT}
        )
    )
    await session.commit()

    saved = _whole(file_document, {"workers": 8})
    response = await client.put(
        "/api/config/overrides", headers=auth_headers, json={"document": saved}
    )
    assert response.status_code == 200, response.text

    session.expire_all()
    row = (await session.execute(select(ConfigOverride))).scalar_one()
    assert row.document["artwork"]["poster"]["text"]["newline_words"] == {}


async def test_restoring_a_delta_snapshot_with_a_secrets_key_is_refused(
    client, auth_headers, session_factory
):
    """A corrupted snapshot row carrying a `secrets` key must not resurrect a
    token into the store on restore -- the delta-merge path refuses it the
    same way an ordinary save's merge does."""
    async with session_factory() as session:
        session.add(
            ConfigOverrideSnapshot(
                document={"secrets": {"plex_token": "leaked"}},
                path_count=1,
                reason="migrate",
                format=1,
            )
        )
        await session.commit()
        snapshot_id = (
            await session.execute(select(func.max(ConfigOverrideSnapshot.id)))
        ).scalar_one()

    response = await client.post(
        f"/api/config/snapshots/{snapshot_id}/restore", json={}, headers=auth_headers
    )
    assert response.status_code == 422, response.text
