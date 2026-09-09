"""``dynamic``: one smart collection per distinct value the library holds.

The engine ``collections/buckets.py`` is one hardcoded instance of. It
enumerates through ``LibraryTagResolver.choices``, derives its keys and titles
with the two pure modules beside it, and writes each collection through the SAME
reconciler ``smart_filter`` uses -- so there is one write path, one query
grammar and one drift hash for every smart collection this service manages
(10a decision C1).
"""
import datetime as dt
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.builders.dynamic import (
    DynamicBuilder,
    DynamicParams,
    YearWindow,
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
    """``queries`` holds WRITES. A write always names a ``method``; the only
    read through here is ``smart.count_matches``'s container-size-0 count
    (roadmap row 198), which names none -- answered from the section's own
    ``fetchItems`` so the count and the fetch cannot disagree, and so a
    section that refuses one refuses the other."""

    def __init__(self, section=None):
        self.queries = []
        self.reads = []
        self._section = section
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://abc123/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((key, headers))
            return SimpleNamespace(
                attrib={"totalSize": str(len(self._section.fetchItems(key)))}
            )
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1", subtype="movie"):
        self.title = title
        self.ratingKey = rating_key
        self.smart = True
        self.subtype = subtype
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
        self._server = FakeServer(self)
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


def test_a_zero_limit_is_the_no_limit_sentinel_and_a_negative_one_still_refuses():
    """The one thing ``limit`` could not say, and now can (Addendum 2).

    ``None`` already means "whatever the type's row pins" (50 for every row),
    so before this sentinel there was no value at all meaning "ask Plex for the
    whole match set" -- which is precisely what Kometa's shipped packs do: they
    pass ``template: [smart_filter, shared]``, whose ``limit`` is an OPTIONAL
    variable, and a pack that does not supply it emits a search with no
    ``limit=`` byte (record §1.8, ``defaults/templates.yml:238-255``). Six of
    the seven packs are in that position, so without a sentinel every one of
    them would ship a 50-item divergence in what its collections CONTAIN.

    Zero is the sentinel rather than a new field because it is already the
    value the query builder treats as "no limit" (``search_url.py:146-154``,
    Kometa's own ``if limit`` test at builder.py:4289) -- so the meaning is
    read off the emitter rather than invented here. Below zero stays refused:
    it is a typo, not an intent.
    """
    assert DynamicParams(type="genre", limit=0).limit == 0
    assert DynamicParams(type="genre").limit is None

    with pytest.raises(ValidationError, match="limit"):
        DynamicParams(type="genre", limit=-1)


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


def test_a_malformed_token_is_refused_like_a_wrong_one():
    """T5 review, Minor 4. ``_TOKEN`` matches ``<<...>>`` and a half-written
    ``<<value>`` matches nothing at all -- so it passed every validator and
    would have been POSTed into a live collection's name exactly as typed,
    which is the one outcome this whole family of refusals exists to prevent.
    A well-formed ``<<key_name>>`` sits alongside it so this reaches the
    unbalanced-delimiter check rather than the separate "must name the key"
    one, which a text with no well-formed token at all would trip first.
    """
    with pytest.raises(ValidationError) as caught:
        DynamicParams(type="genre", title_format="Top <<key_name>> movies <<value")
    assert "<<" in str(caught.value)
    assert "unbalanced" in str(caught.value).lower()

    # And the well-formed one still passes, so the fix is not "refuse angle
    # brackets".
    assert DynamicParams(type="genre", title_format="Top <<key_name>> movies")


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


async def test_the_no_limit_sentinel_reaches_plex_as_a_query_with_no_limit(session):
    """The other half of the sentinel: what the emitted URL actually says.

    The params model accepting ``0`` is worth nothing if the consumption site
    (``dynamic.py``'s ``build_search_url`` call) turns it back into the type's
    default. Both queries are asserted side by side so the difference is the
    sentinel and not the fixture: the default family carries ``limit=50``, the
    sentinel family carries no ``limit=`` at all -- which is byte-for-byte the
    query Kometa's own unlimited packs emit.
    """
    section = FakeSection()
    await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        params={"type": "genre", "limit": 0},
    )))
    assert section.fetched, "no search was emitted, so this proves nothing"
    assert all("limit=" not in one for one in section.fetched), section.fetched
    assert section.fetched[0] == (
        "/library/sections/2/all?type=1&sort=rating%3Adesc&push=1&genre=1138&pop=1"
    )

    default = FakeSection()
    await REGISTRY["dynamic"].apply(_ctx(session, default, _definition()))
    assert default.fetched[0] == (
        "/library/sections/2/all"
        "?type=1&limit=50&sort=rating%3Adesc&push=1&genre=1138&pop=1"
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
    is what 10a-2's sweep enumerates. Never an offline title list."""
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


async def test_a_locked_summary_on_a_family_member_survives_a_definition_change(session):
    """This builder passes ``summary=None`` to the reconciler unconditionally,
    and its definitions refuse ``summary:`` and ``tmdb_summary:`` at load -- so
    that None can never mean "the definition asserts no summary". The only
    summary a generated collection can carry is Plex's own or an operator's,
    and a definition change (here: a new label) is not a licence to delete it."""
    section = FakeSection()
    await REGISTRY["dynamic"].apply(_ctx(session, section, _definition()))
    member = section._existing["Top Horror movies"]
    # What Plex's UI leaves behind on a hand-edit: the text, and the lock it
    # sets on every field it writes -- the same marker the managed clear reads.
    member.summary = "An operator's own text."
    member._real_fields[0].locked = True
    before = len(member._server.queries)

    actions = await REGISTRY["dynamic"].apply(
        _ctx(session, section, _definition(labels=["Curated"]))
    )

    assert member.summary == "An operator's own text."
    assert member._real_fields[0].locked is True
    assert len(member._server.queries) == before, "no summary write"
    assert not any("cleared the summary" in one for one in actions), actions


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


async def test_a_bucket_refusal_does_not_leak_the_parsers_field_path(session):
    """T5 review, deferred nit. ``parse_filters`` prefixes its refusals with the
    dotted config path it was given, so the operator's sentence read '... and
    params.year: 'Eighties' is not a whole number'. The prefix is this module's
    own argument, not anything the operator wrote."""
    definition = _definition(params={
        "type": "year", "include": ["1990"], "addons": {"Eighties": ["1989"]},
        "other_name": "Everything else",
    })
    section = FakeSection(choices=[
        FakeChoice("1990", "1990"), FakeChoice("1989", "1989"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    refused = [one for one in actions if "refused" in one]
    assert refused, actions
    assert "params.year" not in refused[0], refused[0]
    assert "whole number" in refused[0] or "integer" in refused[0]


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


async def test_a_raw_other_tag_never_included_builds_fine(session):
    """T5 review, other-key collision correction. A library value spelled
    ``other`` that the operator never `include`d is not a second titled entry
    keyed ``other`` -- it simply lands among the leftovers bucket's own
    ``values`` (``dynamic_keys.derive_keys``, ``dynamic_titles.family_titles``
    :309-335). One bucket, not two, so there is no collision to refuse: this
    config shape is previously-fine and must keep building both collections."""
    definition = _definition(params={
        "type": "genre", "include": ["Horror"], "other_name": "Everything else",
    })
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("77", "other"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert not any("refused" in one for one in actions), actions
    assert any("Top Horror movies" in one and "created" in one for one in actions)
    assert any("Everything else" in one and "created" in one for one in actions)


async def test_two_titled_entries_keyed_other_refuses_the_collision(session):
    """T4 review's actual finding (``progress.md:2831``, "OTHER_KEY 'other' can
    collide with a real enumerated key"), reconstructed: a real library value
    spelled ``other`` that IS `include`d gets its own titled entry keyed
    ``other`` (``derive_keys`` :156-165), and ``family_titles`` claims the
    leftovers bucket LAST with that same key (:331-335) -- two entries, one
    key. That is the actual ambiguity the origin named, and it is refused."""
    definition = _definition(params={
        "type": "genre", "include": ["Horror", "other"],
        "other_name": "Everything else",
    })
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("77", "other"),
        FakeChoice("9", "Drama"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert any("Top Horror movies" in one and "created" in one for one in actions)
    assert any("Everything else" in one and "created" in one for one in actions)
    assert any(
        "'other'" in one and "leftovers" in one and "refused" in one
        for one in actions
    ), actions


async def test_a_language_family_expands_a_base_code_at_the_emitter(session):
    """10a-1 review, Minor N-4. The language seam -- a family's values are
    ``choice.key``s, and ``LibraryTagResolver._language_keys`` expands a base
    code to every variant the library holds (plex_search.py:518-538) -- was
    correct by reading and pinned only at the resolver. Pinned here at the
    EMITTER, the way ``decade``'s key/title split already is."""
    definition = _definition(params={
        "type": "audio_language", "include": ["es"],
    })
    section = FakeSection(choices=[
        FakeChoice("es", "Spanish"), FakeChoice("es-419", "Spanish (Latin America)"),
        FakeChoice("en", "English"),
    ])
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert len(section.fetched) == 1, section.fetched
    assert "audioLanguage=es&or=1&audioLanguage=es-419" in section.fetched[0], (
        section.fetched
    )


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


async def test_the_cap_refusal_counts_buckets_and_values_separately(session):
    """T5 review, Minor N-3. The message said "%r enumerates that many values
    there" while counting BUCKETS -- post-merge, post-drop -- so an addons-heavy
    family reported a number the library never said. Both numbers now, because
    the operator's next move (raise the cap, or narrow with `include:`) depends
    on which one is large.

    An addons bucket's members are excluded from having a collection of their
    own (dynamic_keys.py:144-146), so merging a two-value library into one
    bucket alone would leave exactly one collection -- never enough to breach
    any valid cap. A third, un-merged value keeps the family at two buckets
    while the library itself reports three values, which is exactly the gap
    the old wording hid.
    """
    definition = _definition(params={
        "type": "genre", "max_collections": 1,
        "addons": {"Scary": ["Horror", "Drama"]},
    })
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("9", "Drama"),
        FakeChoice("77", "Comedy"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert len(actions) == 1
    assert "3 value(s)" in actions[0], actions      # Horror, Drama, Comedy
    assert "2 collections" in actions[0], actions
    assert "max_collections" in actions[0]


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


def test_minimum_items_is_off_by_default_and_zero_refuses_at_load():
    """Upstream has NO per-key minimum for the library dynamic types
    (p10a-upstream-dynamic.md 7.2), so the default cannot be a number an
    operator would have to discover and turn off. (The divergence itself is
    documented on the field, not asserted here -- this test only pins the two
    load-time behaviours: off by default, and zero is not a floor.)"""
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


async def test_an_addons_key_with_every_member_absent_is_named_as_dead(session):
    """T2 review, Minor M-3. A ``addons`` key whose every member is absent from
    the library builds no collection at all (dynamic_keys.py:144-146,
    ``test_a_synthetic_bucket_with_no_present_member_builds_nothing``), which
    looked identical in the old report to a bucket that merely lost one of
    several members -- and those two are very different for the operator: one
    means the bucket is fine and one means it is dead. Named separately, using
    the two-set machinery already in ``_inert``."""
    definition = _definition(params={
        "type": "genre", "addons": {"Kids": ["Zzz", "Yyy"]},
    })
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert "Top Kids movies" not in section._existing
    inert = [one for one in actions if "names no value" in one]
    assert len(inert) == 1, actions
    assert "'Kids'" in inert[0], inert[0]


async def test_an_include_by_display_value_is_reported_inert_on_a_keyed_type(
    session,
):
    """I-1. ``genre`` (key == value) cannot see this bug: ``include`` matches
    the KEY only (``dynamic_keys.py:156``), but ``decade`` keys on
    ``choice.key`` (``1980``) while titling the display value (``1980s``). An
    operator who writes the display form is a typo the old single
    ``present_keys`` union (keys and values both) could not detect. The key
    form, spelled correctly, must build and must NOT be reported."""
    definition = _definition(params={
        "type": "decade", "include": ["1980", "1980s"],
    })
    section = FakeSection(choices=[
        FakeChoice("1980", "1980s"), FakeChoice("1990", "1990s"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert "Best movies of the 1980s" in section._existing
    inert = [one for one in actions if "names no value" in one]
    assert len(inert) == 1, actions
    assert "include: '1980s'" in inert[0]
    assert "include: '1980'" not in inert[0]


# --- the relative year window (`data: {starting, ending}`) -------------------
#
# Kometa's `number` dynamic type, narrowed. Upstream parses the two bounds
# (meta.py:1119-1134) and then enumerates the WHOLE range without asking the
# library (meta.py:1138-1143); here the window narrows the keys
# `LibraryTagResolver.choices` reported, so a year the library holds nothing
# from is simply not a key. The sentinel parser is row 171's own
# (`filters._as_current_year`) and there is deliberately no second one.

YEARS = [
    FakeChoice("1", "2014"), FakeChoice("2", "2016"),
    FakeChoice("3", "2020"), FakeChoice("4", "2026"),
    FakeChoice("5", "2030"),
]

# The window every test below writes, as the pack writes it.
WINDOW = {"starting": "current_year-10", "ending": "current_year"}


def _frozen(monkeypatch, year: int) -> None:
    """Pin the module's one clock seam to midday on 4 March of ``year``.

    A seam rather than a monkeypatched ``datetime`` class: the load-time
    validator and ``apply`` both read it, so one patch covers both, and
    patching ``dt.datetime`` itself would reach every other module sharing
    that module object.
    """
    from autoposter.collections.builders import dynamic as module

    monkeypatch.setattr(module, "_now", lambda: dt.datetime(year, 3, 4, 12, 0))


def test_a_window_resolves_the_sentinel_against_the_moment_it_is_given():
    """Row 171's parser, reused. Subtraction, never addition: Kometa's own
    transcription is ``datetime.now().year - int(offset)``, so
    ``current_year-10`` is ten years AGO (meta.py:1122)."""
    window = YearWindow(starting="current_year-10", ending="current_year")

    assert window.resolve(dt.datetime(2026, 3, 4)) == (2016, 2026)
    # The same window, one year later: both bounds move together.
    assert window.resolve(dt.datetime(2027, 1, 1)) == (2017, 2027)


def test_a_window_takes_plain_years_too_and_leaves_them_alone():
    assert YearWindow(starting=1994, ending=1999).resolve(
        dt.datetime(2026, 3, 4)
    ) == (1994, 1999)


def test_a_window_is_accepted_on_the_year_type_and_refused_on_every_other():
    """The narrowing, both directions. Kometa's `number` type DOES read
    `data:` (meta.py:1112-1143), so the blanket refusal was wrong for this one
    type -- and right for the other nine, where nothing upstream parses it."""
    accepted = DynamicParams(type="year", data=WINDOW)
    assert accepted.data is not None
    assert accepted.data.resolve(dt.datetime(2026, 3, 4)) == (2016, 2026)

    with pytest.raises(ValueError) as refusal:
        DynamicParams(type="genre", data=WINDOW)
    assert "data" in str(refusal.value)
    assert "year" in str(refusal.value), (
        "the refusal has to name the one type that does read it, or an "
        "operator porting year.yml has nowhere to go"
    )

    # Without a window, nothing changed at all.
    assert DynamicParams(type="year").data is None


def test_a_bound_that_is_neither_a_year_nor_the_sentinel_refuses_at_load():
    """Four near misses, including the two the shared parser deliberately
    refuses that Kometa would take: whitespace around the dash, and a `+`
    offset (`_as_current_year`'s own docstring says why).

    The message THIS module produces is a fixed sentence that never echoes
    what was written. pydantic's envelope around it does carry an
    ``input_value=`` echo -- that is the framework's, not this module's, and
    the API layer already drops it before anything is served -- so the
    assertion is against the constant rather than against the whole string.
    """
    from autoposter.collections.builders.dynamic import WINDOW_BOUND_REFUSAL

    for written in ("last_year", "current_year - 5", "current_year+2", "the 90s"):
        with pytest.raises(ValidationError) as refusal:
            DynamicParams(type="year", data={"starting": written, "ending": "current_year"})
        assert WINDOW_BOUND_REFUSAL in str(refusal.value), written
        assert written not in WINDOW_BOUND_REFUSAL


def test_a_bound_outside_the_believable_range_refuses_at_load(monkeypatch):
    _frozen(monkeypatch, 2026)
    for window in (
        {"starting": 1799, "ending": "current_year"},
        {"starting": "current_year-10", "ending": 2028},
    ):
        with pytest.raises(ValidationError):
            DynamicParams(type="year", data=window)

    # The edges themselves are fine. Asserted on the window alone, because a
    # window from 1800 to next year is 228 collections and the model's own
    # `max_collections` rule would refuse it for a different reason entirely.
    assert YearWindow(starting=1800, ending="current_year")
    assert YearWindow(starting="current_year-1", ending=2027)


def test_a_window_that_ends_before_it_starts_refuses_at_load(monkeypatch):
    """Upstream refuses the same way (meta.py:1136-1137). Refused at LOAD
    rather than at build, because an empty window is a definition that can
    never build anything and the operator is right here."""
    _frozen(monkeypatch, 2026)
    with pytest.raises(ValidationError) as refusal:
        DynamicParams(type="year", data={"starting": "current_year", "ending": "current_year-10"})
    assert "empty" in str(refusal.value)


def test_a_window_wider_than_max_collections_refuses_at_load(monkeypatch):
    _frozen(monkeypatch, 2026)
    with pytest.raises(ValidationError) as refusal:
        DynamicParams(type="year", data={"starting": 1900, "ending": "current_year"})
    assert "max_collections" in str(refusal.value)

    # ...and raising the cap on purpose is how it is allowed.
    assert DynamicParams(
        type="year", data={"starting": 1900, "ending": "current_year"},
        max_collections=200,
    )


async def test_the_window_keeps_only_the_years_the_library_holds_inside_it(
    session, monkeypatch
):
    """C5's three cases in one family: a year below the window is absent, a
    year above it is absent, the years inside it are present, and a year
    INSIDE the window that the library holds nothing from is absent too --
    which is where this diverges from upstream, whose `number` type
    enumerates the whole range without asking the library
    (meta.py:1138-1143)."""
    _frozen(monkeypatch, 2026)
    section = FakeSection(choices=YEARS)
    definition = _definition(title="Years", params={
        "type": "year", "data": WINDOW,
        "title_format": "Best of <<key_name>>",
    })

    await REGISTRY["dynamic"].apply(_ctx(session, section, definition))

    assert sorted(section._existing) == [
        "Best of 2016", "Best of 2020", "Best of 2026",
    ]
    # 2014 is below the window, 2030 is above it, and 2018 is inside it and
    # not in the library -- no collection for any of the three.
    assert "Best of 2014" not in section._existing
    assert "Best of 2030" not in section._existing
    assert "Best of 2018" not in section._existing


async def test_include_and_exclude_still_compose_on_top_of_the_window(
    session, monkeypatch
):
    """The window narrows the ENUMERATION; the five narrowing sources then run
    over what is left, unchanged. A year excluded inside the window goes, and
    an include list narrows further still."""
    _frozen(monkeypatch, 2026)
    section = FakeSection(choices=YEARS)

    await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        title="Years", params={
            "type": "year", "data": WINDOW,
            "title_format": "Best of <<key_name>>", "exclude": ["2020"],
        },
    )))
    assert sorted(section._existing) == ["Best of 2016", "Best of 2026"]

    narrower = FakeSection(choices=YEARS)
    await REGISTRY["dynamic"].apply(_ctx(session, narrower, _definition(
        title="Years", params={
            "type": "year", "data": WINDOW,
            "title_format": "Best of <<key_name>>", "include": ["2026"],
        },
    )))
    assert sorted(narrower._existing) == ["Best of 2026"]


async def test_a_window_the_library_answers_nothing_inside_refuses_the_family(
    session, monkeypatch
):
    """Its own refusal, not the "reports no values at all" one: the library
    DID answer, and saying otherwise would send an operator to look at a
    library that is fine. Family-level, so nothing is recorded and the sweep
    considers none of this family's collections."""
    from autoposter.collections.builders.dynamic import generated_titles

    _frozen(monkeypatch, 2026)
    section = FakeSection(choices=[FakeChoice("1", "1999"), FakeChoice("2", "2004")])
    definition = _definition(title="Years", params={
        "type": "year", "data": WINDOW, "title_format": "Best of <<key_name>>",
    })
    ctx = _ctx(session, section, definition)

    actions = await REGISTRY["dynamic"].apply(ctx)

    assert actions and actions[0].startswith("refused")
    assert "window" in actions[0]
    assert section._existing == {}
    assert generated_titles(ctx.run_cache, definition) is None


async def test_next_january_the_oldest_year_leaves_the_familys_record(
    session, monkeypatch
):
    """C3, pinned. `sync: true` upstream is a labelled delete sweep
    (meta.py:1300, :1456-1461); this service runs that sweep for every builder
    instead, off the family label and the pass's generated record. So the
    whole of "the window slides" is that the oldest title stops being in the
    record -- after which `engine._sweep` treats an existing collection under
    it like any other candidate, through `delete_unconfigured`, the protecting
    labels and `max_deletes`. Nothing here deletes anything.
    """
    from autoposter.collections.builders.dynamic import generated_titles

    _frozen(monkeypatch, 2026)
    definition = _definition(title="Years", params={
        "type": "year", "data": WINDOW, "title_format": "Best of <<key_name>>",
    })
    choices = [FakeChoice(str(n), str(2016 + n)) for n in range(11)]

    first = _ctx(session, FakeSection(choices=choices), definition)
    await REGISTRY["dynamic"].apply(first)
    assert "Best of 2016" in generated_titles(first.run_cache, definition)

    _frozen(monkeypatch, 2027)
    second = _ctx(session, FakeSection(choices=choices), definition)
    await REGISTRY["dynamic"].apply(second)
    built = generated_titles(second.run_cache, definition)

    assert "Best of 2016" not in built, (
        "the window moved on and this key is no longer one the family builds"
    )
    assert "Best of 2017" in built
    assert "Best of 2026" in built


async def test_a_family_with_no_window_never_reads_the_clock(session, monkeypatch):
    """The no-behaviour-change pin for every `include`-only family, which is
    all seven shipped packs but this one. A family with no `data:` must not
    resolve anything against a moment -- so the clock seam is made to explode
    and the genre family builds anyway."""
    from autoposter.collections.builders import dynamic as module

    def _explode():
        raise AssertionError("a family with no `data:` window read the clock")

    monkeypatch.setattr(module, "_now", _explode)
    section = FakeSection()

    actions = await REGISTRY["dynamic"].apply(_ctx(session, section, _definition()))

    assert sorted(section._existing) == ["Top Drama movies", "Top Horror movies"]
    assert any("created 'Top Horror movies'" in one for one in actions)


async def test_a_windowed_family_asks_plex_for_the_year_and_the_pinned_top_ten(
    session, monkeypatch
):
    """The emitted query, byte for byte: the key is the year, the sort is the
    pack's and the limit is the pack's. Upstream's `smart_filter` template
    builds the same three things out of `search_term`, `sort_by` and `limit`
    (defaults/templates.yml:238-255)."""
    _frozen(monkeypatch, 2026)
    section = FakeSection(choices=[FakeChoice("4", "2026")])

    await REGISTRY["dynamic"].apply(_ctx(session, section, _definition(
        title="Years", params={
            "type": "year", "data": WINDOW,
            "title_format": "Best of <<key_name>>",
            "sort_by": ["critic_rating.desc"], "limit": 10,
        },
    )))

    assert section.fetched == [
        "/library/sections/2/all"
        "?type=1&limit=10&sort=rating%3Adesc"
        "&push=1&year=2026&pop=1"
    ]
