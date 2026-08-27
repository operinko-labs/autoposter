"""``dynamic``: one smart collection per distinct value the library holds.

The engine ``collections/buckets.py`` is one hardcoded instance of. It
enumerates through ``LibraryTagResolver.choices``, derives its keys and titles
with the two pure modules beside it, and writes each collection through the SAME
reconciler ``smart_filter`` uses -- so there is one write path, one query
grammar and one drift hash for every smart collection this service manages
(10a decision C1).
"""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.builders.dynamic import (
    DynamicBuilder,
    DynamicParams,
    family_label,
)
from autoposter.collections.filters import parse_filters
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, key, title):
        self.key = key
        self.title = title


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    def __init__(self):
        self.queries = []
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://abc123/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self.smart = True
        self.summary = None
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.labels_added = []
        self._server = FakeServer()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self._labels = self._real_labels
        self._fields = self._real_fields

    def addLabel(self, label, locked=True):
        self.labels_added.append(label)
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    """Answers ``listFilterChoices`` with one vocabulary and every search with
    three items, which is all a family needs to be created."""

    def __init__(self, choices=None, section_type="movie"):
        self.key = SECTION_KEY
        self.type = section_type
        self._server = FakeServer()
        self._existing = {}
        self._choices = choices if choices is not None else [
            FakeChoice("1138", "Horror"), FakeChoice("9", "Drama"),
        ]
        self.choice_calls = []
        self.fetched = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        self.fetched.append(path)
        return [FakeItem("1"), FakeItem("2"), FakeItem("3")]

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        return list(self._choices)


def _config(**overrides):
    options = {
        "adopt": False, "adopt_from": [], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "separators": False,
        "ownership_label": LABEL,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _ctx(session, section, definition, *, dry_run=False, library_type="Movie",
         config=None, listing=None):
    return SmartContext(
        session=session, section=section, library="Movies",
        library_type=library_type, label=LABEL, config=config or _config(),
        http=None, dry_run=dry_run, definition=definition, run_cache={},
        listing=listing,
    )


def _definition(**overrides):
    options = {
        "title": "Genres",
        "builder": "dynamic",
        "params": {"type": "genre"},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


# --- registration and the protocol -------------------------------------------


def test_the_builder_is_registered_as_a_smart_builder():
    builder = REGISTRY["dynamic"]
    assert builder.smart is True
    assert isinstance(builder, DynamicBuilder)
    assert builder.params_model is DynamicParams
    assert not hasattr(builder, "titles"), (
        "a dynamic family's titles are the LIBRARY's -- enumerating them "
        "offline is impossible and enumerating them online would put a Plex "
        "call inside _titles_must_not_collide, which runs on every config write"
    )


def test_the_builder_offers_the_sweep_its_generated_record_by_protocol():
    """The engine asks the REGISTRY entry rather than importing this module --
    the same shape ``family_label`` established, so a future family builder can
    join the sweep by growing two methods and nothing in the engine changes."""
    builder = REGISTRY["dynamic"]
    assert callable(getattr(builder, "family_label", None))
    assert callable(getattr(builder, "generated_titles", None))
    assert builder.generated_titles({}, _definition()) is None


# --- the params, at config load ----------------------------------------------


def test_an_unknown_type_refuses_at_load_and_lists_the_ones_that_exist():
    with pytest.raises(ValueError) as refusal:
        _definition(params={"type": "edition"})
    assert "edition" in str(refusal.value)
    assert "genre" in str(refusal.value)


@pytest.mark.parametrize("key", ["test", "data", "sync", "other_template",
                                 "template", "template_variables"])
def test_the_dead_upstream_knobs_refuse_by_name_with_the_reason(key):
    """C5, plus the two this phase cannot mean anything by. Every one of them
    is a key Kometa accepts, so an operator porting a config meets it -- and
    accepting it silently is the failure the refusal table exists to prevent."""
    with pytest.raises(ValueError) as refusal:
        _definition(params={"type": "genre", key: True})
    assert key in str(refusal.value)


def test_a_title_format_that_names_no_key_refuses_at_load():
    """Upstream reverts it to the default and logs (meta.py:1234-1236), which
    builds the family under names the operator did not write."""
    with pytest.raises(ValueError, match="key_name"):
        _definition(params={"type": "genre", "title_format": "Great <<library_type>>s"})


def test_two_key_name_overrides_with_the_same_value_refuse_at_load():
    """Upstream pops a key while iterating (meta.py:1251-1257), which is a
    RuntimeError in CPython 3 rather than the graceful skip it reads as."""
    with pytest.raises(ValueError) as refusal:
        _definition(params={
            "type": "genre",
            "key_name_override": {"Sci-Fi": "Science Fiction", "SciFi": "Science Fiction"},
        })
    assert "Science Fiction" in str(refusal.value)


def test_an_unknown_sort_refuses_at_load():
    with pytest.raises(ValueError, match="not a Plex sort"):
        _definition(params={"type": "genre", "sort_by": ["nonsense.desc"]})


def test_a_non_mapping_addons_refuses_at_load():
    """``dynamic_keys._dictliststr`` raises a bare ``TypeError`` on a
    non-mapping, which would reach the engine as a crash rather than as
    something about this operator's config. The params model is what makes that
    branch unreachable from YAML (T3 review carry)."""
    with pytest.raises(ValueError, match="addons"):
        _definition(params={"type": "genre", "addons": ["1980s"]})

    # ...and the shape that DOES load is the shape the pure module accepts.
    assert DynamicParams.model_validate(
        {"type": "genre", "addons": {"Horror": ["Slasher"]}}
    ).addons == {"Horror": ["Slasher"]}


def test_an_empty_title_override_refuses_at_load():
    """``family_titles`` honours an override by MEMBERSHIP, not truthiness
    (meta.py:1382-1383), so an empty string is faithfully a collection with no
    name at all. Upstream would create it; here the operator hears about it at
    the moment of the edit."""
    with pytest.raises(ValueError, match="title_override"):
        _definition(params={"type": "genre", "title_override": {"Horror": ""}})


@pytest.mark.parametrize("params, token", [
    ({"type": "genre", "title_format": "<<key_name>> <<limit>>"}, "<<limit>>"),
    ({"type": "genre", "title_format": "Top <<key_name>> <<genre>>s"}, "<<genre>>"),
    ({"type": "genre", "include": ["Horror"], "other_name": "Other <<key_name>>"},
     "<<key_name>>"),
    ({"type": "genre", "title_override": {"Horror": "<<key_name>> Films"}},
     "<<key_name>>"),
])
def test_a_token_nothing_resolves_refuses_at_load_and_names_it(params, token):
    """Upstream resolves two more token families than this service can -- a
    template's ``default:`` values (meta.py:1406-1410) and the library-level
    ``<<limit>>`` (:1272-1273) -- and there is no template system here to
    resolve them from. An unresolved token is not a no-op: it ships literally
    into a live collection's name. ``<<genre>>`` is upstream's own
    ``<<{auto_type}>>``, which renders a raw Python list repr (``['Horror']``)
    even where it IS resolved, which is an argument for refusing rather than
    widening."""
    with pytest.raises(ValueError) as refusal:
        _definition(params=params)
    assert token in str(refusal.value)


def test_the_definition_fields_a_family_cannot_apply_refuse_at_load():
    """Every one of the seven ``cs_bucket`` refuses, for the same reasons: this
    definition names a FAMILY, and Plex owns each member's membership."""
    for field, value in [
        ("summary", "one summary"), ("sort", "alpha"), ("limit", 5),
        ("sync_mode", "append"), ("item_label", ["x"]), ("tmdb_summary", 10),
        ("filters", {"year.gte": 2000}),
    ]:
        with pytest.raises(ValueError, match=field):
            _definition(**{field: value})


# --- what it builds -----------------------------------------------------------


async def test_it_creates_one_smart_collection_per_enumerated_value(session):
    section = FakeSection()
    definition = _definition()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert sorted(section._existing) == ["Top Drama movies", "Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)
    # One listFilterChoices for the whole family, not one per key.
    assert section.choice_calls == [("genre", "movie")]


async def test_each_collection_asks_plex_for_its_own_value_under_an_any_base(session):
    """The emitted query is the 9b grammar and the base is ``any:`` -- which is
    upstream's (meta.py:950) and is what makes a bucket's several values an OR
    rather than the ``all:`` AND that would match nothing (10a decision C2)."""
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert any("genre=1138" in one for one in section.fetched)
    assert any("genre=9" in one for one in section.fetched)
    assert all("push=1" in one and "pop=1" in one for one in section.fetched)
    assert not any("and=1" in one for one in section.fetched), (
        "an all: base would AND a bucket's values, which matches nothing"
    )


async def test_one_buckets_several_values_reach_plex_as_one_ord_query(session):
    """Decision C2, byte for byte. The test above pins the ``push=1``/``pop=1``
    envelope, but every key in it holds exactly ONE value, so it cannot see the
    OR itself -- and the OR is the whole decision: row 182's probe answered 0
    for the ``all:`` spelling and 442 for this one. An ``addons``-merged key is
    the shape that carries several, and its three terms have to arrive in ONE
    query joined by ``or=1``, not as three queries or as an AND."""
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("9", "Drama"),
        FakeChoice("77", "Thriller"),
    ])
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={"type": "genre", "addons": {"Horror": ["Drama", "Thriller"]}},
    )))

    # One collection, because the two addon members are merged INTO Horror
    # rather than getting collections of their own -- so one query, whole.
    assert list(section._existing) == ["Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)
    assert section.fetched == [
        "/library/sections/2/all"
        "?type=1&limit=50&sort=rating%3Adesc"
        "&push=1&genre=1138&or=1&genre=9&or=1&genre=77&pop=1"
    ]


async def test_a_decade_family_emits_the_bare_form_and_queries_the_key(session):
    """Amendment 1's route, guarded. ``decade`` is the type that proves routing
    through ``parse_filters(..., searching=True)`` rather than assembling the
    query by hand: its search vocabulary is the BARE form alone (Kometa's
    ``no_not_mods``, plex.py:593 and :597-599), which no ``genre`` test can see
    because tag parsing is identical on both vocabularies. It also pins
    ``dynamic_types``' key/title split at the emitter -- ``1980`` is queried and
    ``1980s`` is titled."""
    section = FakeSection(choices=[
        FakeChoice("1980", "1980s"), FakeChoice("1990", "1990s"),
    ])
    await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        title="Decades", params={"type": "decade"},
    )))

    assert sorted(section._existing) == [
        "Best movies of the 1980s", "Best movies of the 1990s",
    ]
    assert section.choice_calls == [("decade", "movie")]
    assert section.fetched[0] == (
        "/library/sections/2/all"
        "?type=1&limit=50&sort=rating%3Adesc&push=1&decade=1980&pop=1"
    )


def test_the_route_this_builder_emits_through_refuses_a_decade_operator_form():
    """The other half of the same amendment: the reason the builder has no gate
    of its own is that the one it routes through already holds. If this ever
    stops raising, ``decade.gte`` becomes a query Kometa refuses to build and no
    oracle config can cover -- so the subtraction is asserted at the exact call
    ``DynamicBuilder.apply`` makes, arguments and all."""
    with pytest.raises(ValueError) as refusal:
        parse_filters(
            {"decade.gte": "1980"}, field="params", searching=True, base="any",
        )
    assert "decade" in str(refusal.value)
    assert "no modifier at all" in str(refusal.value)


async def test_a_family_labels_every_collection_it_creates(session):
    """C4's mechanism: family membership is a LABEL, which is Kometa's own
    handle for the same job (``append_label: str(map_name)``, meta.py:1421) and
    is what 10a-2's sweep will enumerate. Never an offline title list."""
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    for collection in section._existing.values():
        assert family_label(definition) in collection.labels_added
        assert LABEL in collection.labels_added


async def test_the_family_writes_one_managed_row_per_collection(session):
    section = FakeSection()
    definition = _definition()
    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert sorted(row.title for row in rows) == [
        "Top Drama movies", "Top Horror movies",
    ]
    assert {row.kind for row in rows} == {"smart"}


async def test_an_unchanged_second_pass_writes_nothing(session):
    section = FakeSection()
    definition = _definition()
    ctx = _ctx(session, section, definition)
    await REGISTRY["dynamic"].apply(ctx)
    before = len(section._server.queries)
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert len(section._server.queries) == before
    assert actions == []


async def test_a_dry_run_reports_the_whole_family_and_writes_nothing(session):
    section = FakeSection()
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(), dry_run=True)
    )
    assert len(actions) == 2
    assert all(one.startswith("would create") for one in actions)
    assert section._existing == {}


async def test_the_leftovers_bucket_never_asks_plex_for_the_absent_value(session):
    """Plex's ``None`` choice is "these items have no value for this field",
    not a value. The titling layer drops such a key outright; the ``other``
    bucket is the one place it can still reach a QUERY, because its values are
    the leftover KEYS themselves -- and ``genre=None`` is a term this library
    would answer with something nobody asked for."""
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("9", "Drama"),
        FakeChoice("None", "None"),
    ])
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={
            "type": "genre",
            "include": ["Horror"],
            "other_name": "Everything Else",
        },
    )))

    assert sorted(section._existing) == ["Everything Else", "Top Horror movies"]
    assert not any("None" in one for one in section.fetched), section.fetched
    assert any("created 'Everything Else'" in one for one in actions)


async def test_a_synthetic_addon_key_in_the_leftovers_bucket_refuses_rather_than_raising(session):
    """The leftovers bucket's values are the leftover KEYS themselves
    (``dynamic_keys`` routes any surviving key no ``include:`` entry named into
    ``other_keys``, and ``family_titles`` passes them through verbatim) -- and a
    synthetic ``addons`` key is a bucket NAME, not a value the library holds. On
    an ``int`` type that reaches ``parse_filters`` as ``year: 'Eighties'``,
    whose ``ValueError`` is not in ``REFUSALS`` and would escape the engine's
    unwrapped smart dispatch, costing the WHOLE library its reconcile rather
    than costing this one bucket. The config below validates today."""
    section = FakeSection(choices=[
        FakeChoice("1980", "1980"), FakeChoice("1990", "1990"),
        FakeChoice("2000", "2000"),
    ])
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        title="Years",
        params={
            "type": "year",
            "include": ["1990"],
            "addons": {"Eighties": ["1980"]},
            "other_name": "Everything Else",
        },
    )))

    # Contained to the one bucket: the included key is still built.
    assert list(section._existing) == ["Best movies of 1990"]
    refusals = [one for one in actions if one.startswith("refused")]
    assert len(refusals) == 1
    assert "Everything Else" in refusals[0], "the refusal names the bucket"
    assert "Years" in refusals[0], "...the definition"
    assert "Eighties" in refusals[0], "...and the value Plex cannot be asked for"


async def test_an_empty_addon_key_in_the_leftovers_bucket_refuses_the_same_way(session):
    """The same hole on a TAG type, so the catch is not an ``int`` special
    case: an empty ``addons`` key survives the params model, reaches
    ``other_keys`` by the same route, and ``parse_filters`` refuses an empty tag
    value -- "an empty value matches nothing" -- which is the right verdict and
    the wrong exit."""
    section = FakeSection()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={
            "type": "genre",
            "include": ["Horror"],
            "addons": {"": ["Drama"]},
            "other_name": "Everything Else",
        },
    )))

    assert list(section._existing) == ["Top Horror movies"]
    refusals = [one for one in actions if one.startswith("refused")]
    assert len(refusals) == 1
    assert "Everything Else" in refusals[0]
    assert "Genres" in refusals[0]
    assert "empty value" in refusals[0]


# --- the refusals, which RETURN ----------------------------------------------


async def test_a_library_type_the_type_does_not_serve_refuses_without_enumerating(session):
    """``network`` is show-only upstream (meta.py:19). The refusal returns, and
    it costs zero Plex round trips -- the gate is above the enumeration."""
    section = FakeSection()
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(params={"type": "network"}))
    )
    assert len(actions) == 1
    assert "refused" in actions[0]
    assert section.choice_calls == []


async def test_a_type_that_enumerates_nothing_refuses_and_names_the_library(session):
    """A per-value family with no values must not quietly build one collection
    named after nothing -- and the message names the LIBRARY, because a section
    the API types as ``movie`` that answers nothing is a real operator state
    (the phase's probe found the DVR section answering ``country`` with zero
    values), not a misconfiguration."""
    section = FakeSection(choices=[])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition())
    )
    assert len(actions) == 1
    assert "enumerate" in actions[0]
    assert "Movies" in actions[0]
    assert section._existing == {}


async def test_a_dead_filter_lookup_refuses_class_name_only(session):
    """The resolver wraps both Plex failure classes with the class name and
    nothing else, because either can carry a tokenised URL. The refusal
    RETURNS -- nothing raises through the engine's unwrapped smart dispatch."""
    from plexapi.exceptions import BadRequest

    class Refusing(FakeSection):
        def listFilterChoices(self, field, libtype=None):
            self.choice_calls.append((field, libtype))
            raise BadRequest("bad request; https://plex.example/library?X-Plex-Token=SECRET")

    section = Refusing()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition()))
    assert len(actions) == 1
    assert "BadRequest" in actions[0]
    assert "SECRET" not in actions[0]
    assert "X-Plex-Token" not in actions[0]


async def test_a_fan_out_past_the_cap_refuses_with_both_numbers(session):
    """C8's refuse-over-surprise floor, at the scale the phase's live probe
    actually measured: ``studio`` enumerates to 824 values on the production
    movie library, sixteen times the default cap. The operator raises the cap
    deliberately or narrows the family, and either way nothing is created
    behind their back."""
    section = FakeSection(choices=[FakeChoice(str(n), str(n)) for n in range(824)])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(params={"type": "genre"}))
    )
    assert len(actions) == 1
    assert "824" in actions[0] and "50" in actions[0]
    assert "max_collections" in actions[0]
    assert "genre" in actions[0], "the refusal names the type that fanned out"
    assert "include" in actions[0], "...and the other way out"
    assert section._existing == {}


async def test_raising_the_cap_lets_the_same_family_build(session):
    section = FakeSection(choices=[FakeChoice(str(n), str(n)) for n in range(60)])
    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(
            params={"type": "genre", "max_collections": 100}
        ))
    )
    assert len(section._existing) == 60
    assert len([one for one in actions if one.startswith("created ")]) == 60


async def test_two_keys_that_title_the_same_collection_refuse_the_whole_family(session):
    section = FakeSection(choices=[FakeChoice("1", "Sci-Fi"), FakeChoice("2", "SciFi")])
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={
            "type": "genre",
            "key_name_override": {"Sci-Fi": "Science Fiction"},
            "title_override": {"SciFi": "Top Science Fiction movies"},
        },
    )))
    assert len(actions) == 1
    assert "Sci-Fi" in actions[0] and "SciFi" in actions[0]
    assert section._existing == {}


async def test_one_key_that_refuses_costs_only_that_key(session):
    """A per-key refusal is contained to its key: the rest of the family is
    still built, because one collection Plex cannot answer for is not a reason
    to stop managing eleven working ones."""

    class HalfMatching(FakeSection):
        def fetchItems(self, path, **kw):
            self.fetched.append(path)
            # Drama's key. Its filter matches nothing right now, which is the
            # one refusal a per-key query can meet that the enumeration itself
            # cannot predict.
            return [] if "genre=9" in path else [FakeItem("1")]

    section = HalfMatching()
    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition()))

    assert list(section._existing) == ["Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)
    refusals = [one for one in actions if one.startswith("refused")]
    assert len(refusals) == 1
    assert "Top Drama movies" in refusals[0]


# --- the record the family sweep reads ---------------------------------------


async def test_a_family_records_every_title_it_derived_for_the_sweep(session):
    """The record is the sweep's whole input, so what goes into it is a
    promise: EVERY title the family derived, written before a single collection
    is created. A key whose write refuses is still a key this family builds."""
    from autoposter.collections.builders.dynamic import _generated_key

    definition = _definition()
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert ctx.run_cache[_generated_key(family_label(definition))] == {
        "Top Horror movies", "Top Drama movies",
    }


async def test_a_family_level_refusal_records_nothing_at_all(session):
    """The fail-closed half, at its source: a definition that refuses before it
    derives anything leaves NO record, and ``generated_titles`` answers None --
    which the sweep reads as "do not consider this family's collections"."""
    from autoposter.collections.builders.dynamic import generated_titles

    definition = _definition()
    section = FakeSection(choices=[])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert actions and actions[0].startswith("refused")
    assert generated_titles(ctx.run_cache, definition) is None


async def test_an_all_excluded_family_leaves_no_record_not_an_empty_one(session):
    """The other family-level refusal that can zero out ``titled``: every
    enumerated value survives to ``derive_keys`` but none of them is in
    ``include``, so ``family_titles`` claims nothing and ``if not titled:``
    (dynamic.py:565-570) returns before the record write at :589-590 -- the
    same refusal-before-write structure the empty-enumeration case above
    exercises, reached a different way. Pinning this matters because the
    engine reads absence and an empty set as OPPOSITES (None protects the
    whole family; ``set()`` makes every member a candidate), so a record must
    never be written here -- ``run_cache`` must lack the key entirely, not
    hold ``set()``."""
    from autoposter.collections.builders.dynamic import (
        _generated_key, generated_titles,
    )

    definition = _definition(params={"type": "genre", "include": ["nothing-here"]})
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert actions and actions[0].startswith("refused")
    assert "excluded" in actions[0]
    assert generated_titles(ctx.run_cache, definition) is None
    assert _generated_key(family_label(definition)) not in ctx.run_cache


# --- delete-below-minimum -----------------------------------------------------


def test_minimum_items_is_off_by_default_and_says_it_is_a_divergence():
    """Upstream has NO per-key minimum for the library dynamic types
    (p10a-upstream-dynamic.md 7.2), so the default cannot be a number an
    operator would have to discover and turn off."""
    assert DynamicParams(type="genre").minimum_items is None
    with pytest.raises(ValidationError):
        DynamicParams(type="genre", minimum_items=0)


async def test_a_key_below_the_minimum_is_not_created(session):
    definition = _definition(params={"type": "genre", "minimum_items": 4})
    section = FakeSection()   # every search answers with three items
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert section._existing == {}
    assert any(
        "matches 3 item(s)" in one and "minimum_items" in one and "4" in one
        for one in actions
    ), actions


async def test_a_key_below_the_minimum_leaves_the_sweeps_record(session):
    """The whole of delete-below-minimum: the key drops out of the pass's
    generated record and the family sweep -- one mechanism, through the same
    guards -- deletes the collection if one already exists. Nothing here
    deletes anything itself."""
    from autoposter.collections.builders.dynamic import _generated_key

    definition = _definition(params={"type": "genre", "minimum_items": 4})
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert ctx.run_cache[_generated_key(family_label(definition))] == set()


async def test_a_key_whose_count_plex_refuses_stays_in_the_record(session):
    """The fail-closed half of the same removal, and the reason the discard
    sits AFTER a successful count rather than before one. A count that could
    not be taken is not "below the minimum" -- it is one dead Plex read, and
    reading it as a narrowing would hand the sweep a family it never measured.
    The title stays, and the sweep protects the collection."""
    from autoposter.collections.builders.dynamic import _generated_key

    class Dead(FakeSection):
        def fetchItems(self, path, **kw):
            self.fetched.append(path)
            raise RuntimeError("boom")

    definition = _definition(params={"type": "genre", "minimum_items": 4})
    section = Dead()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert ctx.run_cache[_generated_key(family_label(definition))] == {
        "Top Horror movies", "Top Drama movies",
    }
    assert len([one for one in actions if one.startswith("refused")]) == 2
    assert all("RuntimeError" in one for one in actions)


async def test_a_key_at_the_minimum_is_created(session):
    """The boundary is inclusive: `minimum_items: 3` means three is enough."""
    definition = _definition(params={"type": "genre", "minimum_items": 3})
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert set(section._existing) == {"Top Horror movies", "Top Drama movies"}


async def test_no_minimum_costs_no_extra_plex_read(session):
    """The count is a Plex round trip per key. It is paid only by an operator
    who asked for it -- ``reconcile_smart_collection`` does its own C8 probe on
    the create/update path, and doing it twice for every family in every pass
    would double the read cost of the default configuration."""
    definition = _definition()
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    # Two keys, one probe each, from reconcile_smart_collection's own C8 gate.
    assert len(section.fetched) == 2


# --- what the family tells the operator ---------------------------------------


async def test_the_absent_value_key_is_reported_rather_than_silently_dropped(
    session,
):
    """10a-1 review, T4's deferred minor. ``ABSENT_KEY`` is Plex's "these items
    have no value for this field" and ``family_titles`` drops it, which is
    right -- but silently, so an operator whose library has 40 unrated films
    sees a family with no bucket for them and no reason why. And a real tag
    named literally "None" is indistinguishable from the sentinel at this
    layer, so the report has to say that too rather than pretend to know."""
    definition = _definition()
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("None", "None"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert any(
        "'None'" in one and "no value" in one and "title_override" not in one
        for one in actions
    ), actions
    assert "Top None movies" not in section._existing


async def test_narrowing_entries_the_library_never_reports_are_named(session):
    """10a-1 review, Minor N-5. An ``include:``/``exclude:``/``addons:``/
    override entry naming a key the library does not hold is silently inert --
    upstream parity, and the all-excluded family does refuse, so this is not a
    correctness hole. It is a typo an operator cannot see: ``include: [Horor]``
    builds a family with one fewer collection and says nothing."""
    definition = _definition(params={
        "type": "genre",
        "include": ["Horror", "Horor"],
        "key_name_override": {"Wsetern": "Western"},
    })
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    inert = [one for one in actions if "names no value" in one]
    assert len(inert) == 1, actions
    assert "'Horor'" in inert[0] and "include" in inert[0]
    assert "'Wsetern'" in inert[0] and "key_name_override" in inert[0]
    assert "'Horror'" not in inert[0]


async def test_an_addons_report_names_the_members_and_not_the_bucket(session):
    """``addons`` is the one narrowing key whose ENTRIES are not what goes
    inert. An addon KEY the library never reported is upstream's synthetic
    bucket (``dynamic_keys`` :140-151) -- it builds a real collection under
    that name, which is the opposite of doing nothing, and a typo in it is
    visible in the family's own titles. An addon MEMBER the library never
    reported is dropped twice over, silently, and is the invisible typo this
    report exists for."""
    definition = _definition(params={
        "type": "genre", "addons": {"Eighties": ["Horror", "Dama"]},
    })
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert "Top Eighties movies" in section._existing, (
        "the synthetic bucket is built, so naming it inert would be a lie"
    )
    inert = [one for one in actions if "names no value" in one]
    assert len(inert) == 1, actions
    assert "'Dama'" in inert[0] and "addons" in inert[0]
    assert "'Eighties'" not in inert[0]
