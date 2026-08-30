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
import hashlib
import json
import pathlib
import re
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.collections import catalog, iso_names, packs
from autoposter.collections.builders import REGISTRY
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
ROADMAP = (
    pathlib.Path(__file__).parent.parent
    / "docs" / "superpowers" / "specs" / "2026-08-22-full-parity-roadmap.md"
)
PASSWORD = "correct horse battery staple"

LIBRARY_TYPES = ("Movie", "Show")

# The rows an operator can actually switch on: everything READY that is not a
# setting-backed display row. Built once, at collection time, because the
# expand-and-validate tests below are parametrized over it -- one case per
# preset, so a single bad row names itself in the failure instead of hiding
# inside a loop over forty.
READY_PRESETS = [
    preset for preset in CATALOG if preset.readiness == READY and preset.setting is None
]


def _by_key(preset) -> str:
    return preset.key


def _roadmap_rows() -> set[int]:
    """Every numbered row of the parity roadmap's gap table.

    Read out of the document rather than listed here: the point of
    ``Preset.gated_row`` is that it names a row somebody can go and read, and a
    hand-kept copy of the row numbers would let a typo'd citation stay green.
    """
    rows = set()
    for line in ROADMAP.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\|\s*(\d+)\s*\|", line)
        if match:
            rows.add(int(match.group(1)))
    return rows


def _managed_titles(config, library_type: str) -> list[str]:
    """Every collection title ``default_definitions`` would manage, as a LIST.

    A list and not a set, because the question these tests ask is whether a
    title appears TWICE -- which is the one collision ``_titles_must_not_collide``
    cannot catch (it compares operator definitions against the built-ins, never
    the built-ins against each other). Two built-in definitions sharing a title
    overwrite each other on every pass.

    The three kinds of definition are handled the way ``engine.definition_titles``
    handles them: a smart builder lists its own family, an expanding builder's
    titles are dynamic and are not claimed here, everything else is its title.
    """
    titles: list[str] = []
    for definition in default_definitions(config, library_type):
        builder = REGISTRY[definition.builder]
        smart = getattr(builder, "smart", False)
        pattern = getattr(builder, "TITLE_PATTERN", None)
        if not smart and pattern is None:
            # Both reads have a default, so a builder that owns a FAMILY of
            # titles but has lost its ``smart`` flag would be probed as a
            # single title and could hide a collision -- the one way this
            # helper can be silently weakened. A family builder is
            # recognisable without the flag: it carries ``titles``.
            assert not hasattr(builder, "titles"), definition.builder
        if smart:
            # ``engine.definition_titles`` (engine.py:968-978) is the shape
            # this mirrors, and it has TWO branches for a smart builder: one
            # that lists a family it derives itself (``cs_bucket``), and a
            # fall-through to the definition's own title for one that lists
            # nothing. ``smart_filter`` manages exactly the collection its
            # definition names; ``DynamicBuilder`` declares no ``titles`` at
            # all because a dynamic family's titles are the library's and
            # offline enumeration is impossible (9c decision C6). Both reserve
            # the definition's title and nothing else, so that is what this
            # helper counts for them.
            lister = getattr(builder, "titles", None)
            titles += (
                sorted(lister(library_type, config))
                if lister
                else [definition.title]
            )
        elif pattern is not None:
            continue
        else:
            titles.append(definition.title)
    return titles


def test_the_titles_helper_answers_for_a_smart_builder_that_lists_nothing(
    monkeypatch,
):
    """THE break site row 93 noted, proven before it is fixed.

    ``engine.definition_titles`` falls through to the definition's own title
    for a smart builder that declares no ``titles`` (``engine.py:976-977``) --
    ``smart_filter`` manages the one collection its definition names, and
    ``DynamicBuilder`` declares none at all because a dynamic family's titles
    are the LIBRARY's (``builders/dynamic.py``, "No ``titles()``,
    deliberately"). This helper called ``titles`` unconditionally, so the first
    dynamic preset to reach it raised ``AttributeError`` instead of reporting
    the placeholder title the engine reserves -- and the whole-table collision
    test below is the one that would have raised.

    A double rather than a shipped row, because at this commit no shipped
    preset builds with ``dynamic`` yet; Task 3's packs are what make this
    branch load-bearing.
    """
    double = Preset(
        key="content_dynamic_double",
        category="content",
        name="Dynamic double",
        description="test double",
        kometa_source=catalog.NOT_KOMETA + "written for this test",
        library_types=("Movie",),
        collections=(
            catalog.PresetCollection(
                title="Genre double",
                builder="dynamic",
                params=(("type", "genre"),),
            ),
        ),
    )
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (double,))

    titles = _managed_titles(_config(["content_dynamic_double"]), "Movie")

    assert "Genre double" in titles
    assert titles.count("Genre double") == 1


def test_the_titles_helper_still_refuses_a_family_builder_that_lost_its_flag(
    monkeypatch,
):
    """The guard beside the branch Task 2 changed, pinned.

    Both reads in the helper have a default, so a builder that owns a family of
    titles and has lost its ``smart`` flag would be probed as a single title
    and could hide a collision. A family builder is recognisable without the
    flag -- it carries ``titles`` -- and the guard is what says so. ``imdb_award``
    is an ordinary non-smart builder the example config already builds with, so
    giving it a ``titles`` attribute is exactly the shape the guard describes.
    """
    monkeypatch.setattr(
        REGISTRY["imdb_award"], "titles", lambda *a, **k: set(), raising=False
    )

    with pytest.raises(AssertionError, match="imdb_award"):
        _managed_titles(_config([]), "Movie")


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


# category -> (READY presets, GATED presets, setting-backed rows). The
# transcription's checksum, per category rather than as one total: a row added
# to the wrong tab, or a GATED row quietly flipped READY without its builder,
# moves exactly one number here. See ``catalog.py``'s count-checksum comment
# above ``CATALOG``.
CATALOG_CHECKSUM: dict[str, tuple[int, int, int]] = {
    "awards": (15, 0, 1),
    "charts": (10, 0, 1),
    # 4/1/0 until the divider-polish phase: `content_franchises` moved to the
    # tenth category, `franchises`, when C2 gave the franchises block its own
    # group and divider. The row itself did not change -- only its tab.
    "content": (3, 1, 0),
    "content_ratings": (7, 0, 1),
    "franchises": (1, 0, 0),
    # 1/2/0 until the location-names phase: `location_region` and
    # `location_continent` flipped GATED -> READY when row 196's code->name
    # join shipped (`collections/iso_names.py`).
    "location": (3, 0, 0),
    "media": (3, 1, 0),
    "people": (5, 0, 0),
    "production": (3, 0, 0),
    "time": (1, 2, 0),
}


def test_the_catalog_table_checksum():
    counted = {
        category: (
            len([
                p for p in CATALOG
                if p.category == category and p.setting is None and p.readiness == READY
            ]),
            len([
                p for p in CATALOG
                if p.category == category and p.setting is None and p.readiness == GATED
            ]),
            len([p for p in CATALOG if p.category == category and p.setting is not None]),
        )
        for category in CATEGORIES
    }

    assert counted == CATALOG_CHECKSUM
    assert len(CATALOG) == sum(sum(row) for row in CATALOG_CHECKSUM.values())
    assert {preset.category for preset in CATALOG if preset.setting is not None} == {
        "awards", "charts", "content_ratings",
    }
    # Every tab has something in it. The nine categories were declared by Task
    # 3 with eight of them empty and the frontend's honest "nothing here yet"
    # copy behind them; this is the assertion that they are no longer empty.
    assert all(sum(row) for row in counted.values())


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


def test_a_collection_may_not_widen_its_presets_library_types(monkeypatch):
    """Import-time guard (``catalog._check_collection_library_types``), proven
    on a table written for it, because no shipped row violates it.

    The invariant matters because ``Preset.definitions`` reads
    ``collection.library_types or self.library_types`` -- bypassing the
    preset's own types -- while ``titles()`` and the picker payload iterate
    the preset's. The two assertions below are that bypass happening: the
    widened collection IS built for Show, and the preset's own title list
    never mentions it. The check is what stops such a row shipping.
    """
    widened = Preset(
        key="widened_row",
        category="media",
        name="Widened",
        description="A Show collection under a Movie-only preset.",
        kometa_source=catalog.NOT_KOMETA + "written for this test",
        library_types=("Movie",),
        collections=(
            catalog.PresetCollection(
                title="Widened Shows", builder="plex_all", library_types=("Show",)
            ),
        ),
    )
    assert [d.title for d in widened.definitions("Show")] == ["Widened Shows"]
    assert widened.titles() == []

    monkeypatch.setattr(catalog, "CATALOG", (widened,))
    with pytest.raises(AssertionError, match="widened_row"):
        catalog._check_collection_library_types()


def test_the_shipped_table_only_ever_narrows():
    """The same check over the real catalog, so the guard is not only proven
    against a synthetic row: every collection that sets its own library types
    (the three show-only streaming services) is inside its preset's."""
    catalog._check_collection_library_types()
    narrowing = [
        (preset.key, collection.title)
        for preset in CATALOG
        for collection in preset.collections
        if collection.library_types is not None
    ]
    assert narrowing, "no row narrows, so the check above proved nothing"


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
        assert preset.years_title() == (
            'one per ceremony, named "%s"' % (event.year_title % "<year>")
        )


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
    producer behind them has. Every category is held to it; the parametrized
    expansion tests below say the same thing one row at a time.

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


# --- every READY preset, one parametrized case each --------------------------


def test_there_are_ready_presets_to_parametrize_over():
    """The guard the two parametrized tests below need: an empty parameter
    list is a pass that proves nothing, and pytest reports it as a pass."""
    assert len(READY_PRESETS) == sum(ready for ready, _, _ in CATALOG_CHECKSUM.values())


@pytest.mark.parametrize("preset", READY_PRESETS, ids=_by_key)
def test_a_ready_presets_expansion_is_definitions_the_config_would_accept(preset):
    """Expand-and-validate, one case per row.

    ``CollectionDefinition`` checks the builder against the registry, the
    params against that builder's own model and the ``filters`` block against
    the tier-1 attribute table as it is constructed -- so a preset naming a
    builder that does not exist, passing a param it does not take, or filtering
    on an attribute Phase 9a deferred cannot even be expanded. Round-tripped
    through ``model_validate`` so the claim is a test rather than a side effect
    of construction, and asserted non-empty so a row that expands to nothing on
    every library type fails here rather than shipping as a dead checkbox.
    """
    produced = []
    for library_type in LIBRARY_TYPES:
        definitions = preset.definitions(library_type)
        for definition in definitions:
            assert CollectionDefinition.model_validate(definition.model_dump()) == definition
        # Within ONE library type: two definitions of one preset sharing a
        # title is the same overwrite-every-pass collision the whole-table test
        # below looks for, caught at the row that causes it. Across library
        # types it is normal -- a ceremony's year placeholder is the same title
        # in both, and they are different libraries.
        titles = [definition.title for definition in definitions]
        assert len(titles) == len(set(titles)), (preset.key, library_type)
        produced += definitions

    assert produced, preset.key


@pytest.mark.parametrize("preset", READY_PRESETS, ids=_by_key)
def test_a_ready_preset_loads_and_reaches_default_definitions(preset):
    """The whole path, per row: the key loads through the real config loader
    -- which runs ``_presets_must_be_known_and_ready`` AND the collision
    validator, and therefore this expansion, for every library type -- and the
    definitions it stands for come out of ``default_definitions``."""
    config = build_config(_document([preset.key]))

    assert config.collections.presets == [preset.key]
    for library_type in LIBRARY_TYPES:
        expected = preset.definitions(library_type)
        if not expected:
            # A tail comparison against an empty expectation is ``[] == []``,
            # which passes whatever the loader did. The only correct reason
            # for an empty expansion is that the row does not cover this kind
            # of library at all, so that is what gets asserted instead.
            assert library_type not in preset.library_types, (preset.key, library_type)
            continue
        produced = default_definitions(config, library_type)
        assert produced[len(produced) - len(expected):] == expected


def test_every_ready_preset_at_once_never_builds_one_title_twice():
    """The collision the validator cannot catch, asserted over the whole table.

    ``_titles_must_not_collide`` compares an operator's ``definitions:`` against
    the built-ins; it does not compare the built-ins against each other. Two
    catalog rows -- or one catalog row and a shipped family -- naming the same
    title in one library would overwrite each other on every pass and the
    members hash would flap between them forever. Every switch is on here, which
    is the worst case an operator can reach.
    """
    keys = [preset.key for preset in READY_PRESETS]
    document = _document(keys)
    document["collections"].update({"charts": True, "awards": True, "separators": True})
    config = build_config(document)

    for library_type in LIBRARY_TYPES:
        titles = _managed_titles(config, library_type)
        repeated = sorted({title for title in titles if titles.count(title) > 1})
        assert not repeated, (library_type, repeated)


# --- the dynamic packs -------------------------------------------------------


def _dynamic_rows() -> list[tuple[Preset, catalog.PresetCollection, dict]]:
    """Every READY row that ships a Kometa dynamic pack, with its params.

    A list built from the table rather than a hand-kept key list: a pack added
    to the catalog is held to the contract below without anybody remembering to
    add it here, which is the failure mode a literal tuple of seven keys has.
    """
    return [
        (preset, collection, dict(collection.params))
        for preset in READY_PRESETS
        for collection in preset.collections
        if collection.builder == "dynamic"
    ]


def test_there_are_dynamic_packs_to_hold_to_the_contract():
    """The guard the tests below need: an empty list is a pass that proves
    nothing, and pytest reports it as a pass."""
    assert {preset.key for preset, _, _ in _dynamic_rows()} == {
        "content_genres",
        "time_decade",
        "media_audio_language",
        "media_subtitle_language",
        "location_country",
        "production_studio",
        "production_network",
    }


def test_a_pack_is_exactly_one_definition_whose_type_the_engine_enumerates():
    """A pack is ONE definition that expands at run time, not a table of many.

    The definition validates against ``DynamicParams`` as it is constructed
    (``CollectionDefinition._params_must_satisfy_the_builders_own_model``), so
    a pack naming a type this service does not enumerate, or a param the
    builder does not take, cannot even be expanded -- this asserts the shape
    around that, which construction alone does not say.
    """
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    for preset, collection, params in _dynamic_rows():
        assert params["type"] in DYNAMIC_TYPES, preset.key
        row = DYNAMIC_TYPES[params["type"]]
        # The pack's library types are the TYPE's -- a movie-only type under a
        # both-libraries preset would build nothing on half its libraries and
        # say nothing about it.
        assert set(preset.library_types) <= set(row.kinds), preset.key
        for library_type in preset.library_types:
            definitions = preset.definitions(library_type)
            assert len(definitions) == 1, (preset.key, library_type)
            assert definitions[0].builder == "dynamic"
            assert definitions[0].title == collection.title


def test_every_pack_says_which_dynamic_type_it_is_in_words():
    """The row an operator READS names the type they would write themselves.

    Every pack description already tells them the customisation path is a
    ``definitions:`` entry of their own; that path is worthless without the
    ``type:`` to write in it.
    """
    for preset, _collection, params in _dynamic_rows():
        assert "`type: %s`" % params["type"] in preset.description, preset.key


def test_every_opinion_a_pack_pins_is_stated_in_the_row():
    """Global constraint 10, enforced rather than trusted.

    A cap, an include list or a divergent title format is an OPINION
    (``catalog.py``'s "opinion-free until asked"), and an opinion that ships
    without appearing in the description is one the operator cannot find. The
    numbers are asserted as strings because that is how they appear in the
    sentence an operator reads.
    """
    for preset, _collection, params in _dynamic_rows():
        cap = params.get("max_collections")
        if cap is not None:
            assert str(cap) in preset.description, (preset.key, cap)
        else:
            # The converse, which the check above cannot see: a pin DELETED
            # from the params leaves the sentence that announced it standing,
            # and the row then promises a ceiling the family does not have.
            # Measured as a gap by the plan's own mutation proof -- dropping
            # `("max_collections", len(STUDIO_INCLUDE))` left every test green.
            assert "`max_collections` is pinned" not in preset.description, preset.key
        if params.get("include"):
            assert "include" in preset.description, preset.key
        if params.get("title_format"):
            assert "title" in preset.description.lower(), preset.key


def test_a_language_packs_include_list_still_has_norwegian_in_it():
    """The YAML 1.1 footgun the transcription record found, pinned.

    ``defaults/both/audio_language.yml`` writes Norwegian as an unquoted
    ``- no``, which PyYAML loads as the boolean ``False`` -- so a transcription
    round-tripped through a YAML loader silently drops the one code the
    production movie library actually holds. The record's §1 says the string is
    there; this is what makes a re-transcription that loses it fail.
    """
    packs = [
        (preset, params)
        for preset, _collection, params in _dynamic_rows()
        if params["type"].endswith("_language")
    ]
    assert packs, "no language pack ships, so this proves nothing"
    for preset, params in packs:
        assert "no" in params["include"], preset.key
        assert len(params["include"]) == 187, preset.key


def _table_checksum(entries) -> str:
    """A canonical, order-sensitive sha256 over one pack table.

    ``entries`` is either a ``dict[str, list[str]]`` (an addons table -- one
    ``"key=v1,v2,..."`` line per entry, in the dict's own order) or a plain
    string sequence (an include list -- one entry per line). Order-sensitive
    on purpose: the record's §1 preserves upstream's file order and a
    transcription that reordered entries without dropping any would be a
    finding this checksum should also catch.
    """
    if isinstance(entries, dict):
        lines = ["%s=%s" % (key, ",".join(values)) for key, values in entries.items()]
    else:
        lines = list(entries)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_the_big_pack_tables_have_not_drifted_by_one_entry():
    """Minor 1, extended to every big table this catalog ships. The record
    these eight tables transcribe is gitignored (``.gitignore:23
    .superpowers/``), so ``packs.py`` is the only in-repo copy of them -- and,
    for ``_LANGUAGE_INCLUDE``, a checksum closes a gap the existing
    187+Norwegian pin does not: a same-length substitution (one code swapped
    for a typo) moves neither the count nor the Norwegian membership check.

    The cap-in-the-description mechanism (``test_every_opinion_a_pack_pins_
    is_stated_in_the_row``) does not substitute for this either: both the old
    and the new cap are prose in the same row (e.g. "255" and "256"), so a
    one-entry loss still leaves a substring match. A literal count plus a
    content checksum per table closes it regardless of which way an edit
    moves the table.

    Recomputing a digest after a DELIBERATE table edit: re-verify the edited
    table against the record's own §1 first -- the record is the source of
    truth, not this test -- then regenerate the digest from the verified
    table with ``_table_checksum`` and paste the new hex string in below.
    Never adjust a digest to make a red test green without that check.
    """
    assert len(packs._GENRE_ADDONS) == 10
    assert _table_checksum(packs._GENRE_ADDONS) == (
        "85582082a63c163741c8cb44098104bb5afa36390153bbe9b1803a06c531cd11"
    )

    assert len(packs._LANGUAGE_INCLUDE) == 187
    assert _table_checksum(packs._LANGUAGE_INCLUDE) == (
        "b06b349c369ba76078b1137a2cc651170840a3a73a26d07c0cd61ea91301b7cd"
    )

    assert len(packs._COUNTRY_INCLUDE) == 255
    assert _table_checksum(packs._COUNTRY_INCLUDE) == (
        "8b58aba716f85fc231c62eeec26ac792df015f5f6976a62eb0a17fd5f8f44737"
    )

    assert len(packs._COUNTRY_ADDONS) == 48
    assert _table_checksum(packs._COUNTRY_ADDONS) == (
        "2e6cc1ac88769636cdf7b668eb6820f858a5f1e430bb1218b317999329604568"
    )

    assert len(packs._STUDIO_INCLUDE) == 485
    assert _table_checksum(packs._STUDIO_INCLUDE) == (
        "be107fdae55fc9c24694b718433900088850a6930270a9418dc1f03abf3606f8"
    )

    assert len(packs._STUDIO_ADDONS) == 85
    assert _table_checksum(packs._STUDIO_ADDONS) == (
        "41f834b2e27ecd1c507e676726ab75447a9cd1756a76848de7fcf3b76ee23ffc"
    )

    assert len(packs._NETWORK_INCLUDE) == 272
    assert _table_checksum(packs._NETWORK_INCLUDE) == (
        "7ba061041e77f3334ba600d5178d4282b283de183283442e89f7e8d87ba2a043"
    )

    assert len(packs._NETWORK_ADDONS) == 46
    assert _table_checksum(packs._NETWORK_ADDONS) == (
        "f75e251286b9b9d848e03912911929bf434e2555f75fb606c5e1c170bc443fe3"
    )

    # The two entries upstream's YAML does not hold as strings -- `- 5` parses
    # as the integer and `"#0"` is quoted because `#` opens a comment. The
    # checksum above would catch a lost one, but not what a reader needs to
    # know: that these two are matched as the strings a Plex choice title is.
    assert "5" in packs._NETWORK_INCLUDE and "#0" in packs._NETWORK_INCLUDE


def test_a_packs_placeholder_title_is_listed_and_reserved():
    """The pair C3 asks to read honestly.

    ``titles()`` reports the placeholder -- which IS a title this service
    manages, because the engine reserves ``{definition.title}`` for a smart
    builder that lists none (``engine.py:976-977``) -- and ``years_title()``
    reports the SHAPE of what the family really builds. Neither claims a
    collection title the library has not been asked about.
    """
    for preset, collection, _params in _dynamic_rows():
        assert collection.title in preset.titles(), preset.key
        shape = preset.years_title()
        assert shape is not None, preset.key
        assert shape.startswith("one per "), (preset.key, shape)
        assert "named " in shape, (preset.key, shape)


def test_a_packs_shape_line_comes_from_the_renderer_the_builder_uses():
    """Derived, not restated -- the module's second property, applied to the
    picker's copy. If the shape line were written by hand it could name a title
    format the engine does not use; here it comes out of the same
    ``render_title`` the builder titles collections with, so it cannot.
    """
    from autoposter.collections.dynamic_titles import render_title
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    for preset, _collection, params in _dynamic_rows():
        row = DYNAMIC_TYPES[params["type"]]
        noun = row.name.replace("_", " ")
        key_name = catalog.DYNAMIC_KEY_PLACEHOLDER % noun
        expected = render_title(
            params.get("title_format") or row.title_format,
            key_name,
            catalog.DYNAMIC_LIBRARY_TYPE,
            key=key_name,
            values=(),
            auto_type=row.name,
        )
        assert expected in preset.years_title(), preset.key


def test_a_packs_placeholder_reaches_the_managed_titles_helper():
    """Task 2's fix, exercised by a shipped row rather than a double: the
    placeholder is what the collision test counts for this family, and it is
    what the engine reserves on a real pass."""
    keys = [preset.key for preset, _, _ in _dynamic_rows()]
    config = build_config(_document(keys))

    for library_type in LIBRARY_TYPES:
        titles = _managed_titles(config, library_type)
        for preset, collection, _params in _dynamic_rows():
            if library_type in preset.library_types:
                assert collection.title in titles, (preset.key, library_type)


def test_a_dynamic_packs_builds_string_is_honest_end_to_end():
    """Minor 5, and Important 1's fix pinned past ``dynamic_shape`` called in
    isolation.

    ``years_title`` is the exact string ``CatalogPanel.tsx``'s ``buildsLabel``
    interpolates next to the placeholder in ``titles`` (its two-part branch:
    ``Builds: ${titles}, plus ${dynamic}``), so this reconstructs the whole
    sentence an operator reads for the Genres row through ``catalog_listing``'s
    real payload -- not a ``dynamic_shape`` call with a hand-picked
    ``placeholder_title`` argument. Before Important 1's fix, ``grep "Builds"``
    found only the frontend template and vitest fixture values; nothing on
    either side asserted the rendered string, so this closes that gap.
    """
    listing = catalog_listing(
        _config(["content_genres"], charts=False, awards=False, separators=False)
    )
    content = next(category for category in listing if category["key"] == "content")
    genres = next(p for p in content["presets"] if p["key"] == "content_genres")

    assert genres["titles"] == ["Genres"]
    assert genres["years_title"] == (
        'one per genre the library holds, named "<genre> <Library type>s" '
        '("Genres" above is the reserved definition title -- the family\'s '
        "own collections are the ones per genre, named this way)"
    )

    # The picker's own composition, unchanged by this fix
    # (``CatalogPanel.tsx``'s ``buildsLabel``, two-part branch).
    builds = "Builds: %s, plus %s" % (
        ", ".join(genres["titles"]), genres["years_title"]
    )
    assert builds == (
        "Builds: Genres, plus one per genre the library holds, named "
        '"<genre> <Library type>s" ("Genres" above is the reserved '
        "definition title -- the family's own collections are the ones per "
        "genre, named this way)"
    )


_FORMAT_SENTINEL = "\x1fKEY\x1f"

# A key value shaped like something the ``content_rating`` bucket table
# actually holds (``assets/collections/content_rating_cs.json``'s ``include``
# is the digit strings ``"1"``..``"18"``), used only for the cs_bucket half of
# the collision check below. ``_FORMAT_SENTINEL`` can never equal a bucket
# title -- no bucket title contains ``\x1f`` -- so that half of the check
# would pass even if a shipped pack rendered exactly into one; this
# substitute renders into a string a real bucket title actually IS, so the
# comparison can fail (task-4 review, Important 2).
_BUCKET_SHAPED_KEY = "13"


def _rendered_formats(
    preset, params, key_name: str = _FORMAT_SENTINEL
) -> list[tuple[str, str]]:
    """One ``(library_type, rendered format)`` pair per library this pack
    serves. Defaults to the sentinel no real value can be, for the
    family-vs-family check below; the cs_bucket check passes
    ``_BUCKET_SHAPED_KEY`` instead, since that comparison needs a
    substitution that CAN equal a real title.

    Either engine's type table, because the collision check below spans both
    (task-3 review, Important 1): the two tables are separate for the reasons
    ``facts_family.py``'s docstring gives, but both rows carry the ``name`` and
    ``title_format`` this needs, and ``family_titles`` renders both families
    through the same ``render_title``. A type in neither table raises here
    rather than rendering something nothing builds."""
    from autoposter.collections.dynamic_titles import render_title
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES
    from autoposter.collections.facts_family import FACTS_FAMILY_TYPES

    row = DYNAMIC_TYPES.get(params["type"]) or FACTS_FAMILY_TYPES[params["type"]]
    return [
        (
            library_type,
            render_title(
                params.get("title_format") or row.title_format,
                key_name,
                library_type,
                key=key_name,
                values=(),
                auto_type=row.name,
            ),
        )
        for library_type in preset.library_types
    ]


# The preset-key pairs allowed to render ONE format on one library type, as
# frozenset literals -- pinned, not derived, so a pair that is not one of these
# fails the guard loudly and has to be argued for here (task-3 review,
# Important 1, which is also the review that widened this guard past the
# dynamic packs it could see to the facts-enumerated ones it could not).
#
# All four packs below title with the bare ``<<key_name>>`` on Movie. For the
# three location packs that is Kometa's own format, transcribed; for
# ``content_franchises`` it is the type row's DEFAULT, because
# ``franchise.yml`` spells out no ``title_format`` at all and
# ``FRANCHISE_PARAMS`` deliberately writes the absent key as nothing
# (``packs.py:1434-1439``) -- so that one is OURS, arrived at rather than
# copied. Either way these are not collisions to fix by renaming a
# transcription: they are rows 135/162's documented residual -- families whose
# formats agree, colliding only where their KEY spaces touch -- with the
# touching part measured rather than assumed.
_ALLOWED_FORMAT_SHARERS: frozenset[frozenset[str]] = frozenset({
    # MEASURED overlaps, so a certain duplicate title the moment both are
    # enabled on a library holding such a film, not a hypothetical one:
    #     country ∩ region    = {Antarctica, Micronesia}
    #     country ∩ continent = {Antarctica}
    #     region  ∩ continent = {Antarctica}
    # Upstream's own region.yml/continent.yml carry `Antarctica` in both files,
    # so refusing this would mean a NOT KOMETA title on a pack whose whole
    # claim is that it is a transcription. Disclosed in all three descriptions
    # instead -- named packs, named titles, named consequence, pinned by
    # ``test_the_three_location_packs_disclose_the_titles_they_share``.
    frozenset({"location_country", "location_region"}),
    frozenset({"location_country", "location_continent"}),
    frozenset({"location_region", "location_continent"}),
    # No measured overlap: TMDb franchise names against place names. Allowed
    # rather than argued away because this guard compares FORMATS and not key
    # spaces, and `content_franchises` ships no `include:` at all -- its keys
    # are whatever the library turns out to belong to, so there is no offline
    # set to intersect. A franchise TMDb names `Antarctica` would collide, and
    # nothing offline can see it; that is the same residual, stated.
    frozenset({"content_franchises", "location_country"}),
    frozenset({"content_franchises", "location_region"}),
    frozenset({"content_franchises", "location_continent"}),
})


def _assert_no_two_formats_collide(rows, allowed=frozenset()) -> set[frozenset[str]]:
    """The family-vs-family half of the check below, pulled out so the
    red-first proof (``test_the_collision_test_can_actually_fail``) can drive
    the SAME code the real test runs instead of a copy of it (task-4 review,
    Important 1).

    ``other_name`` leftovers titles go into the same ``seen`` map (Minor 2): a
    leftovers bucket is a rendered title exactly like a KEY title is, and two
    co-enabled packs sharing one -- on the same library type -- would build
    the same collection with nobody the wiser, since neither pack's real
    titles appear anywhere offline for the whole-table title test to catch.

    ``allowed`` is the set of key pairs that may share one rendered title, and
    the default is empty, so a caller that passes nothing gets the flat "no two
    families may agree" rule this always was. EVERY pair among the packs
    sharing a title is checked rather than only the first pair seen, so an
    allowlisted pair cannot shield a third pack behind it; the pairs actually
    exercised are returned, so the caller can pin the allowlist to reality
    instead of letting a stale entry sit forever (task-3 review, Important 1).
    """
    from autoposter.collections.dynamic_titles import _other_title

    seen: dict[tuple[str, str], list[str]] = {}
    exercised: set[frozenset[str]] = set()

    def _record(library_type: str, rendered: str, key: str) -> None:
        sharers = seen.setdefault((library_type, rendered), [])
        for previous in sharers:
            pair = frozenset({previous, key})
            assert pair in allowed, (library_type, rendered, previous, key)
            exercised.add(pair)
        sharers.append(key)

    for preset, _collection, params in rows:
        for library_type, rendered in _rendered_formats(preset, params):
            _record(library_type, rendered, preset.key)

        other_name = params.get("other_name")
        if other_name:
            for library_type in preset.library_types:
                _record(
                    library_type, _other_title(other_name, library_type), preset.key,
                )

    return exercised


def _assert_no_bucket_collisions(rows) -> None:
    """The cs_bucket half of the check below, pulled out for the same reason
    (task-4 review, Important 2).

    Rendered with ``_BUCKET_SHAPED_KEY`` rather than the sentinel: a
    comparison that can never match proves nothing, and a pack that rendered
    exactly into a bucket's title, for some real value it enumerates, would
    build the same collection ``cs_bucket`` already ships.
    """
    from autoposter.collections.buckets import derive_buckets

    for preset, _collection, params in rows:
        for library_type, rendered in _rendered_formats(
            preset, params, key_name=_BUCKET_SHAPED_KEY
        ):
            for bucket in derive_buckets(set(), library_type):
                assert rendered != bucket.title, (
                    library_type, rendered, bucket.title, preset.key,
                )


def test_no_two_families_an_operator_can_co_enable_share_a_title_format():
    """The collision the whole-table title test cannot see.

    ``test_every_ready_preset_at_once_never_builds_one_title_twice`` compares
    the titles this table can NAME, and a dynamic pack names only its
    placeholder -- the family's real titles are the library's and appear
    nowhere offline. So two packs with the same ``title_format`` pass that test
    and then create the same collection title on a real library the moment
    their key spaces touch, which for the two language packs is not a
    coincidence but a certainty: they enumerate almost the same vocabulary.

    This is the offline half of the answer: two enabled families must not share
    a rendered format for one library type, and neither may share an
    ``other_name`` leftovers title with another (Minor 2). It catches the
    whole class rather than the instances that bite today. The residual --
    two DIFFERENT formats that happen to render the same string for specific
    runtime values -- is rows 135/162's documented gap and is not caught here
    or anywhere else.

    BOTH engines' rows, not just the dynamic ones (task-3 review, Important 1).
    Until the location-names phase the facts-enumerated set was one pack and
    this guard never looked at it; then two more shipped, sharing Kometa's bare
    ``<<key_name>>`` with the ``Countries`` pack on the same Movie libraries and
    with measurably overlapping include lists, and the guard could not see a
    duplicate title that was already certain. The four packs that share that
    format are allowlisted BY PAIR in ``_ALLOWED_FORMAT_SHARERS``, with the
    measurement and the reason each is not renamed; anything new that renders
    into an existing title fails here.

    ``cs_bucket``'s static titles are in the comparison because it is a
    shipped family an operator cannot switch off, so a pack that rendered
    into one of its titles would collide with something always present. That
    half is checked with ``_BUCKET_SHAPED_KEY`` rather than the sentinel used
    above, because the sentinel can never equal a real bucket title -- see
    ``_assert_no_bucket_collisions``.
    """
    rows = _dynamic_rows() + _facts_family_rows()

    exercised = _assert_no_two_formats_collide(rows, _ALLOWED_FORMAT_SHARERS)
    # No stale entry: an allowlisted pair that no longer shares a title is a
    # permission nobody needs, and leaving it standing is how the allowlist
    # would grow into "anything goes" one dead line at a time.
    assert exercised == _ALLOWED_FORMAT_SHARERS, sorted(
        sorted(pair) for pair in _ALLOWED_FORMAT_SHARERS - exercised
    )
    _assert_no_bucket_collisions(rows)


def test_the_collision_test_can_actually_fail():
    """A test that has never been seen red is a test nobody has debugged.

    Both halves above are driven here through their real helpers --
    ``_assert_no_two_formats_collide`` and ``_assert_no_bucket_collisions`` --
    over fabricated ``(preset, params)`` pairs, not a re-implementation of
    their loops: the assertion's teeth are proven on the exact code the real
    test runs, not a copy of it that would keep passing if that code were
    deleted, inverted, or had its assertion removed (task-4 review, Important
    1 and 2). ``genre`` is used as every fake's ``type`` purely to satisfy
    ``_rendered_formats``' ``DYNAMIC_TYPES`` lookup; what actually collides in
    each case is the fabricated ``title_format`` or ``other_name``.
    """
    class _FakePreset:
        __slots__ = ("key", "library_types")

        def __init__(self, key: str, library_types: tuple[str, ...]) -> None:
            self.key = key
            self.library_types = library_types

    # Two packs sharing one rendered format on the same library type.
    format_collision = [
        (
            _FakePreset("pack_a", ("Movie",)), None,
            {"type": "genre", "title_format": "Top <<key_name>> Movies"},
        ),
        (
            _FakePreset("pack_b", ("Movie",)), None,
            {"type": "genre", "title_format": "Top <<key_name>> Movies"},
        ),
    ]
    with pytest.raises(AssertionError, match="pack_b"):
        _assert_no_two_formats_collide(format_collision)

    # Two DIFFERENT formats that nonetheless share one `other_name` leftovers
    # title (Minor 2) -- the class the format check above cannot see.
    other_name_collision = [
        (
            _FakePreset("pack_c", ("Movie",)), None,
            {
                "type": "genre", "title_format": "<<key_name>> Leftovers",
                "other_name": "Shared Leftovers",
            },
        ),
        (
            _FakePreset("pack_d", ("Movie",)), None,
            {
                "type": "genre", "title_format": "<<key_name>> Extras",
                "other_name": "Shared Leftovers",
            },
        ),
    ]
    with pytest.raises(AssertionError, match="pack_d"):
        _assert_no_two_formats_collide(other_name_collision)

    # A format that renders exactly into a real `cs_bucket` title
    # (`_BUCKET_SHAPED_KEY` is `"13"`, one of the table's own keys).
    bucket_collision = [
        (
            _FakePreset("pack_e", ("Movie",)), None,
            {"type": "genre", "title_format": "Age <<key_name>>+ Movies"},
        ),
    ]
    with pytest.raises(AssertionError, match="pack_e"):
        _assert_no_bucket_collisions(bucket_collision)

    # The stale-entry equality itself (line 1040) has never been seen red:
    # prove it fails when a COPY of the allowlist carries a phantom pair that
    # no fabricated row exercises.
    shared_format = [
        (_FakePreset("pack_f", ("Movie",)), None,
         {"type": "genre", "title_format": "Shared <<key_name>> Movies"}),
        (_FakePreset("pack_g", ("Movie",)), None,
         {"type": "genre", "title_format": "Shared <<key_name>> Movies"}),
    ]
    phantom_allowlist = {frozenset({"pack_f", "pack_g"}), frozenset({"pack_h", "pack_i"})}
    assert _assert_no_two_formats_collide(shared_format, phantom_allowlist) != phantom_allowlist


# Every pack's `title_format`, as the literal `packs.py` pins. The second site
# of a deliberate two-site edit: the test above proves the seven formats do not
# COLLIDE, which a coordinated edit of two of them could keep true while
# silently renaming a shipped family's every collection. Six of these are
# Kometa's own; `production_studio`'s is this service's divergence, and it is
# the one most likely to be "improved" by a later reader who has not read why
# it is not bare.
_PINNED_FORMATS: dict[str, str] = {
    "content_genres": "<<key_name>> <<library_typeU>>s",
    "time_decade": "Best of <<key_name>>",
    "media_audio_language": "<<key_name>> Audio",
    "media_subtitle_language": "<<key_name>> Subtitles",
    "location_country": "<<key_name>>",
    "production_network": "<<key_name>>",
    "production_studio": "Top <<key_name>> <<library_typeU>>s",
}


def test_every_packs_title_format_is_pinned_here_as_well_as_in_the_pack():
    for preset, _collection, params in _dynamic_rows():
        assert params.get("title_format") == _PINNED_FORMATS.get(preset.key), preset.key
    assert set(_PINNED_FORMATS) == {preset.key for preset, _, _ in _dynamic_rows()}


# --- the facts-enumerated packs ----------------------------------------------
#
# The same contract as the dynamic packs above, over the family whose values
# come from this service's own ``item_facts`` rather than from Plex. A separate
# helper rather than a widened ``_dynamic_rows`` because the two families have
# separate type tables and separate params models, and a single loop would have
# to branch on the builder in every assertion.


def _facts_family_rows() -> list[tuple[Preset, catalog.PresetCollection, dict]]:
    """Every READY row that ships a facts-enumerated pack, with its params."""
    return [
        (preset, collection, dict(collection.params))
        for preset in READY_PRESETS
        for collection in preset.collections
        if collection.builder == "facts_family"
    ]


def test_there_are_facts_family_packs_to_hold_to_the_contract():
    """The guard the tests below need: an empty list is a pass that proves
    nothing, and pytest reports it as a pass.

    UPDATE THIS SET when a pack re-files instead of shipping -- and if all of
    them re-file, delete these tests with the packs rather than leaving a green
    suite asserting over nothing. location_region and location_continent
    shipped in the location-names phase: the code->name join row 196 was filed
    for exists now (collections/iso_names.py), the join was measured per-name
    first (docs/research/tmdb-iso-names/README.md), and their grouping tables
    transcribe region.yml/continent.yml verbatim.
    """
    assert {preset.key for preset, _, _ in _facts_family_rows()} == {
        "content_franchises",
        "location_region",
        "location_continent",
    }


def test_a_facts_pack_is_one_definition_whose_type_this_service_enumerates():
    from autoposter.collections.facts_family import FACTS_FAMILY_TYPES

    for preset, collection, params in _facts_family_rows():
        assert params["type"] in FACTS_FAMILY_TYPES, preset.key
        row = FACTS_FAMILY_TYPES[params["type"]]
        # The pack's library types are the FIELD's -- a movie-only field under
        # a both-libraries preset would build nothing on half its libraries and
        # say nothing about it.
        assert set(preset.library_types) <= set(row.field.kinds), preset.key
        for library_type in preset.library_types:
            definitions = preset.definitions(library_type)
            assert len(definitions) == 1, (preset.key, library_type)
            assert definitions[0].builder == "facts_family"
            assert definitions[0].title == collection.title


def test_every_facts_pack_says_which_type_it_is_in_words():
    for preset, _collection, params in _facts_family_rows():
        assert "`type: %s`" % params["type"] in preset.description, preset.key


def test_every_facts_pack_states_its_coverage_story():
    """The one thing these packs must say that the Plex-enumerated ones need
    not: their values come from what the facts pipeline has VISITED, so a
    freshly-deployed library gets a small, correct, growing family. An
    operator who is not told that reads a half-built family as a bug.

    Three assertions rather than the one the plan named, because the one has
    no teeth on its own: the sweep's two config knobs both carry ``drift`` in
    their names, so a description that deleted the whole coverage paragraph and
    kept ``scheduler.drift_days`` would still contain the substring. What makes
    the story actionable is naming the sweep AND the knobs that move it, so
    both are asserted -- measured, not assumed: deleting the word ``drift``
    from the sentence alone left this test green.
    """
    for preset, _collection, _params in _facts_family_rows():
        description = preset.description
        assert "drift" in description.lower(), preset.key
        assert "scheduler.drift_days" in description, preset.key
        assert "scheduler.drift_batch_size" in description, preset.key


def test_every_opinion_a_facts_pack_pins_is_stated_in_the_row():
    for preset, _collection, params in _facts_family_rows():
        cap = params.get("max_collections")
        if cap is not None:
            assert str(cap) in preset.description, (preset.key, cap)
        else:
            # The converse the check above cannot see, exactly as the dynamic
            # packs' own opinion test spells it: a pin DELETED from the params
            # leaves the sentence that announced it standing.
            assert "`max_collections` is pinned" not in preset.description, preset.key
        if params.get("include"):
            assert "include" in preset.description, preset.key
        if params.get("title_format"):
            assert "title" in preset.description.lower(), preset.key


def test_a_facts_packs_placeholder_title_is_listed_and_reserved():
    """The same pair C3 asks to read honestly for a dynamic pack. The
    placeholder IS a title this service manages -- ``facts_family`` declares no
    ``titles``, so ``engine.definition_titles`` falls through to the
    definition's own -- and ``years_title()`` reports the SHAPE of what the
    family really builds."""
    for preset, collection, _params in _facts_family_rows():
        assert collection.title in preset.titles(), preset.key
        shape = preset.years_title()
        assert shape is not None, preset.key
        assert shape.startswith("one per "), (preset.key, shape)
        assert "named " in shape, (preset.key, shape)


def test_a_facts_packs_shape_line_comes_from_the_renderer_the_builder_uses():
    """Derived, not restated -- ``dynamic_shape``'s own property, applied to
    the second family that has a shape line. If it were written by hand it
    could name a title format the builder does not use; here it comes out of
    the same ``render_title`` ``family_titles`` names collections with."""
    from autoposter.collections.dynamic_titles import render_title
    from autoposter.collections.facts_family import FACTS_FAMILY_TYPES

    for preset, _collection, params in _facts_family_rows():
        row = FACTS_FAMILY_TYPES[params["type"]]
        noun = row.name.replace("_", " ")
        key_name = catalog.DYNAMIC_KEY_PLACEHOLDER % noun
        expected = render_title(
            params.get("title_format") or row.title_format,
            key_name,
            catalog.DYNAMIC_LIBRARY_TYPE,
            key=key_name,
            values=(),
            auto_type=row.name,
        )
        assert expected in preset.years_title(), preset.key


def test_the_new_pack_tables_have_not_drifted_by_one_entry():
    """The same digest discipline ``test_the_big_pack_tables_have_not_drifted_
    by_one_entry`` applies to the shipped seven: the record these tables
    transcribe is gitignored, so ``packs.py`` is the only in-repo copy.

    Recomputing a digest after a DELIBERATE table edit: re-verify against the
    record's §4 FIRST, then regenerate with ``_table_checksum``. Never adjust a
    digest to make a red test green without that check.
    """
    assert len(packs._FRANCHISE_ADDONS) == 12
    assert _table_checksum(packs._FRANCHISE_ADDONS) == (
        "f9deaf7c7a547be038ea241eca719fe620a75df573083a34551258ae18114dce"
    )

    assert len(packs._FRANCHISE_TITLE_OVERRIDE) == 3
    assert _table_checksum(
        {key: [value] for key, value in packs._FRANCHISE_TITLE_OVERRIDE.items()}
    ) == (
        "882f5ed719355d5bf868fc4f3cb094962aceea2bb6f883726ba1dce1b5af7cb1"
    )

    # Every key in both tables is a numeric TMDb collection id written as the
    # STRING the enumeration yields (record §4.5's `FRANCHISE KEY: id`). A
    # transcription that wrote them as Python integers would still validate --
    # `coerce_numbers_to_str` and `_strlist` would both take them -- and would
    # move neither count nor digest away from a reader's eye, so the shape is
    # asserted rather than left to the digest to imply.
    for key in (*packs._FRANCHISE_ADDONS, *packs._FRANCHISE_TITLE_OVERRIDE):
        assert isinstance(key, str) and key.isdigit(), key
    for members in packs._FRANCHISE_ADDONS.values():
        assert all(member.isdigit() for member in members), members

    assert len(packs._REGION_INCLUDE) == 23
    assert _table_checksum(packs._REGION_INCLUDE) == (
        "64f372ef8c1166220614283c90c55b9cdc812ec95661794543e051a6fac72d3c"
    )
    assert len(packs._REGION_ADDONS) == 22
    assert sum(len(v) for v in packs._REGION_ADDONS.values()) == 323
    assert _table_checksum(packs._REGION_ADDONS) == (
        "ef1bad98b54d13103eff37511866638e01173484d7b7975295eec70b46103052"
    )
    assert len(packs._CONTINENT_INCLUDE) == 6
    assert _table_checksum(packs._CONTINENT_INCLUDE) == (
        "1847d7b0d1300b324450d3dbc9e22bac1685c33ccbd63e9015ac54337035c6ab"
    )
    assert len(packs._CONTINENT_ADDONS) == 5
    assert sum(len(v) for v in packs._CONTINENT_ADDONS.values()) == 324
    assert _table_checksum(packs._CONTINENT_ADDONS) == (
        "348c6e9c2e2828d0c32e5298470262bb7bb506fcca8d86729eab9dc4daad79b0"
    )


def test_the_ten_tmdb_spellings_upstream_misses_land_in_their_own_group():
    """The alias overlay is CONSUMED, not merely shipped beside the tables.

    Ten of TMDb's 251 country names are spelled differently by Kometa's
    grouping tables (``iso_names.COUNTRY_NAME_ALIASES``, derived from the same
    fetched bytes -- docs/research/tmdb-iso-names/README.md §3). The family's
    keys are TMDb's spellings, so without the overlay those ten would match no
    member and fall into 'Other Regions'/'Other Continents' -- ten countries
    upstream does carry, silently mis-grouped.

    Both halves are asserted: the shipped params carry every TMDb spelling in
    the SAME group its upstream spelling sits in, and the verbatim tables the
    digest test pins carry none of them, so the transcription stays a
    transcription.
    """
    from autoposter.collections import iso_names

    assert iso_names.COUNTRY_NAME_ALIASES, "no aliases, so this proves nothing"

    for params, verbatim in (
        (dict(packs.REGION_PARAMS), packs._REGION_ADDONS),
        (dict(packs.CONTINENT_PARAMS), packs._CONTINENT_ADDONS),
    ):
        shipped = params["addons"]
        for tmdb_name, upstream_name in iso_names.COUNTRY_NAME_ALIASES.items():
            groups = [
                key for key, members in verbatim.items()
                if key == upstream_name or upstream_name in members
            ]
            assert len(groups) == 1, (tmdb_name, upstream_name, groups)
            assert tmdb_name in shipped[groups[0]], (tmdb_name, groups[0])
            assert tmdb_name not in verbatim[groups[0]], tmdb_name

    # The two the phase's own record singles out: `FO` is the pair whose
    # derivation §3 had to spell out by hand, and `PS` is the one the alias
    # table reaches by its weakest layer.
    region = dict(packs.REGION_PARAMS)["addons"]
    continent = dict(packs.CONTINENT_PARAMS)["addons"]
    assert "Faeroe Islands" in region["Northern Europe"]
    assert "Palestinian Territory" in region["Western Asia"]
    assert "Faeroe Islands" in continent["Europe"]
    assert "Palestinian Territory" in continent["Asia"]


def test_the_three_location_packs_disclose_the_titles_they_share():
    """The disclosure half of the collision answer (task-3 review, Important 1).

    ``_ALLOWED_FORMAT_SHARERS`` lets these three render one title format
    because the alternative is a NOT KOMETA rename of a transcription. What an
    operator gets in exchange is being TOLD, so the telling is pinned here
    rather than left to survive the next edit of a three-hundred-word row.

    The overlap is re-MEASURED off the shipped include lists rather than
    restated, so a table that regenerated wider fails this test instead of
    leaving three descriptions naming yesterday's titles -- and the failure
    lands next to the allowlist that would then also be understating things.
    """
    universes = {
        key: set(dict(params)["include"])
        for key, params in (
            ("location_country", packs.COUNTRY_PARAMS),
            ("location_region", packs.REGION_PARAMS),
            ("location_continent", packs.CONTINENT_PARAMS),
        )
    }

    assert universes["location_country"] & universes["location_region"] == {
        "Antarctica", "Micronesia",
    }
    assert universes["location_country"] & universes["location_continent"] == {
        "Antarctica"
    }
    assert universes["location_region"] & universes["location_continent"] == {
        "Antarctica"
    }

    # Each row names the packs it collides with, the title(s) it collides on,
    # and the consequence. The consequence is pinned as a substring for the
    # same reason the coverage story's three assertions are: a row that kept
    # the word `Antarctica` in some other sentence and dropped the warning
    # would otherwise still pass.
    for key, others, shared in (
        ("location_country", ("Regions", "Continents"), ("Antarctica", "Micronesia")),
        ("location_region", ("Countries", "Continents"), ("Antarctica", "Micronesia")),
        ("location_continent", ("Countries", "Regions"), ("Antarctica",)),
    ):
        description = catalog.BY_KEY[key].description
        for other in others:
            assert other in description, (key, other)
        for title in shared:
            assert title in description, (key, title)
        assert "one title" in description, key


# --- the gated rows ----------------------------------------------------------


def test_every_gated_row_cites_a_roadmap_row_that_exists():
    """"Not yet" without a number is a shrug (``Preset.gated_row``'s docstring),
    and a number nobody can look up is worse than none -- so the citation is
    checked against the roadmap document itself."""
    rows = _roadmap_rows()
    # The parse works: rows this catalog cites -- including the two filed for
    # it (160, 161) -- and one it does not.
    assert {96, 102, 155, 160, 161, 194} <= rows
    assert 999999 not in rows

    gated = [preset for preset in CATALOG if preset.readiness == GATED]
    assert len(gated) == sum(gated_count for _, gated_count, _ in CATALOG_CHECKSUM.values())
    for preset in gated:
        assert preset.gated_row in rows, (preset.key, preset.gated_row)


def test_no_preset_still_waits_on_the_row_the_dynamic_engine_closed():
    """Row 102 closed in phase 10a-2 and its preset-expansion phase (10b) has
    shipped. A row still citing it would be citing work that is DONE, which is
    the same as citing nothing -- so every preset that was gated on it has
    either shipped or now names what actually blocks it.

    ``DYNAMIC_ENGINE_ROW`` stays in the module: the packs' descriptions cite it
    as the phase that shipped their engine, which is a different kind of
    citation from a blocker.
    """
    still_waiting = [
        preset.key for preset in CATALOG
        if preset.gated_row == catalog.DYNAMIC_ENGINE_ROW
    ]

    assert still_waiting == []
    assert catalog.BY_KEY["content_franchises"].gated_row is None
    assert catalog.BY_KEY["time_year"].gated_row == catalog.RELATIVE_YEAR_ROW
    for key in ("location_region", "location_continent"):
        assert catalog.BY_KEY[key].gated_row is None, key
        assert catalog.BY_KEY[key].readiness == catalog.READY, key


def test_no_preset_still_waits_on_the_rows_the_facts_enumeration_closed():
    """Rows 189 and 192 closed with the prefetch phase: the TMDb walk both were
    filed for is this service's own facts pipeline now, and ``facts_family``
    consumes it. A row still citing either as a BLOCKER would be citing work
    that is done -- which is the same as citing nothing.

    Both constants stay in the module, in the shape ``TMDB_LANGUAGE_NAME_ROW``
    already had: cited rather than waited on. ``content_franchises`` names 192
    as the enumeration it ships on, and the two location packs name 189 as the
    values they build from and 196 as the join that let them ship -- both
    cited in running prose now, neither waited on.
    """
    assert [
        preset.key for preset in CATALOG
        if preset.gated_row in (catalog.TMDB_ORIGIN_COUNTRY_ROW,
                                catalog.TMDB_COLLECTION_TYPE_ROW)
    ] == []

    assert "row %d" % catalog.TMDB_COLLECTION_TYPE_ROW in catalog.BY_KEY[
        "content_franchises"
    ].description
    for key in ("location_region", "location_continent"):
        description = catalog.BY_KEY[key].description
        assert "row %d" % catalog.TMDB_ORIGIN_COUNTRY_ROW in description, key
        assert "row %d" % catalog.TMDB_COUNTRY_NAME_ROW in description, key


def test_no_preset_still_waits_on_the_person_rows():
    """Row 83 closed in phase 10c-lite -- the filmographies, their TMDb
    biographies and their profile photos all ship. Row 194 closed in phase B:
    the credit scan, the counted enumeration and the four people SEARCH rows
    all ship, so the four Top-* packs build.

    ``PERSON_SCAN_ROW`` stays in the module, in the shape
    ``TMDB_LANGUAGE_NAME_ROW`` already had: CITED rather than waited on. The
    Director starter set names it as the scan its enumerated sibling needed,
    which is a different kind of citation from a blocker.
    """
    assert [
        preset.key for preset in CATALOG
        if preset.gated_row in (83, catalog.PERSON_SCAN_ROW)
    ] == []
    for key in ("people_top_actors", "people_top_directors",
                "people_top_writers", "people_top_producers"):
        assert catalog.BY_KEY[key].readiness == READY, key
        assert catalog.BY_KEY[key].gated_row is None, key
    assert "row %d" % catalog.PERSON_SCAN_ROW in catalog.BY_KEY[
        "people_directors"
    ].description


def _credits_family_rows() -> list[tuple[Preset, catalog.PresetCollection, dict]]:
    """Every READY row that ships a counted-credits family, with its params."""
    return [
        (preset, collection, dict(collection.params))
        for preset in READY_PRESETS
        for collection in preset.collections
        if collection.builder == "credits_family"
    ]


def test_the_four_people_packs_are_one_counted_credits_definition_each():
    """A pack is ONE definition that expands at run time, and its type is one
    of the four credit kinds the scan actually stores.

    The library types are the TYPE's: ``actor`` is both (Plex answers a show
    library's ``<Role>`` tags), and the three crew kinds are movie-only in
    every table that touches them -- Kometa's ``movie_only_searches``, its
    ``filters_by_type``, and the probe's own D7 measurement that a show
    section enumerates them empty rather than refusing.
    """
    from autoposter.collections.builders.credits_family import TITLE_FORMATS

    assert {preset.key for preset, _, _ in _credits_family_rows()} == {
        "people_top_actors", "people_top_directors",
        "people_top_writers", "people_top_producers",
    }
    for preset, collection, params in _credits_family_rows():
        assert params["type"] in TITLE_FORMATS, preset.key
        expected = ("Movie", "Show") if params["type"] == "actor" else ("Movie",)
        assert preset.library_types == expected, preset.key
        for library_type in preset.library_types:
            definitions = preset.definitions(library_type)
            assert len(definitions) == 1, (preset.key, library_type)
            assert definitions[0].builder == "credits_family"
            assert definitions[0].title == collection.title


def test_a_credits_packs_placeholder_title_is_listed_and_reserved():
    """The same pair C3 asks to read honestly for the other two families:
    ``titles()`` reports the placeholder, which IS a title this service manages
    (the engine reserves ``{definition.title}`` for a smart builder that lists
    none), and ``years_title()`` reports the SHAPE of what the family really
    builds. Neither claims a person's name the cache has not counted."""
    for preset, collection, _params in _credits_family_rows():
        assert collection.title in preset.titles(), preset.key
        shape = preset.years_title()
        assert shape is not None, preset.key
        assert shape.startswith("one per "), (preset.key, shape)
        assert "named " in shape, (preset.key, shape)


def test_a_credits_packs_shape_line_comes_from_the_renderer_the_builder_uses():
    """Derived, not restated -- the property both sibling families are held to.
    The format is ``credits_family.TITLE_FORMATS``', which is the table the
    builder titles collections from, so the picker cannot describe a shape the
    builder will not produce."""
    from autoposter.collections.builders.credits_family import TITLE_FORMATS
    from autoposter.collections.dynamic_titles import render_title

    for preset, _collection, params in _credits_family_rows():
        noun = params["type"]
        key_name = catalog.DYNAMIC_KEY_PLACEHOLDER % noun
        expected = render_title(
            params.get("title_format") or TITLE_FORMATS[noun],
            key_name,
            catalog.DYNAMIC_LIBRARY_TYPE,
            key=key_name,
            values=(),
            auto_type=noun,
        )
        assert expected in preset.years_title(), preset.key


def test_every_people_pack_discloses_the_two_things_it_cannot_promise():
    """The row an operator READS carries both honesty clauses, because neither
    is visible from the collections themselves.

    One: the membership is the Plex TAG, not a TMDb filmography -- the two are
    different memberships under the same name, and roadmap row 194 left the
    choice open for this task to make and disclose. Two: the counts are a
    FLOOR, because Plex caps its credit list at 200 people per item, so
    "the most-credited" cannot be read as "the most-credited in the library".
    """
    for preset, _collection, params in _credits_family_rows():
        description = preset.description
        assert "TAG" in description, preset.key
        assert "filmography" in description.lower(), preset.key
        assert "200" in description, preset.key
        assert "FLOOR" in description, preset.key
        # The pinned opinions, stated in the row the way both sibling families
        # are held to state theirs.
        assert "depth: %d" % params["depth"] in description, preset.key
        assert "limit: %d" % params["limit"] in description, preset.key
        assert "credits_scan_days" in description, preset.key


def test_every_gated_key_is_refused_at_load_naming_its_row():
    """The refusal the picker's disabled state is backed by, over every gated
    row rather than one injected double: a key copied out of the picker by hand
    is refused, and the refusal says which roadmap row it waits on."""
    for preset in CATALOG:
        if preset.readiness != GATED:
            continue
        with pytest.raises(ValidationError, match=str(preset.gated_row)):
            build_config(_document([preset.key]))


def test_a_gated_row_expands_to_nothing_even_though_it_is_in_the_catalog():
    for preset in CATALOG:
        if preset.readiness != GATED:
            continue
        for library_type in LIBRARY_TYPES:
            assert preset.definitions(library_type) == [], preset.key


# --- provenance --------------------------------------------------------------


def test_every_row_cites_a_real_kometa_defaults_file_or_says_it_has_none():
    """``_KOMETA_DEFAULTS`` is strict, the way ``_KOMETA_FILES`` already was: a
    row's ``kometa_source`` is either a path in the pinned set of Kometa
    defaults files this catalog transcribes, or a sentence that starts by
    saying it has no Kometa source at all. There is no third case -- an
    invented "defaults/both/whatever.yml" is what this refuses."""
    for preset in CATALOG:
        if preset.kometa_source.startswith(catalog.NOT_KOMETA):
            assert len(preset.kometa_source) > len(catalog.NOT_KOMETA), preset.key
            continue
        assert preset.kometa_source in catalog._KOMETA_DEFAULTS, preset.key


def test_a_row_citing_a_defaults_file_nobody_pinned_raises(monkeypatch):
    """The strictness proven directly, the shape
    ``test_a_kometa_file_missing_for_a_real_event_raises`` already has: the
    import-time check is what makes a mistyped path fail loudly instead of
    shipping a provenance line that points at nothing."""
    fake = Preset(
        key="content_invented",
        category="content",
        name="Invented",
        description="test double",
        kometa_source="defaults/both/not_a_real_defaults_file.yml",
        library_types=("Movie",),
    )
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (fake,))

    with pytest.raises(AssertionError, match="not_a_real_defaults_file"):
        catalog._check_kometa_sources()


def test_the_pinned_set_holds_no_path_nothing_cites():
    """The check reads only one way on its own -- it refuses a row citing an
    unpinned path, and says nothing about a pinned path no row cites. A leftover
    entry is a Kometa file somebody meant to transcribe and did not, and it
    would silently license a future typo that happened to match it."""
    cited = {preset.kometa_source for preset in CATALOG}

    assert catalog._KOMETA_DEFAULTS <= cited, sorted(catalog._KOMETA_DEFAULTS - cited)


def test_a_row_carrying_both_producers_raises(monkeypatch):
    """``Preset.definitions`` asks ``award_event`` first, so a row with both
    families' producer would expand the ceremony and silently drop its table.
    The import-time guard proven directly, against a double that carries
    both."""
    fake = Preset(
        key="award_cannes_and_a_table",
        category="awards",
        name="Cannes (two-producer test double)",
        description="test double",
        kometa_source="defaults/award/cannes.yml",
        library_types=("Movie",),
        award_event="cannes",
        collections=(catalog.PresetCollection(
            title="Cannes, again", builder="tmdb_chart", params=(("chart", "popular"),)
        ),),
    )
    monkeypatch.setattr(catalog, "CATALOG", catalog.CATALOG + (fake,))

    with pytest.raises(AssertionError, match="both an award_event"):
        catalog._check_one_producer_per_row()


def test_the_award_paths_are_in_the_pinned_set_rather_than_a_second_copy():
    """``_KOMETA_DEFAULTS`` derives the fifteen award paths from
    ``_KOMETA_FILES`` instead of restating them, so a ceremony whose file name
    is corrected in one place cannot be left stale in the other."""
    for key, stem in catalog._KOMETA_FILES.items():
        assert "defaults/award/%s.yml" % stem in catalog._KOMETA_DEFAULTS, key


# --- the transcriptions ------------------------------------------------------
#
# One test per transcribed table rather than one test over all of them: a
# single failing assertion in a combined test hid which pack had moved behind
# whichever one happened to be asserted first, and there are now eleven of
# them.
#
# What each of these pins is the SHAPE -- how many collections a Kometa table
# contributes, under which titles -- plus, for the content-rating families, a
# DIGEST over the whole expansion. The shape alone is not enough for a rating
# family: a watch-provider id off by one builds a full, plausible collection of
# the wrong service's catalogue, and a dropped or mistyped content-rating addon
# quietly leaves part of a bucket out. Neither shows up as an error anywhere
# downstream, and a count checksum cannot see a value that was mistyped rather
# than dropped.
#
# The digests were computed at transcription time from the upstream files
# themselves (``yaml.safe_load`` over each ``defaults/.../content_rating_*.yml``,
# key prepended, de-duped, exactly as the tables are built), NOT copied off the
# module -- so they are an independent second opinion about the transcription
# rather than a restatement of it.
#
# Everything here is read off the expansion rather than out of the module's
# private tables, so what is checked is what a deployment would actually build.


def _rating_buckets(key: str, library_type: str) -> dict[str, list[str]]:
    """One content-rating family's expansion, as ``title -> filter values``."""
    return {
        definition.title: definition.filters["content_rating"]
        for definition in catalog.BY_KEY[key].definitions(library_type)
    }


def _digest(buckets: dict[str, list[str]]) -> str:
    """A stable checksum over the WHOLE of one family's expansion.

    A canonical JSON dump (insertion order, no whitespace) hashed with sha256:
    titles, bucket order, value order and every value itself. One character
    changed anywhere in a hundred-value table moves it.
    """
    return hashlib.sha256(
        json.dumps(buckets, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# family key -> (the library type its expansion is digested on, the digest).
# All seven families, the two US ones retrofitted onto tables that were already
# shipped and verbatim-reviewed.
CONTENT_RATING_DIGESTS: dict[str, tuple[str, str]] = {
    "content_ratings_us": (
        "Movie", "628aa7c65766c5d8e58f7547befe5703039b5ca4c7ce645a166fcc8b47e0d51f",
    ),
    "content_ratings_us_show": (
        "Show", "0f48210982cc4ff5115370e6d8e06a14e9bbc2ba901e0261ef9f335b053cc55b",
    ),
    "content_ratings_uk": (
        "Movie", "03b3e84aa790f2a55cfbd49a6f11828226c27df98acf363594b691a59d7eb26b",
    ),
    "content_ratings_de": (
        "Movie", "9c4df7cff8130240d0797010d80803fc98a76355253a7746c26b5db52d03bad3",
    ),
    "content_ratings_au": (
        "Movie", "40a982de35db92a10de56fa9de999ec204ae5cb1a7db56fa21a1f6d7a84332f8",
    ),
    "content_ratings_nz": (
        "Movie", "dbcf55f688ddb64e6447bae6cc7e2e737f20f401c9654e1cb24821fe1f676616",
    ),
    "content_ratings_mal": (
        "Movie", "a6f5fcf91e8f97b29e4c0b5d69cd9e9827e82a9faa36fc10c9f4e29f048ba418",
    ),
}

# The five regional families -> (the bucket titles WITHOUT the library-type
# suffix, in Kometa's own include order; the stored value count of each bucket;
# the file's own raw value count; how many of those the de-dupe drops).
#
# The last two columns are the interesting ones. Three of these files list a
# bucket's own key among its addons, and MAL's G lists ``0`` as both a YAML int
# and a YAML string -- so for those families the STORED count is deliberately
# below the file's, and the delta is stated here rather than left to look like
# a dropped value.
_REGIONAL_RATING_CHECKSUMS: dict[str, tuple[list[str], list[int], int, int]] = {
    "content_ratings_uk": (
        ["UK U", "UK PG", "UK 12", "UK 12A", "UK 15", "UK 18", "UK R18"],
        [27, 23, 11, 12, 12, 13, 5],
        103, 0,
    ),
    "content_ratings_de": (
        ["DE 0", "DE 6", "DE 12", "DE 16", "DE 18", "DE BPjM"],
        [25, 24, 13, 9, 12, 3],
        86, 0,
    ),
    "content_ratings_au": (
        ["AU G", "AU PG", "AU M", "AU MA15+", "AU R18+", "AU X18+"],
        [28, 24, 15, 12, 15, 6],
        102, 2,
    ),
    "content_ratings_nz": (
        ["NZ G", "NZ PG", "NZ M", "NZ R13", "NZ RP13", "NZ R15", "NZ R16",
         "NZ RP16", "NZ R18", "NZ RP18", "NZ R"],
        [28, 24, 15, 3, 2, 11, 3, 2, 14, 2, 4],
        111, 3,
    ),
    "content_ratings_mal": (
        ["MAL G", "MAL PG", "MAL PG-13", "MAL R", "MAL R+", "MAL Rx"],
        [21, 21, 16, 11, 2, 2],
        74, 1,
    ),
}


def test_the_us_certifications_transcription():
    """The Movie half of the US pack -- and the titles it must keep.

    ``G Movies`` and its four neighbours shipped before the regional families
    existed; deployments already build them. The regional rows gained a country
    prefix precisely so these five would not have to change, so this title list
    is an operator-facing contract and not just a checksum.
    """
    ratings = _rating_buckets("content_ratings_us", "Movie")

    assert list(ratings) == [
        "G Movies", "PG Movies", "PG-13 Movies", "R Movies", "NC-17 Movies",
    ]
    assert [len(values) for values in ratings.values()] == [23, 21, 19, 12, 6]
    # One addon per bucket, spot-checked: these are the certifications that
    # make the bucket more than its own name, and the ones an operator would
    # never notice missing.
    assert "TV-14" in ratings["PG-13 Movies"]
    assert "gb/U" in ratings["G Movies"]
    assert "TV-MA" in ratings["R Movies"]
    assert _digest(ratings) == CONTENT_RATING_DIGESTS["content_ratings_us"][1]


def test_the_us_tv_ratings_transcription():
    """The Show half of the same pack -- a separate upstream file with its own
    include list. Five buckets, 73 values, and no bucket lists its own key
    upstream, so these counts are the file's own addon counts plus one for the
    prepended key."""
    show_ratings = _rating_buckets("content_ratings_us_show", "Show")

    assert list(show_ratings) == [
        "TV-G Shows", "TV-Y Shows", "TV-PG Shows", "TV-14 Shows", "TV-MA Shows",
    ]
    assert [len(values) for values in show_ratings.values()] == [20, 15, 13, 14, 11]
    assert sum(len(values) for values in show_ratings.values()) == 73
    # Spot-checked addons, in the three directions the bucket names do not
    # predict: the film certifications Kometa folds into the TV buckets, the
    # British ones, and the TV-Y7 pair that belongs to TV-Y rather than to a
    # bucket of its own. Getting these wrong is invisible until an operator
    # wonders why a PG-13-tagged series landed nowhere.
    assert "PG-13" in show_ratings["TV-14 Shows"]
    assert "gb/18" in show_ratings["TV-MA Shows"]
    assert "TV-Y7-FV" in show_ratings["TV-Y Shows"]
    assert _digest(show_ratings) == CONTENT_RATING_DIGESTS["content_ratings_us_show"][1]


@pytest.mark.parametrize("key", sorted(_REGIONAL_RATING_CHECKSUMS))
def test_a_regional_content_rating_family_transcribes_its_kometa_file(key):
    """Each regional family, against its own upstream file.

    One case per family so a single mistyped certification names its country
    in the failure. Both library types are checked because these are single
    ``defaults/both`` files: the same buckets build ``UK 12 Movies`` on a film
    library and ``UK 12 Shows`` on a television one, with identical values.
    """
    titles, counts, raw_total, deduped = _REGIONAL_RATING_CHECKSUMS[key]
    library_type, expected_digest = CONTENT_RATING_DIGESTS[key]
    assert library_type == "Movie"

    movies = _rating_buckets(key, "Movie")
    assert list(movies) == ["%s Movies" % title for title in titles]
    assert [len(values) for values in movies.values()] == counts
    # The de-dupe delta, stated rather than inferred: what is stored plus what
    # the de-dupe dropped is exactly what the upstream file lists.
    assert sum(counts) + deduped == raw_total
    assert _digest(movies) == expected_digest

    shows = _rating_buckets(key, "Show")
    assert list(shows) == ["%s Shows" % title for title in titles]
    assert list(shows.values()) == list(movies.values())


def test_the_regional_buckets_that_list_their_own_key_store_it_once():
    """Three upstream files put a bucket's key in its own addon list, and MAL
    lists ``0`` twice in two YAML types. The tables prepend the key the way the
    US ones do, so without an order-preserving de-dupe these buckets would
    carry a duplicate filter value -- harmless to Plex, and a silent divergence
    from every count this file pins."""
    au = _rating_buckets("content_ratings_au", "Movie")
    nz = _rating_buckets("content_ratings_nz", "Movie")
    mal = _rating_buckets("content_ratings_mal", "Movie")

    assert au["AU G Movies"].count("G") == 1
    assert au["AU PG Movies"].count("PG") == 1
    assert nz["NZ G Movies"].count("G") == 1
    assert nz["NZ PG Movies"].count("PG") == 1
    assert nz["NZ R18 Movies"].count("R18") == 1
    # The int/string pair, one certification once ``str()`` has been applied.
    assert mal["MAL G Movies"].count("0") == 1
    # De-duped, not re-ordered: the key still leads its bucket, as it does in
    # the US tables.
    assert au["AU G Movies"][0] == "G"
    assert nz["NZ R18 Movies"][0] == "R18"


def test_the_regional_values_are_strings_without_stray_whitespace():
    """Two transcription traps at once.

    DE's and UK's bucket keys are YAML INTEGERS upstream (``0:``, ``12:``);
    they are certifications rather than numbers and are stored as strings, or
    the filter would compare an int against Plex's text. And UK's ``18`` bucket
    carries ``gb/18+`` with a trailing space in the raw file -- YAML strips it,
    and a value that kept it would match nothing at all.
    """
    de = _rating_buckets("content_ratings_de", "Movie")
    uk = _rating_buckets("content_ratings_uk", "Movie")

    assert list(de)[:2] == ["DE 0 Movies", "DE 6 Movies"]
    assert "gb/18+" in uk["UK 18 Movies"]
    for key in _REGIONAL_RATING_CHECKSUMS:
        for title, values in _rating_buckets(key, "Movie").items():
            for value in values:
                assert isinstance(value, str), (title, value)
                assert value == value.strip(), (title, value)


def test_no_content_rating_family_builds_a_not_rated_bucket():
    """Kometa's ``other_name`` bucket is omitted by all seven, deliberately: it
    is the set complement of the buckets that DID match, which this expansion
    cannot compute without scanning the library -- and the Common Sense family
    already builds a collection under that exact title on both kinds of
    library, so building one here would collide with it seven times over."""
    for key, (library_type, _digest_value) in CONTENT_RATING_DIGESTS.items():
        titles = list(_rating_buckets(key, library_type))
        assert not [title for title in titles if title.startswith("Not Rated")], key


def test_the_regional_rows_say_where_their_letters_mislead():
    """A bucket whose letter means something else in another country is a
    collection an operator only finds wrong by opening it, so the rows say so
    in the description the picker shows."""
    nz = catalog.BY_KEY["content_ratings_nz"].description
    au = catalog.BY_KEY["content_ratings_au"].description

    # NZ's R is the X-rated restricted bucket, not "R-rated".
    assert "RESTRICTED" in nz
    # The RP buckets overlap the R ones by Kometa's own design, kept verbatim.
    assert "'NZ RP13'" in nz
    # AU/NZ M is not the US M.
    assert "NOT the US 'M'" in au
    assert "'NZ M' is not the US 'M'" in nz


def test_the_streaming_transcription():
    streaming = {
        library_type: {
            definition.title: definition.params
            for definition in catalog.BY_KEY["production_streaming"].definitions(library_type)
        }
        for library_type in LIBRARY_TYPES
    }
    # Fifteen services, three of them (Crunchyroll, discovery+, hayu) offered
    # for Show libraries only, as Kometa's allowed_libraries has them.
    assert len(streaming["Show"]) == 15
    assert len(streaming["Movie"]) == 12
    assert set(streaming["Show"]) - set(
        title.replace(" Movies", " Shows") for title in streaming["Movie"]
    ) == {"Crunchyroll Shows", "discovery+ Shows", "hayu Shows"}
    assert streaming["Movie"]["Netflix Movies"]["with_watch_providers"] == "8"
    assert streaming["Movie"]["Prime Video Movies"]["with_watch_providers"] == "9"
    assert streaming["Movie"]["Disney+ Movies"]["with_watch_providers"] == "337"
    # The two pipe forms, which are TMDb's "either of these" and the reason the
    # column is a string rather than an int.
    assert streaming["Movie"]["Paramount+ Movies"]["with_watch_providers"] == "531|1770"
    assert streaming["Movie"]["AMC+ Movies"]["with_watch_providers"] == "528|1854"
    # Without the region TMDb ignores the provider filter and answers the
    # UNFILTERED query -- every title instead of the service's.
    assert {params["watch_region"] for params in streaming["Show"].values()} == {"US"}


def test_the_resolution_transcription():
    resolutions = {
        definition.title: definition.filters["resolution"]
        for definition in catalog.BY_KEY["media_resolution"].definitions("Movie")
    }
    assert resolutions == {
        "4k Movies": ["4k", "8k"],
        "1080 Movies": ["1080", "2k"],
        "720 Movies": ["720"],
        "480 Movies": ["480", "144", "240", "360", "sd", "576"],
    }


def test_the_universes_transcription():
    universes = catalog.BY_KEY["content_universes"]
    assert len(universes.definitions("Movie")) == 8
    # The three Kometa restricts to film libraries.
    assert {d.title for d in universes.definitions("Movie")} - {
        d.title for d in universes.definitions("Show")
    } == {"Alien / Predator", "Conjuring Universe", "Fast & Furious"}
    assert all(
        d.params["list"].startswith("ls") for d in universes.definitions("Movie")
    )
    # The split, held from this side too: DC left this pack for content_dc,
    # and a row drifting back in would collide with that preset's first title
    # the moment both were enabled.
    assert "DC Universe" not in {d.title for d in universes.definitions("Movie")}


def test_the_universe_ids_have_not_drifted_by_one_entry():
    """The eight-id drift guard the DC re-point exposed a gap for: nothing else
    in this suite pins the ids themselves, only the count and shape
    (``test_the_universes_transcription``). One list has already died and been
    swapped once, and the swap's replacement has since left for ``content_dc``
    (the one-line history above ``_UNIVERSE_LISTS``' Fast & Furious row); this
    makes the next such edit a deliberate two-site change -- table plus digest
    -- rather than a silent one.

    Recomputing the digest after a DELIBERATE id edit: verify the new id
    against its source first, then regenerate with ``_table_checksum`` over
    the ids in table order and paste the new hex string in below. Never
    adjust it just to turn a red test green.
    """
    ids = tuple(list_id for _title, list_id, _types in catalog._UNIVERSE_LISTS)
    assert len(ids) == 8
    assert _table_checksum(ids) == (
        "7470699d2bf5d0300047204cc68d6d2a968900383772164bf6551960882e5a5a"
    )


def test_the_dc_transcription():
    """The three-way split's shape: three rows, the exact titles in the
    directive's order, the per-builder split (1x tmdb_list / 2x mdblist_list)
    and the exact params -- each validated by its builder's own model at
    expansion, which is what ``PresetCollection.definition`` does."""
    dc = catalog.BY_KEY["content_dc"]
    movie = dc.definitions("Movie")
    assert [(d.title, d.builder) for d in movie] == [
        ("DC Universe", "tmdb_list"),
        ("DC Extended Universe", "mdblist_list"),
        ("In Association With DC", "mdblist_list"),
    ]
    assert movie[0].params == {"id": 8642250}
    assert movie[1].params == {"list": "fa11en82/dc-extended-universe"}
    assert movie[2].params == {"list": "fa11en82/in-association-with-dc"}
    assert [d.title for d in dc.definitions("Show")] == [d.title for d in movie]


def test_the_dc_source_refs_have_not_drifted_by_one_entry():
    """The two-site-change law, extended to the DC table the way
    ``test_the_universe_ids_have_not_drifted_by_one_entry`` states it for the
    universe ids: editing a source ref is a deliberate two-site change --
    table plus this digest -- never a silent one. Recomputing after a
    DELIBERATE edit: verify the new ref against its LIVE list first (the T1
    probe pattern -- fetched through the shipped client, never assumed), then
    regenerate with ``_table_checksum`` over the stringified refs in table
    order and paste the new hex string in below. Never adjust it just to turn
    a red test green.
    """
    refs = tuple(
        str(param[1]) for _title, _builder, param, _types in catalog._DC_LISTS
    )
    assert len(refs) == 3
    assert _table_checksum(refs) == (
        "dbfa79b6745487ccaf24ffe4715a73eca6f9ac16533ea83427d6bbae5c186703"
    )
    # Params validity, the half a digest cannot see: a positive int where
    # TmdbEntityParams demands one, the "<user>/<slug>" shape where
    # MdblistListParams demands that.
    _title, _builder, (key, tmdb_id), _types = catalog._DC_LISTS[0]
    assert key == "id" and isinstance(tmdb_id, int) and tmdb_id > 0
    for _title, _builder, (key, slug), _types in catalog._DC_LISTS[1:]:
        assert key == "list"
        assert re.fullmatch(r"[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]+", slug)


def test_the_starter_director_transcription():
    directors = catalog.BY_KEY["people_directors"].definitions("Movie")
    assert len(directors) == 6
    assert directors[0].title == "Steven Spielberg (Director)"
    assert directors[0].params == {"id": 488}


def test_the_starter_director_pack_says_the_people_are_not_kometas():
    """The one row in the table that names things Kometa's defaults do not.

    Kometa's four people packs enumerate a library rather than listing names,
    so there is nothing there to transcribe -- and a row that borrowed
    ``defaults/movie/director.yml`` as its provenance would be claiming an
    upstream source for six choices made here."""
    preset = catalog.BY_KEY["people_directors"]

    assert preset.kometa_source.startswith(catalog.NOT_KOMETA)
    assert "ours" in preset.kometa_source
    assert "OUR choice" in preset.description


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


# The roots that can reach the world, in one place because two modules are now
# held to the same rule (task-3 review, Important 2) and a denylist copied per
# module is a denylist that grows in one copy only.
_ROOTS_THAT_REACH_THE_WORLD = {
    "httpx", "requests", "urllib", "socket", "http",
    "plexapi", "pathlib", "os", "io", "open",
    "sqlalchemy", "asyncpg",
}


def _imported_roots(module) -> set[str]:
    """The top-level package name of every import in ``module``'s source, read
    off the AST rather than off ``sys.modules``: an import guarded by
    ``TYPE_CHECKING`` or hidden inside a function still counts, because it is
    still a line somebody can make run."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_catalog_module_imports_nothing_that_could_reach_the_world():
    """The module docstring's central claim, pinned structurally the way
    ``test_collection_filters.py`` pins ``filters.py``'s: read the import list
    rather than trust the docstring to stay true.

    ``_titles_must_not_collide`` runs this expansion on every config write, so
    an import of httpx here is a network call inside config validation -- and
    its failure is a 500 on a settings save."""
    imported_roots = _imported_roots(catalog)

    assert imported_roots.isdisjoint(_ROOTS_THAT_REACH_THE_WORLD), sorted(
        imported_roots
    )

    # ``packs.py`` is the tables ``catalog.py`` draws its dynamic rows from,
    # and was import-free until the location-names phase gave the two location
    # packs one thing to consume: ``iso_names``' alias table, which is what
    # keeps ten TMDb country spellings out of the leftovers bucket. An
    # ALLOW-LIST rather than the count of zero this used to be -- naming the
    # one permitted import is a tighter pin than "none", because it says which
    # one and refuses the next by default. ``iso_names`` is a plain module of
    # vendored data and imports nothing itself.
    packs_tree = ast.parse(pathlib.Path(packs.__file__).read_text(encoding="utf-8"))
    packs_imports = set()
    for node in ast.walk(packs_tree):
        if isinstance(node, ast.Import):
            packs_imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            packs_imports.update(
                "%s.%s" % (node.module, alias.name) for alias in node.names
            )

    assert packs_imports == {"autoposter.collections.iso_names"}, sorted(packs_imports)

    # The property that allow-list DEPENDS on, one hop out (task-3 review,
    # Important 2). Permitting `iso_names` here is only safe while `iso_names`
    # itself reaches nothing: an httpx import THERE is the same network call
    # inside config validation, the same 500 on a settings save, and the
    # allow-list above would stay green straight through it. The denylist is
    # the catalog's own, not a count of zero -- `iso_names` imports nothing
    # today, and a future `dataclasses` there would be nobody's problem.
    iso_roots = _imported_roots(iso_names)

    assert iso_roots.isdisjoint(_ROOTS_THAT_REACH_THE_WORLD), sorted(iso_roots)


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
    """The refusal itself, against a row injected here.

    ``test_every_gated_key_is_refused_at_load_naming_its_row`` above now covers
    every shipped GATED row; this one stays because it pins the refusal to the
    *mechanism* rather than to the table -- it would still fail if the last
    GATED row were ever switched READY, which is exactly when the machinery
    would otherwise stop being tested."""
    monkeypatch.setitem(catalog.BY_KEY, "media_aspect_double", Preset(
        key="media_aspect_double",
        category="media",
        name="Aspect ratio",
        description="One collection per aspect ratio.",
        kometa_source="defaults/both/aspect.yml",
        library_types=("Movie", "Show"),
        readiness=GATED,
        gated_row=155,
    ))

    with pytest.raises(ValidationError, match="155"):
        build_config(_document(["media_aspect_double"]))


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
        "years_title": 'one per ceremony, named "Cannes <year>"',
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


async def test_the_catalog_endpoint_serves_the_groups_in_effective_order(
    client, auth_headers
):
    """The Groups panel's enumeration source (group-order UI phase, C2): the
    server serves the groups so the frontend never transcribes the eleven keys.
    The example config leaves `group_order` unset, so this is canonical."""
    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    assert [entry["key"] for entry in body["groups"]] == [
        "charts", "awards", "content_ratings", "content", "franchises",
        "location", "media", "people", "production", "time", "operator",
    ]
    assert body["groups"][0] == {
        "key": "charts", "title": "Chart Collections",
        "section": "010", "position": 0,
    }
    assert body["groups"][-1] == {
        "key": "operator", "title": "Collections",
        "section": "110", "position": 10,
    }
    # The fence is not a group: it takes no position, and `group_order` cannot
    # name it, so the panel never offers it as a row to move.
    assert all(entry["key"] != "other" for entry in body["groups"])


async def test_the_catalog_endpoint_reports_the_live_group_order(
    client, app, auth_headers
):
    """The same live-config read the active-presets test above proves: a
    swapped `group_order` reorders and renumbers the served array without a
    restart, which is what makes the panel's re-read after save truthful."""
    config = app.state.config_holder.current
    app.state.config_holder.swap(config.model_copy(
        update={
            "collections": config.collections.model_copy(
                update={"group_order": ["operator"]}
            )
        }
    ))

    body = (await client.get("/api/collections/catalog", headers=auth_headers)).json()

    assert body["groups"][0]["key"] == "operator"
    assert body["groups"][0]["section"] == "010"
    assert body["groups"][1]["key"] == "charts"
    assert [entry["position"] for entry in body["groups"]] == list(range(11))


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


def test_the_presets_9b_readjudicated_carry_their_evidence_into_the_pack():
    """Phase 9b proved the DATA path for three presets and none of the shapes;
    10a shipped the enumerator; 10b ships the packs. What must not be lost in
    that sequence is the evidence -- each of these rows names the live probe
    that measured its value count, so the next reader does not re-run it.

    ``media_audio_language`` ships here; ``production_network`` and
    ``media_subtitle_language`` ship in the task after this one. The assertion
    is written over whichever of them is READY, so it holds in both states
    rather than needing an edit between two commits of one phase.
    """
    for key in ("production_network", "media_audio_language",
                "media_subtitle_language"):
        preset = catalog.BY_KEY[key]
        assert "live probe" in preset.description, key
        assert "enumerat" in preset.description, key
        if preset.readiness == GATED:
            assert preset.gated_row == catalog.DYNAMIC_ENGINE_ROW, key

    # The client-side strand is still named -- it is why these cannot be built
    # out of a library walk -- but it was never what they waited on.
    assert "row %d" % catalog.STRANDED_FILTER_ROW in catalog.BY_KEY[
        "media_audio_language"
    ].description


def test_the_catalog_has_a_franchises_category_and_the_preset_moved():
    from autoposter.collections.catalog import BY_KEY, CATEGORIES

    assert CATEGORIES["franchises"] == "Franchises"
    assert list(CATEGORIES) == sorted(CATEGORIES)  # the declaration stays alphabetical
    assert BY_KEY["content_franchises"].category == "franchises"
