"""``facts_family``: one collection per value the STORED FACTS hold.

The third family shape. ``cs_bucket`` manages a family of SMART collections
from a static table; ``dynamic`` manages a family of SMART collections from a
Plex enumeration; this manages a family of LIST collections from a database
enumeration -- because the values are TMDb's and Plex holds none of them
(roadmap rows 189/192).

Everything about WHICH keys become collections and what each is CALLED is phase
10a's, unchanged and untouched: ``dynamic_keys.derive_keys`` takes the
enumeration as data, and ``dynamic_titles.family_titles`` takes what it
returns. Neither has ever known where the values came from, which is exactly
what makes this family shape cheap.
"""
import httpx
import pytest
from sqlalchemy import func, select, text

from autoposter.collections import iso_names
from autoposter.collections.builders import facts_family as facts_family_module
from autoposter.collections.builders.base import BuilderContext
from autoposter.collections.builders.facts_family import (
    FactsFamilyBuilder,
    FactsFamilyParams,
    family_label,
    generated_titles,
)
from autoposter.collections.builders.sources_bundle import SourceClients
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ItemFacts, MediaItem
from autoposter.providers.tmdb_lists import TmdbListClient

from conftest import seed_media_item


async def _item(session, rating_key, *, library="Movies", kind="movie", title="X",
                **facts):
    item = await seed_media_item(session, rating_key, library=library, kind=kind, title=title)
    if facts:
        session.add(ItemFacts(item_id=item.id, **facts))
        await session.flush()
    return item


def _definition(**kwargs):
    base = dict(
        title="Countries of origin", builder="facts_family",
        params={"type": "origin_country"},
    )
    base.update(kwargs)
    return CollectionDefinition(**base)


def _ctx(session, definition, **kwargs):
    base = dict(
        library="Movies", library_type="Movie", session=session,
        definition=definition, config=definition.params, run_cache={},
    )
    base.update(kwargs)
    return BuilderContext(**base)


def _tmdb(handler):
    """A context manager yielding a ``SourceClients`` whose TMDb client answers
    ``handler`` -- the two franchise tests' only difference is the response."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_one_unit_per_enumerated_value(session):
    """Most-populated first, which is ``enumerate_values``' order and therefore
    the order the family's collections are created in. Titles and keys are
    TMDb's English names since row 196 closed; membership still queries ISO
    codes."""
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    await _item(session, "3", tmdb_origin_country=["US"])
    definition = _definition()
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == [
        iso_names.COUNTRY_NAMES["US"], iso_names.COUNTRY_NAMES["FI"],
    ]
    assert {unit.builder for unit in units} == {"facts_value"}
    # The KEYS are display names now (row 196's normalisation decision), and
    # the membership folds back DOWN to the codes the database stores.
    assert units[0].params == {"field": "origin_country", "values": ["US"]}


async def test_addons_merge_exactly_as_they_do_for_a_smart_family(session):
    """``derive_keys`` is phase 10a's, unchanged: an addon key the library does
    not itself hold becomes a synthetic bucket over the members it does hold,
    which is what makes ``region.yml``'s 'Nordic' a collection at all."""
    await _item(session, "1", tmdb_origin_country=["FI"])
    await _item(session, "2", tmdb_origin_country=["SE"])
    definition = _definition(params={
        "type": "origin_country",
        "include": ["Nordic"],
        "addons": {"Nordic": [
            iso_names.COUNTRY_NAMES["FI"], iso_names.COUNTRY_NAMES["SE"],
            iso_names.COUNTRY_NAMES["NO"],
        ]},
    })
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == ["Nordic"]
    # NO's name is not in the library, so it is not asked for; the two that
    # are fold back to their codes for the facts_value query.
    assert units[0].params["values"] == ["FI", "SE"]


async def test_a_code_the_table_cannot_name_keys_as_itself(session):
    """Never invents: a code with no vendored entry keys, titles and queries
    as the bare code -- visible in the collection list rather than hidden."""
    assert "XX" not in iso_names.COUNTRY_NAMES  # a precondition, not a recall
    await _item(session, "1", tmdb_origin_country=["XX"])
    units = await FactsFamilyBuilder().expand(_ctx(session, _definition()))
    assert [unit.title for unit in units] == ["XX"]
    assert units[0].params["values"] == ["XX"]


async def test_two_codes_that_share_one_name_key_once_and_query_both(session):
    """The join record's D5, end to end. TMDb gives both `CD` and `CG` the
    `english_name` `Congo`, so the family builds ONE collection whose
    membership folds back to BOTH codes: `country_codes` answers a tuple, and
    `derive_keys` keys on the name, so nothing is dropped and no duplicate
    title refuses the family. The forward direction stays lossy -- one key for
    two countries -- which `iso_names`' docstring records."""
    assert iso_names.country_codes(iso_names.COUNTRY_NAMES["CD"]) == ("CD", "CG")
    await _item(session, "1", tmdb_origin_country=["CD"])
    await _item(session, "2", tmdb_origin_country=["CG"])
    units = await FactsFamilyBuilder().expand(_ctx(session, _definition()))
    assert [unit.title for unit in units] == [iso_names.COUNTRY_NAMES["CD"]]
    assert units[0].params["values"] == ["CD", "CG"]


async def test_a_language_family_titles_from_the_vendored_table(session):
    """Row 190: only the TITLE renders through the table -- the key stays the
    code (row 156's law), so the membership query and every narrowing knob
    keep speaking ISO."""
    await _item(session, "1", tmdb_original_language="en")
    await _item(session, "2", tmdb_original_language="fi")
    await _item(session, "3", tmdb_original_language="en")
    definition = _definition(
        title="Original languages", params={"type": "original_language"},
    )
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == [
        iso_names.LANGUAGE_NAMES["en"], iso_names.LANGUAGE_NAMES["fi"],
    ]
    assert units[0].params == {"field": "original_language", "values": ["en"]}


async def test_two_languages_that_share_one_name_both_build_and_neither_refuses(session):
    """The Congo hazard's mirror, on the seam where the collapse does not save
    it. TMDb gives `nr` and `nd` one `english_name`; `names="titles"` keys on
    the CODE, so both reach `family_titles` and would claim the title
    `Ndebele` -- a `DuplicateFamilyTitle` the builder turns into `return []`
    for EVERY collection in the family. `language_title` appends the code, so
    both build, the keys and the membership queries stay ISO, and the family
    survives."""
    assert iso_names.LANGUAGE_NAMES["nr"] == iso_names.LANGUAGE_NAMES["nd"]
    await _item(session, "1", tmdb_original_language="nr")
    await _item(session, "2", tmdb_original_language="nd")
    await _item(session, "3", tmdb_original_language="nr")
    definition = _definition(
        title="Original languages", params={"type": "original_language"},
    )
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == ["Ndebele (nr)", "Ndebele (nd)"]
    assert units[0].params == {"field": "original_language", "values": ["nr"]}
    assert units[1].params == {"field": "original_language", "values": ["nd"]}


async def test_a_language_familys_narrowing_still_speaks_the_code(session):
    await _item(session, "1", tmdb_original_language="en")
    await _item(session, "2", tmdb_original_language="fi")
    definition = _definition(
        title="Original languages",
        params={"type": "original_language", "exclude": ["en"]},
    )
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert [unit.title for unit in units] == [iso_names.LANGUAGE_NAMES["fi"]]


async def test_a_franchise_family_delegates_membership_to_the_shipped_builder(session):
    """The franchise family's members are the FRANCHISE's parts, not the items
    this service happens to have fetched -- ``tmdb_collection`` shipped in 8b
    and reads ``/collection/{id}``'s own ``parts``. The enumeration says WHICH
    franchises the library is in; the builder says what is in each."""
    await _item(session, "1", tmdb_collection_id=1241)

    def handler(request):
        return httpx.Response(200, json={
            "id": 1241, "name": "Harry Potter Collection", "parts": [{"id": 671}],
        })

    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    async with _tmdb(handler) as http:
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        units = await FactsFamilyBuilder().expand(
            _ctx(session, definition, sources=sources, http=http)
        )

    # TMDb's own name, verbatim: the " Collection" strip the franchise PACK
    # asks for is `remove_suffix`, a param, not something this row does behind
    # an operator's back.
    assert [unit.title for unit in units] == ["Harry Potter Collection"]
    assert units[0].builder == "tmdb_collection"
    assert units[0].params == {"id": 1241}


async def test_the_franchise_suffix_strip_is_phase_10as_and_still_works(session):
    """The pack's ``remove_suffix: [Collection]`` is ``dynamic_titles``' own
    machinery, reached through a family whose values came from a database."""
    await _item(session, "1", tmdb_collection_id=1241)

    def handler(request):
        return httpx.Response(200, json={
            "id": 1241, "name": "Harry Potter Collection", "parts": [{"id": 671}],
        })

    definition = _definition(title="Franchises", params={
        "type": "tmdb_collection", "remove_suffix": ["Collection"],
    })
    async with _tmdb(handler) as http:
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        units = await FactsFamilyBuilder().expand(
            _ctx(session, definition, sources=sources, http=http)
        )

    assert [unit.title for unit in units] == ["Harry Potter"]


async def test_a_franchise_tmdb_cannot_name_is_dropped_and_reported(session):
    """A franchise whose name will not resolve has no title, and a collection
    titled from an id is nobody's ask. Dropped, and named in the report -- the
    silent variant is what an operator cannot see."""
    await _item(session, "1", tmdb_collection_id=1241)

    def handler(request):
        return httpx.Response(404, json={})

    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    async with _tmdb(handler) as http:
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        ctx = _ctx(session, definition, sources=sources, http=http)
        units = await FactsFamilyBuilder().expand(ctx)

    assert units == []
    assert any("1241" in note for note in ctx.run_cache.get("facts_family:notes", []))


async def test_a_franchise_family_with_no_tmdb_client_builds_nothing(session):
    """Absent means None and None means say so: a family that cannot name a
    single collection must not quietly build none."""
    await _item(session, "1", tmdb_collection_id=1241)
    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    ctx = _ctx(session, definition)
    assert await FactsFamilyBuilder().expand(ctx) == []
    assert generated_titles(ctx.run_cache, definition) is None
    assert any(
        "TMDb" in note for note in ctx.run_cache.get("facts_family:notes", [])
    )


async def test_a_franchise_familys_leftovers_bucket_builds_one_of_its_ids(session):
    """The ``other`` bucket on a FRANCHISE family, which is the one shape the
    task that shipped this builder could not reach (its T3b review's minor 7).

    It matters because the leftovers bucket is the one unit whose ``values``
    are not a single enumerated key: they are every key ``include`` left over,
    and ``tmdb_collection`` takes ONE id. So the bucket is well-defined -- the
    leftover keys are ids, ``int(unit.values[0])`` is a real franchise -- and it
    is also lossy in exactly the way a merged addon bucket is, which is why the
    same report line has to fire for it. A bucket that silently built one of
    three franchises under a name promising all the others is the failure this
    pins.

    No shipped pack reaches this: ``packs.FRANCHISE_PARAMS`` carries no
    ``include``, and ``other_name`` is gated on one (upstream gates it the same
    way). It is exercised here rather than left to a future pack to discover.
    """
    for rating_key, collection_id in (("1", 1241), ("2", 2806), ("3", 8091)):
        await _item(session, rating_key, tmdb_collection_id=collection_id)

    names = {1241: "Harry Potter Collection", 2806: "American Pie Collection",
             8091: "Alien Collection"}

    def handler(request):
        collection_id = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={
            "id": collection_id, "name": names[collection_id], "parts": [],
        })

    definition = _definition(title="Franchises", params={
        "type": "tmdb_collection", "remove_suffix": ["Collection"],
        "include": ["1241"], "other_name": "Other Franchises",
    })
    async with _tmdb(handler) as http:
        sources = SourceClients(tmdb=TmdbListClient("tok", http))
        ctx = _ctx(session, definition, sources=sources, http=http)
        units = await FactsFamilyBuilder().expand(ctx)

    assert [unit.title for unit in units] == ["Harry Potter", "Other Franchises"]
    # The leftovers bucket's own id, and an ``int`` -- the param the shipped
    # ``tmdb_collection`` builder takes. 2806 rather than 8091 because
    # ``enumerate_values`` orders ties by the value ascending and the column is
    # an Integer, so the leftovers keep the family's own order.
    assert units[1].params == {"id": 2806}
    notes = ctx.run_cache.get("facts_family:notes", [])
    assert any(
        "Other Franchises" in note and "8091" in note for note in notes
    ), notes


async def test_an_empty_enumeration_builds_nothing_and_protects_everything(session):
    """The fail-closed state ``generated_titles`` documents: a family that did
    not enumerate has NOT narrowed, and the sweep must not treat its
    collections as candidates. ``None``, not ``set()``."""
    definition = _definition()
    ctx = _ctx(session, definition)
    units = await FactsFamilyBuilder().expand(ctx)
    assert units == []
    assert generated_titles(ctx.run_cache, definition) is None


async def test_a_family_that_did_enumerate_records_every_title_it_derived(session):
    await _item(session, "1", tmdb_origin_country=["US"])
    definition = _definition()
    ctx = _ctx(session, definition)
    await FactsFamilyBuilder().expand(ctx)
    assert generated_titles(ctx.run_cache, definition) == {
        iso_names.COUNTRY_NAMES["US"],
    }


async def test_every_unit_carries_the_family_label_and_the_operators_own(session):
    """The sweep's handle. ``_family_state`` finds a member by the family
    label, and the operator's own labels must survive the expansion -- the
    engine fills ``labels`` from the placeholder only when the expander set
    none (``engine._completed``), so an expander that sets it has to carry both.
    """
    await _item(session, "1", tmdb_origin_country=["US"])
    definition = _definition(labels=["mine"])
    units = await FactsFamilyBuilder().expand(_ctx(session, definition))
    assert units[0].labels == ["mine", family_label(definition)]


async def test_an_over_cap_family_creates_nothing_and_says_both_numbers(session):
    """``max_collections``, the same refusal ``builders/dynamic.py`` makes and
    for the same reason: 300 collections an operator then deletes one at a time
    is worse than a refusal that names the way out."""
    for index in range(5):
        await _item(session, str(index), tmdb_origin_country=["C%d" % index])
    definition = _definition(params={"type": "origin_country", "max_collections": 3})
    ctx = _ctx(session, definition)
    units = await FactsFamilyBuilder().expand(ctx)
    assert units == []
    assert generated_titles(ctx.run_cache, definition) is None
    note = " ".join(ctx.run_cache.get("facts_family:notes", []))
    assert "5" in note and "3" in note


async def test_the_over_cap_refusal_reaches_the_logs_page(session, caplog):
    """Roadmap row 223's real finding. This module had NO logger at all, so the
    franchise family's cap refusal existed only as a string in a run report --
    invisible on the logs page, which is where an operator looks when a family
    stops updating. ``builders/dynamic._refused`` has warned since it shipped;
    this is the same line, on the same branch, in the sibling builder that
    actually hit the incident. ONE record, naming the library and the
    definition, and the reported string is byte-for-byte the one it replaced.
    """
    import logging

    for index in range(5):
        await _item(session, str(index), tmdb_origin_country=["C%d" % index])
    definition = _definition(params={"type": "origin_country", "max_collections": 3})
    ctx = _ctx(session, definition)

    with caplog.at_level(logging.WARNING):
        units = await FactsFamilyBuilder().expand(ctx)

    assert units == []
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "autoposter.collections.builders.facts_family"
    ]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    message = warnings[0].getMessage()
    # The reason string already embeds the library (``%r`` of ``ctx.library``
    # inside ``why``), so a bare "Movies" in message` substring check passes
    # even if ``ctx.library`` were dropped from the outer ``%s`` in
    # ``logger.warning("%s: %r was not built: %s", ...)``. Only the record's
    # own shape -- library, then ``: ``, then the quoted title -- pins that
    # argument.
    assert message.startswith("Movies: 'Countries of origin' was not built:")
    assert "`max_collections` is 3" in message
    note = " ".join(ctx.run_cache.get("facts_family:notes", []))
    assert note == (
        "'Countries of origin' built nothing: this would create 5 collections "
        "in 'Movies' -- 'origin_country' reports 5 value(s) there, which "
        "`include:`, `exclude:` and `addons:` narrow to that many buckets -- "
        "and `max_collections` is 3. Narrow the family, or raise "
        "`max_collections` past 5 if that is really what you want"
    )


async def test_a_library_type_the_field_cannot_carry_is_refused_before_any_query(session):
    """A movie-only family on a show library costs zero database work and says
    why -- ``require_library_type``, above the enumeration deliberately."""
    from autoposter.collections.builders.base import LibraryTypeMismatch

    definition = _definition(title="Franchises", params={"type": "tmdb_collection"})
    with pytest.raises(LibraryTypeMismatch):
        await FactsFamilyBuilder().expand(
            _ctx(session, definition, library="TV", library_type="Show")
        )


async def test_no_session_raises_rather_than_building_nothing():
    """Same rule ``facts_value`` keeps one layer down: a context with no
    database must not look like a library with no values."""
    definition = _definition()
    with pytest.raises(ValueError):
        await FactsFamilyBuilder().expand(BuilderContext(
            library="Movies", library_type="Movie",
            definition=definition, config=definition.params,
        ))


async def test_a_failing_enumeration_leaves_the_session_usable_for_what_runs_next(
    session, monkeypatch,
):
    """``engine.py:379-391`` contains an expansion failure and carries on with
    the next definition, on the invariant that a dead source does not stop the
    pass. That invariant is false unless a failed read here leaves the shared
    ``AsyncSession``'s transaction usable -- the same savepoint
    ``facts_value.build`` takes, for the same reason."""
    async def _broken_query(*args, **kwargs):
        await session.execute(text("SELECT * FROM this_table_does_not_exist"))
        return []  # pragma: no cover - the execute above always raises first

    monkeypatch.setattr(facts_family_module, "enumerate_values", _broken_query)

    definition = _definition()
    with pytest.raises(Exception):
        await FactsFamilyBuilder().expand(_ctx(session, definition))

    result = await session.execute(select(func.count()).select_from(MediaItem))
    assert result.scalar_one() == 0


async def test_this_builder_produces_no_membership_of_its_own(session):
    """A registry entry is either a ``Builder`` or a ``SmartBuilder`` and this
    one is the former by protocol only: every unit it expands into names a
    DIFFERENT builder, so ``engine._run_one`` never dispatches back here. It
    raises rather than answering with an empty membership, which one layer down
    means "make no changes"."""
    from autoposter.collections.builders.base import REGISTRY

    definition = _definition()
    with pytest.raises(ValueError, match="expand"):
        await REGISTRY["facts_family"].build(_ctx(session, definition))


async def test_a_type_this_service_does_not_enumerate_is_refused_at_config_load():
    with pytest.raises(ValueError) as caught:
        FactsFamilyParams.model_validate({"type": "actor"})
    assert "origin_country" in str(caught.value)


def test_the_family_label_is_prefixed_and_not_the_bare_title():
    """A family called 'Countries' must not claim a plain ``Countries`` label
    an operator may already use -- ``builders/dynamic.py``'s reasoning, one
    prefix along, and the two prefixes must differ so the two sweeps cannot
    enumerate each other's members."""
    from autoposter.collections.builders.dynamic import (
        FAMILY_LABEL_PREFIX as DYNAMIC_PREFIX,
    )
    from autoposter.collections.builders.facts_family import FAMILY_LABEL_PREFIX

    assert FAMILY_LABEL_PREFIX != DYNAMIC_PREFIX
    assert family_label(_definition()) == FAMILY_LABEL_PREFIX + "Countries of origin"


def _generated_key_for(definition):
    from autoposter.collections.builders.facts_family import (
        _generated_key,
        family_label as _label,
    )
    return _generated_key(_label(definition))


def test_the_engine_finds_this_familys_label_through_the_registry():
    """FLAG 2, as a test: ``_family_state`` was written for smart builders and
    reads the protocol off the registry entry with ``getattr``. A list family
    that did not join it would have its collections deleted on the next pass --
    they carry the ownership label and a managed row, and their titles are not
    in ``definition_titles_for``'s set."""
    from autoposter.collections.engine import _family_state

    definition = _definition()
    labels, generated = _family_state([definition], "Movies", {})
    assert labels == {family_label(definition): definition.title}
    assert generated == {}, "a family that has not run protects everything"

    ran = {_generated_key_for(definition): {"US"}}
    labels, generated = _family_state([definition], "Movies", ran)
    assert generated[family_label(definition)] == {"US"}


def test_the_ten_dynamic_types_are_still_exactly_ten():
    """Global constraint 2, as a test rather than as a promise. This phase adds
    THREE family types and none of them is a ``DYNAMIC_TYPES`` row: that table's
    only consumer is a smart builder that cannot build a list family, and a row
    it cannot build would be advertised to operators by
    ``DynamicParams``' own error message."""
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    assert len(DYNAMIC_TYPES) == 10
    assert "origin_country" not in DYNAMIC_TYPES
    assert "original_language" not in DYNAMIC_TYPES
    assert "tmdb_collection" not in DYNAMIC_TYPES


async def test_a_contested_unit_is_skipped_and_reported(session):
    """The precedence rule. A curated definition elsewhere in this config
    already manages 'Fast & Furious' (``catalog._UNIVERSE_LISTS``), and the
    enumeration finds the same franchise through TMDb. Curated wins: the
    family builds the rest and names what it left alone, the same way it
    already names an over-cap fan-out or a franchise TMDb cannot title."""
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    definition = _definition(
        title="Regions", params={"type": "origin_country"},
    )
    ctx = _ctx(
        session, definition,
        managed_titles=frozenset({iso_names.COUNTRY_NAMES["US"]}),
    )
    units = await FactsFamilyBuilder().expand(ctx)

    # The contested unit is not built...
    assert [unit.title for unit in units] == [iso_names.COUNTRY_NAMES["FI"]]
    # ...and the pass report says which one, and who kept it.
    reported = "\n".join(facts_family_module.notes(ctx.run_cache))
    assert iso_names.COUNTRY_NAMES["US"] in reported
    assert "already managed by another definition" in reported


async def test_the_contested_title_stays_the_curated_definitions_alone(session):
    """The other half of the law, asserted through the only two handles by
    which this family could reach that Plex collection: a returned unit (which
    would drive ``engine._run_one`` and rewrite its membership) and the delete
    sweep's ``generated`` record (which would let ``_sweep`` delete it as a
    narrowed family's leftover). Neither names the contested title."""
    await _item(session, "1", tmdb_origin_country=["US"])
    await _item(session, "2", tmdb_origin_country=["FI"])
    definition = _definition(
        title="Regions", params={"type": "origin_country"},
    )
    contested = iso_names.COUNTRY_NAMES["US"]
    managed = frozenset({contested})
    ctx = _ctx(session, definition, managed_titles=managed)
    units = await FactsFamilyBuilder().expand(ctx)

    assert contested not in {unit.title for unit in units}
    assert contested not in generated_titles(ctx.run_cache, definition)
    assert generated_titles(ctx.run_cache, definition) == {
        iso_names.COUNTRY_NAMES["FI"]
    }
    # Read, never edited: the rest of the config is not this builder's to touch.
    assert ctx.managed_titles == managed


async def test_two_enumerated_families_in_one_pass_do_not_contest_each_other(
    session,
):
    """The location latent contest, guarded. ``Regions`` and ``Continents``
    are both ``facts_family`` and both title with the bare name, so the same
    country name can be a bucket in each -- and neither is visible to
    ``engine.definition_titles`` (a family contributes only its placeholder
    title). The pass's own ``generated`` records are the ledger: whichever
    family runs first keeps the title, the second stands down and reports.
    """
    await _item(session, "1", tmdb_origin_country=["US"])
    first = _definition(title="Regions", params={"type": "origin_country"})
    second = _definition(title="Continents", params={"type": "origin_country"})
    run_cache: dict = {}

    first_units = await FactsFamilyBuilder().expand(
        _ctx(session, first, run_cache=run_cache)
    )
    second_units = await FactsFamilyBuilder().expand(
        _ctx(session, second, run_cache=run_cache)
    )

    assert [unit.title for unit in first_units] == [iso_names.COUNTRY_NAMES["US"]]
    assert second_units == []
    reported = "\n".join(facts_family_module.notes(run_cache))
    assert iso_names.COUNTRY_NAMES["US"] in reported
    # Fail-closed: the second family never decided, so the sweep must not read
    # its empty output as "the operator narrowed it" and delete the first
    # family's collection.
    assert generated_titles(run_cache, second) is None


def test_the_engine_computes_the_contested_set_from_the_definitions_it_has():
    """The wiring, at the seam. ``definition_titles_for`` is the set the delete
    sweep and the leftovers report already share; a curated preset's own
    collections are in it by their own titles (``engine.py:1355``), which is
    exactly what makes the franchise contest visible. The empty ``collections``
    argument is deliberate -- see the call site's comment."""
    from types import SimpleNamespace

    from autoposter.collections.engine import definition_titles_for
    from autoposter.config.schema import CollectionsConfig

    curated = CollectionDefinition(
        title="Fast & Furious", builder="imdb_list",
        params={"list": "ls4102351575"},
    )
    placeholder = CollectionDefinition(
        title="Franchises", builder="facts_family",
        params={"type": "tmdb_collection"},
    )
    config = SimpleNamespace(collections=CollectionsConfig())
    titles = definition_titles_for(
        [curated, placeholder], [], "Movies", "Movie", config,
    )
    assert "Fast & Furious" in titles
    assert "Franchises" in titles
