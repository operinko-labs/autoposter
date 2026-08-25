"""The preset catalog, the ``presets:`` config field, and the expansion seam.

Three properties carry this phase, and every test here is one of them:

- **The expansion is pure.** ``_titles_must_not_collide`` runs
  ``default_definitions`` for every library type on every config write, so a
  fetch inside the preset expansion would put a network round trip inside
  config validation. Pinned structurally (the module's own import list) and at
  run time (the suite's ``no_outbound_network`` guard turns any real request
  into a failure, so an expansion that reached the dataset could not pass).
- **Empty presets change nothing.** The third term of ``default_definitions``
  contributes an empty list, in that order, and the golden gate
  (``tests/test_builder_port_golden.py``) is the byte-level half of the same
  claim.
- **Preset titles reach the collision validator.** They flow through
  ``default_definitions``, which is what the validator enumerates -- so an
  operator definition colliding with an ACTIVE preset's title is refused with
  no new code in the validator, and one colliding with an INACTIVE preset's
  title is not.
"""
import ast
import pathlib
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.collections import catalog
from autoposter.collections.builders.imdb_award import EVENTS
from autoposter.collections.catalog import (
    CATALOG,
    CATEGORIES,
    GATED,
    READY,
    Preset,
    award_years_title,
    catalog_listing,
    preset_definitions,
)
from autoposter.collections.sources import (
    AWARD_YEARS_TITLE,
    chart_and_award_definitions,
    default_definitions,
)
from autoposter.config.loader import build_config, load_config, read_config_document
from autoposter.config.schema import CollectionDefinition, Secrets

EXAMPLE = pathlib.Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

LIBRARY_TYPES = ("Movie", "Show")


def _config(presets: list[str], **collections):
    """The example config with ``presets`` (and any toggle) set.

    ``model_copy`` rather than the loader, deliberately: these tests are about
    what the expansion produces, not about what the config accepts, and the
    refusals below use the real loader for the other half.
    """
    config = load_config(EXAMPLE)
    section = config.collections.model_copy(
        update={"presets": presets, **collections}
    )
    return SimpleNamespace(collections=section)


def _document(presets, definitions=None) -> dict:
    """The example config document with ``collections.presets`` replaced.

    Goes through the real loader, so these exercise the same path a config file
    and the overrides API both end in.
    """
    document = read_config_document(EXAMPLE)
    document["collections"]["presets"] = presets
    if definitions is not None:
        document["collections"]["definitions"] = definitions
    return document


# --- the table --------------------------------------------------------------


def test_the_awards_category_is_every_ceremony_but_the_oscars():
    """Count checksum, and the derivation that makes it one.

    The rows are built from ``EVENTS``; nothing here restates a ceremony. A
    seventeenth event added to that registry becomes a preset on its own -- and
    fails at import if nobody wrote it a catalog note -- so this equality is
    the assertion that the catalog cannot fall behind the builders.
    """
    awards = [preset for preset in CATALOG if preset.category == "awards"]
    presets = [preset for preset in awards if preset.setting is None]

    assert len(presets) == 15
    assert {preset.key for preset in presets} == {
        "award_%s" % key for key in EVENTS if key != "oscars"
    }
    # The Oscars are ``collections.awards``: not a preset key an operator can
    # list -- a preset for them would be a second switch building the same
    # four collections under the same titles -- but they still get a row, a
    # setting-backed one, so the Awards tab has something to show for them.
    assert "award_oscars" not in {preset.key for preset in presets}
    oscars_row = next(preset for preset in awards if preset.key == "oscars")
    assert oscars_row.setting == "collections.awards"
    assert oscars_row.award_event == "oscars"


def test_the_catalog_table_checksum():
    """15 award presets + 3 setting-backed rows (Oscars, the IMDb charts
    family, the Common Sense divider) = 18. See ``catalog.py``'s own
    count-checksum comment above ``CATALOG``."""
    presets = [preset for preset in CATALOG if preset.setting is None]
    settings = [preset for preset in CATALOG if preset.setting is not None]

    assert len(CATALOG) == 18
    assert len(presets) == 15
    assert len(settings) == 3
    assert {preset.category for preset in settings} == {
        "awards", "charts", "content_ratings",
    }


def test_every_event_has_a_kometa_file_entry():
    """``_KOMETA_FILES`` is strict, the same shape ``_AWARD_NOTES`` already
    had -- every non-Oscars ceremony names its Kometa defaults file here, so a
    ceremony added to ``EVENTS`` without one fails at import rather than
    ``.get(key, key)`` inventing a file name nobody checked."""
    for key in EVENTS:
        if key == "oscars":
            continue
        assert key in catalog._KOMETA_FILES, key


def test_a_kometa_file_missing_for_a_real_event_raises(monkeypatch):
    """The strictness proven directly: deleting one real ceremony's entry
    makes building its preset raise, rather than falling back to a guessed
    file name."""
    monkeypatch.delitem(catalog._KOMETA_FILES, "cannes")

    with pytest.raises(KeyError):
        catalog._award_preset("cannes")


def test_award_narrowing_keys_must_be_real_awards_of_their_event(monkeypatch):
    """Import-time guard (``catalog._check_award_narrowing``), proven
    directly: a narrowing row naming an award its ceremony does not have
    raises rather than silently narrowing nothing."""
    monkeypatch.setitem(
        catalog._AWARD_NARROWING, "cannes", (("not_a_real_award", ("Movie",)),)
    )

    with pytest.raises(AssertionError, match="not_a_real_award"):
        catalog._check_award_narrowing()


def test_every_preset_row_is_internally_consistent():
    keys = [preset.key for preset in CATALOG]
    assert len(keys) == len(set(keys)), "duplicate preset key"
    for preset in CATALOG:
        assert preset.category in CATEGORIES, preset.key
        assert preset.readiness in (READY, GATED), preset.key
        # "not yet" without a row number is a shrug; a READY row citing one
        # would be a row waiting on work it does not need.
        assert (preset.gated_row is not None) == (preset.readiness == GATED), preset.key
        assert preset.description, preset.key
        assert preset.kometa_source, preset.key
        assert set(preset.library_types) <= set(LIBRARY_TYPES), preset.key


def test_an_award_preset_derives_its_facts_from_the_event_registry():
    """Titles, library types and the years-title shape are the builders' own.

    If any of them were transcribed into the catalog instead, this is the test
    that a ceremony corrected in ``EVENTS`` -- a renamed collection, a widened
    library type -- would leave failing.
    """
    for preset in CATALOG:
        if preset.award_event is None:
            continue
        event = EVENTS[preset.award_event]
        assert preset.name == event.name
        assert preset.titles() == [award.title for award in event.awards.values()]
        assert preset.library_types == event.library_types
        assert preset.years_title() == event.year_title % "<year>"


def test_the_years_placeholder_derivation_matches_the_shipped_oscars_one():
    """``sources.AWARD_YEARS_TITLE`` predates this module and is the placeholder
    the golden fixture recorded. The derivation every other ceremony's
    placeholder comes from has to produce exactly it for the Oscars, or the
    fifteen presets are naming their year definitions in a shape the shipped
    one does not use."""
    assert award_years_title(EVENTS["oscars"]) == AWARD_YEARS_TITLE


def test_every_ready_preset_actually_builds_something():
    """A READY preset that expands to nothing on every library type is a
    checkbox that does nothing -- the failure mode a table of rows with no
    producer behind them has. Task 5's categories are held to this the moment
    their rows land.

    Setting-backed rows are the deliberate exception: they are switches for a
    family that already builds through its own boolean, not presets with a
    producer of their own -- see the setting-backed-rows tests below for what
    they ARE held to."""
    for preset in CATALOG:
        if preset.readiness != READY or preset.setting is not None:
            continue
        produced = [
            definition
            for library_type in LIBRARY_TYPES
            for definition in preset.definitions(library_type)
        ]
        assert produced, preset.key


def test_every_preset_definition_is_a_definition_the_config_would_accept():
    """Expanding is validating: ``CollectionDefinition`` checks the builder
    against the registry and the params against the builder's own model as it
    is constructed, so a preset naming a builder that does not exist, or
    passing a param it does not take, cannot even be expanded. Round-tripped
    here so the claim is a test rather than a side effect."""
    for preset in CATALOG:
        for library_type in LIBRARY_TYPES:
            for definition in preset.definitions(library_type):
                assert CollectionDefinition.model_validate(
                    definition.model_dump()
                ) == definition


# --- the expansion ----------------------------------------------------------


def test_an_award_preset_expands_to_its_statics_and_its_years_placeholder():
    definitions = preset_definitions(_config(["award_cannes"]), "Movie")

    assert [(d.title, d.builder, d.params) for d in definitions] == [
        ("Cannes Golden Palm Winners", "imdb_award",
         {"event": "cannes", "award": "palm"}),
        ("Cannes (recent ceremonies)", "cannes_award_years", {}),
    ]


def test_a_movie_only_ceremony_expands_to_nothing_on_a_show_library():
    """The gate is the point of ``AwardEvent.library_types``: a Cannes
    definition on a Show library resolves nothing, which looks exactly like a
    festival that awarded nobody."""
    assert preset_definitions(_config(["award_cannes"]), "Show") == []


def test_a_television_ceremony_expands_on_show_and_not_on_movie():
    assert preset_definitions(_config(["award_emmy"]), "Movie") == []
    assert [d.title for d in preset_definitions(_config(["award_emmy"]), "Show")] == [
        "Emmys Best in Category Winners",
        "Emmys (recent ceremonies)",
    ]


def test_the_one_ceremony_whose_static_collection_is_narrower_than_its_years():
    """Critics Choice awards film and television, so its year collections
    belong in both libraries -- but "best picture" is a film award, and on a
    Show library that collection would resolve nothing. The narrowing lives on
    the preset (``award_library_types``), because the event-level types are
    right for the years and wrong for this one collection."""
    movie = preset_definitions(_config(["award_choice"]), "Movie")
    show = preset_definitions(_config(["award_choice"]), "Show")

    assert [d.title for d in movie] == [
        "Critics Choice Best Picture Winners",
        "Critics Choice Awards (recent ceremonies)",
    ]
    # The years survive on Show; the best-picture collection does not.
    assert [d.title for d in show] == ["Critics Choice Awards (recent ceremonies)"]
    # ...and the event itself still says both, which is what the year
    # collections need and what the narrowing deliberately does not change.
    assert EVENTS["choice"].library_types == ("Movie", "Show")


def test_two_presets_expand_in_catalog_order_whatever_order_they_were_listed():
    """Definition order is the order a pass runs them in. Re-ordering two lines
    of YAML is not a change to what a deployment builds, so the expansion reads
    the catalog's order rather than the operator's."""
    one = preset_definitions(_config(["award_cannes", "award_venice"]), "Movie")
    other = preset_definitions(_config(["award_venice", "award_cannes"]), "Movie")

    assert [d.title for d in one] == [d.title for d in other]
    assert [d.title for d in one] == [
        "Cannes Golden Palm Winners",
        "Cannes (recent ceremonies)",
        "Venice Golden Lions",
        "Venice (recent ceremonies)",
    ]


def test_no_presets_expand_to_nothing_at_all():
    for library_type in LIBRARY_TYPES:
        assert preset_definitions(_config([]), library_type) == []


# --- the seam: default_definitions ------------------------------------------


def test_empty_presets_leave_default_definitions_exactly_as_they_were():
    """The no-op that the whole phase rests on, over the toggle matrix rather
    than one config: whatever ``charts``/``awards``/``separators`` say, an
    empty ``presets`` produces the same list the two shipped terms produce on
    their own. ``tests/test_builder_port_golden.py`` is the byte-level half of
    this claim, recorded against the pre-port code."""
    for charts in (True, False):
        for awards in (True, False):
            config = _config([], charts=charts, awards=awards)
            for library_type in LIBRARY_TYPES:
                assert default_definitions(config, library_type) == [
                    CollectionDefinition(
                        title="Common Sense age ratings", builder="cs_bucket"
                    ),
                    *chart_and_award_definitions(config, library_type),
                ]


def test_the_preset_expansion_is_appended_after_the_two_shipped_terms():
    """Order is load-bearing (``sources.py``'s own comment): the Common Sense
    family, then the charts and awards, then -- and only then -- the presets.
    An expansion inserted anywhere else re-orders the pass for every deployment
    that switches a preset on."""
    config = _config(["award_cannes"])
    shipped = default_definitions(_config([]), "Movie")

    definitions = default_definitions(config, "Movie")

    assert definitions[: len(shipped)] == shipped
    assert definitions[len(shipped):] == preset_definitions(config, "Movie")
    assert len(definitions) > len(shipped), "the preset produced nothing to order"


def test_a_preset_reaches_default_definitions_at_all():
    titles = [d.title for d in default_definitions(_config(["award_venice"]), "Movie")]

    assert "Venice Golden Lions" in titles


# --- purity -----------------------------------------------------------------


def test_the_catalog_module_imports_nothing_that_could_reach_the_world():
    """The module docstring's central claim, pinned structurally the way
    ``test_collection_filters.py`` pins ``filters.py``'s: read the import list
    rather than trust the docstring to stay true.

    ``_titles_must_not_collide`` runs this expansion on every config write, so
    an import of httpx here is a network call inside config validation -- and
    its failure is a 500 on a settings save."""
    tree = ast.parse(
        pathlib.Path(catalog.__file__).read_text(encoding="utf-8")
    )
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert imported_roots.isdisjoint({
        "httpx", "requests", "urllib", "socket", "http",
        "plexapi", "pathlib", "os", "io", "open",
        "sqlalchemy", "asyncpg",
    }), sorted(imported_roots)


def test_expanding_every_preset_reaches_no_network(monkeypatch):
    """The run-time half. ``tests/conftest.py``'s autouse
    ``no_outbound_network`` fixture replaces httpx's real transport with one
    that raises, so an expansion that fetched the ceremony dataset would fail
    here rather than pass quietly -- this test needs no mocking of its own,
    only every preset switched on at once."""
    config = _config([preset.key for preset in CATALOG])

    for library_type in LIBRARY_TYPES:
        assert default_definitions(config, library_type)


def test_the_expansion_is_the_same_list_every_time_it_is_asked():
    """Purity's other half: no memoisation, no accumulation, no shared list
    handed back to two callers. The validator calls this on every write."""
    config = _config(["award_cannes", "award_emmy"])

    first = preset_definitions(config, "Movie")
    second = preset_definitions(config, "Movie")

    assert first == second
    assert first is not second


# --- the setting-backed rows -------------------------------------------------


def test_setting_backed_rows_are_not_in_by_key():
    """A setting-backed row is not something ``presets:`` can name -- typing
    it there must be refused as unknown, the same as any other typo, rather
    than accepted as a no-op nobody asked for."""
    setting_keys = {preset.key for preset in CATALOG if preset.setting is not None}

    assert setting_keys == {"oscars", "imdb_charts", "content_ratings_divider"}
    assert setting_keys.isdisjoint(catalog.BY_KEY)


def test_a_setting_backed_key_is_refused_at_load_as_unknown():
    with pytest.raises(ValidationError, match="unknown collection preset 'oscars'"):
        build_config(_document(["oscars"]))


def test_setting_backed_rows_never_expand_via_preset_definitions():
    """These rows are rendered switches, not presets: even if their key ends
    up in ``presets`` -- bypassing the real config validator, which refuses
    them as unknown -- ``preset_definitions`` must expand them to nothing.
    Their families already build through the booleans ``Preset.setting``
    names (``sources.chart_and_award_definitions``, ``cs_bucket``)."""
    setting_keys = [preset.key for preset in CATALOG if preset.setting is not None]
    assert setting_keys  # otherwise this proves nothing

    for library_type in LIBRARY_TYPES:
        assert preset_definitions(_config(setting_keys), library_type) == []


def test_a_gated_row_with_a_producer_still_expands_to_nothing(monkeypatch):
    """No shipped GATED row carries a producer, so today's safety against a
    GATED key building something is an accident of the table, not something
    ``preset_definitions`` enforces on its own. A double built here WOULD
    build something if its GATED guard were dropped -- ``award_event`` is a
    real ceremony's -- which is what makes this a proof rather than a
    tautology."""
    fake = Preset(
        key="award_cannes_gated_double",
        category="awards",
        name="Cannes (gated test double)",
        description="test double",
        kometa_source="defaults/award/cannes.yml",
        library_types=("Movie",),
        readiness=GATED,
        gated_row=999,
        award_event="cannes",
    )
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (fake,))

    assert preset_definitions(_config([fake.key]), "Movie") == []


def test_the_listings_active_reads_the_setting_for_setting_backed_rows():
    def _row(listing, category, key):
        found = next(c for c in listing if c["key"] == category)
        return next(p for p in found["presets"] if p["key"] == key)

    on = catalog_listing(_config([], charts=True, awards=False, separators=False))
    off = catalog_listing(_config([], charts=False, awards=False, separators=False))

    charts_on = _row(on, "charts", "imdb_charts")
    charts_off = _row(off, "charts", "imdb_charts")
    assert charts_on["setting"] == "collections.charts"
    assert charts_on["active"] is True
    assert charts_off["active"] is False

    oscars = _row(on, "awards", "oscars")
    assert oscars["setting"] == "collections.awards"
    assert oscars["active"] is False  # awards=False in this config

    divider = _row(off, "content_ratings", "content_ratings_divider")
    assert divider["setting"] == "collections.separators"
    assert divider["active"] is False


def test_ordinary_preset_rows_carry_no_setting_in_the_listing():
    listing = catalog_listing(_config(["award_venice"]))
    awards = next(c for c in listing if c["key"] == "awards")
    venice = next(p for p in awards["presets"] if p["key"] == "award_venice")

    assert venice["setting"] is None


# --- the config field -------------------------------------------------------


def test_presets_default_to_none_configured():
    assert load_config(EXAMPLE).collections.presets == []


def test_a_known_preset_key_loads():
    config = build_config(_document(["award_cannes"]))

    assert config.collections.presets == ["award_cannes"]


def test_an_unknown_preset_key_is_refused_at_load_naming_the_catalog():
    with pytest.raises(ValidationError, match="award_oscars"):
        build_config(_document(["award_oscars"]))


def test_the_unknown_preset_error_lists_the_keys_that_do_exist():
    """An operator who mis-types a key gets the catalog, not just a rejection
    -- the ``builder:`` precedent one field along."""
    with pytest.raises(ValidationError, match="award_cannes"):
        build_config(_document(["award_cannnes"]))


def test_a_duplicated_preset_key_is_refused():
    """Twice in the list is not twice the collections -- the expansion is a
    set membership test -- so a duplicate is a config that does not mean what
    it says. Refused rather than silently collapsed.

    Matches "listed twice", not "award_cannes": the key appears in the
    "unknown" refusal's message too, so pinning the key alone would stay
    green even if this raised the wrong refusal for the wrong reason."""
    with pytest.raises(ValidationError, match="listed twice"):
        build_config(_document(["award_cannes", "award_cannes"]))


def test_a_gated_preset_key_is_refused_at_load_naming_its_roadmap_row(monkeypatch):
    """No shipped row is GATED yet -- the eight other categories arrive with
    Task 5, and the gated ones among them cite the roadmap row they wait on.
    The refusal is machinery the config field needs from its first day, so it
    is tested against a row injected here rather than left unproven until
    there is one."""
    monkeypatch.setitem(catalog.BY_KEY, "media_aspect", Preset(
        key="media_aspect",
        category="media",
        name="Aspect ratio",
        description="One collection per aspect ratio.",
        kometa_source="defaults/both/aspect.yml",
        library_types=("Movie", "Show"),
        readiness=GATED,
        gated_row=155,
    ))

    with pytest.raises(ValidationError, match="155"):
        build_config(_document(["media_aspect"]))


def test_the_expansion_cannot_raise_on_a_key_the_catalog_does_not_have():
    """Why the refusal above is the only guard the field needs.

    ``_titles_must_not_collide`` calls ``preset_definitions`` during validation
    of the very model that carries the keys. If that expansion looked its keys
    up -- ``BY_KEY[key]`` -- an unknown one would be a ``KeyError`` raised from
    inside validation, which is a 500 on a settings save instead of a 422
    naming the catalog. It scans the catalog instead, so a key nobody knows
    expands to nothing and the validator gets to do the talking.

    This was written as a test of validator declaration ORDER first. The
    mutation proving it (moving the presets validator after the collision one)
    left it green, because order is not what makes this safe -- the shape of
    the expansion is."""
    config = _config(["not_a_preset", "award_cannes"])

    assert [d.title for d in preset_definitions(config, "Movie")] == [
        "Cannes Golden Palm Winners",
        "Cannes (recent ceremonies)",
    ]


# --- the collision seam -----------------------------------------------------


def test_a_definition_colliding_with_an_ACTIVE_presets_title_is_refused():
    """No new code in the validator: preset titles flow through
    ``default_definitions``, which is what ``_titles_must_not_collide``
    enumerates. Switching a preset on makes its titles built-in titles."""
    document = _document(
        ["award_cannes"],
        definitions=[{
            "title": "Cannes Golden Palm Winners",
            "builder": "plex_id",
            "params": {"ids": ["1"]},
        }],
    )

    with pytest.raises(ValidationError, match="Cannes Golden Palm Winners"):
        build_config(document)


def test_the_same_definition_loads_while_that_preset_is_off():
    """The other direction, which is what makes the test above a test of the
    preset rather than of the title: a toggle switched off frees its titles,
    exactly as ``charts: false`` does."""
    document = _document(
        [],
        definitions=[{
            "title": "Cannes Golden Palm Winners",
            "builder": "plex_id",
            "params": {"ids": ["1"]},
        }],
    )

    config = build_config(document)

    assert config.collections.definitions[0].title == "Cannes Golden Palm Winners"


# --- the endpoint -----------------------------------------------------------


@pytest_asyncio.fixture
async def app(session_factory):
    secrets = Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )
    return create_app(load_config(EXAMPLE), session_factory, secrets)


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": "Bearer %s" % response.json()["token"]}


async def test_the_catalog_endpoint_requires_a_session(client):
    assert (await client.get("/api/collections/catalog")).status_code == 401


async def test_the_catalog_endpoint_answers_without_plex(client, app, auth_headers):
    """Plex-free, and this instance proves it: ``plex_server_factory`` is None
    here, which is the state every other endpoint in this router answers 503
    from. The picker has to work on a replica with no Plex connection, because
    choosing which collections to build is not a question about a server."""
    assert getattr(app.state, "plex_server_factory", None) is None

    response = await client.get("/api/collections/catalog", headers=auth_headers)

    assert response.status_code == 200


async def test_the_catalog_endpoint_lists_every_category_and_the_awards(
    client, auth_headers
):
    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    assert [category["key"] for category in body["categories"]] == list(CATEGORIES)
    awards = next(c for c in body["categories"] if c["key"] == "awards")
    # The 15 award presets plus the Oscars' own setting-backed row.
    assert len(awards["presets"]) == 16
    cannes = next(p for p in awards["presets"] if p["key"] == "award_cannes")
    assert cannes == {
        "key": "award_cannes",
        "name": "Cannes",
        "titles": ["Cannes Golden Palm Winners"],
        "years_title": "Cannes <year>",
        "description": cannes["description"],
        "kometa_source": "defaults/award/cannes.yml",
        "library_types": ["Movie"],
        "readiness": READY,
        "gated_row": None,
        "setting": None,
        "active": False,
    }
    assert cannes["description"].startswith("Cannes: 1 winners collection,")


async def test_the_catalog_endpoint_reports_which_presets_are_active(
    client, app, auth_headers
):
    config = app.state.config_holder.current
    # The same swap the overrides API performs, so this reads the live config
    # rather than the one the process started with -- which is the whole point
    # of ``collections`` being a live section. ``awards: False`` isolates the
    # check to the preset: the Oscars' own row would otherwise also read
    # active from the example config's default ``awards: true``.
    app.state.config_holder.swap(config.model_copy(
        update={
            "collections": config.collections.model_copy(
                update={"presets": ["award_venice"], "awards": False}
            )
        }
    ))

    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    awards = next(c for c in body["categories"] if c["key"] == "awards")
    assert {p["key"] for p in awards["presets"] if p["active"]} == {"award_venice"}


def test_the_listing_is_the_endpoints_only_source_of_truth():
    """The handler is a lookup and a dump; everything it says comes from
    ``catalog_listing``, so the shape can be tested without a request.

    ``charts``/``awards``/``separators`` are switched off here so the three
    setting-backed rows do not also read active -- this test is about the
    ONE preset key, isolated the same way the endpoint test above is."""
    listing = catalog_listing(
        _config(["award_venice"], charts=False, awards=False, separators=False)
    )

    assert [category["key"] for category in listing] == list(CATEGORIES)
    assert sum(len(category["presets"]) for category in listing) == len(CATALOG)
    active = [
        preset["key"]
        for category in listing
        for preset in category["presets"]
        if preset["active"]
    ]
    assert active == ["award_venice"]
