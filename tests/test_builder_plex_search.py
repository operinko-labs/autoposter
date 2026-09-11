"""``plex_search`` -- the builder, its params, and its refusals.

The URL itself is proven elsewhere (``tests/test_collection_search_oracle.py``,
byte-identical against Kometa). What is proven here is the surface an operator
touches: which spellings load, which refuse and what they say, what the builder
asks Plex, and how many times it asks.
"""
import datetime as dt
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
import requests
from plexapi.exceptions import NotFound
from pydantic import ValidationError

from autoposter.collections.builders.base import (
    BuilderContext,
    LibraryTypeMismatch,
    SourceClients,
)
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchParams,
    PlexSearchBuilder,
    PlexSearchUnavailable,
)
from autoposter.collections.builders.sources_bundle import PlexSectionAccess


def test_a_base_is_required_and_named():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"genre": "Horror"})
    message = str(error.value)
    assert "any:" in message and "all:" in message


def test_two_bases_are_refused():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate(
            {"all": {"genre": "Horror"}, "any": {"studio": "A24"}}
        )
    assert "one base" in str(error.value)


def test_an_empty_all_base_names_all_not_any():
    """Important 1. YAML's most common mistake -- ``all:`` with nothing under
    it -- parses as ``{"all": None}``. Before the fix this fell through to
    ``base = "any"`` (computed as ``"all" if self.all is not None else
    "any"``) and sent the operator looking for an ``any:`` block that does
    not exist. The written key is known here, in the ``before`` validator,
    which is why the refusal can name it correctly."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": None})
    message = str(error.value)
    assert "`all:` is written but empty" in message
    assert "params.any" not in message


def test_an_empty_any_base_names_any_not_all():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"any": None})
    message = str(error.value)
    assert "`any:` is written but empty" in message
    assert "params.all" not in message


def test_a_non_mapping_base_is_refused_naming_its_own_key():
    """Kometa's other empty-base message (``{base} must be a dictionary``,
    kometa_build_filter.py:978), reproduced alongside the blank one."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": ["genre: Horror"]})
    assert "`all:` must be a mapping" in str(error.value)


def test_the_implicit_base_is_refused_with_kometas_rule_spelled_out():
    """D1. Kometa lets you omit the base and then splits the keys itself: a
    bare ``genre:`` becomes an OR block and ``genre.and:`` an AND one
    (builder.py:4261-4276), so the same key means two memberships depending on
    a three-character suffix. Refused, and the message says what to write."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"genre": "Horror", "studio": "A24"})
    message = str(error.value)
    assert "Kometa" in message
    assert "all:" in message


def test_validate_false_is_refused_by_name():
    """D1. Kometa's ``validate: false`` (builder.py:4160-4167) downgrades every
    per-attribute error to a log line and builds the query WITHOUT the clause
    it could not resolve -- a narrower collection than the config asks for,
    with no failure anywhere."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "validate": False})
    message = str(error.value)
    assert "validate" in message
    assert "silently" in message


def test_the_type_key_is_refused_by_name():
    """Kometa's ``type:`` (builder.py:4109-4121) selects the season/episode/
    album/track libtype. v1 searches movies and shows, and accepting the key
    while ignoring it would be a setting that reads as applied and is not.

    Asserts a fragment of the tailored reason rather than the bare word
    ``"type"`` -- every pydantic ``ValidationError`` contains that word on its
    own (``[type=value_error, ...]``), so it does not pin the refusal being
    exercised here at all (Task 4 review, Minor 9)."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "type": "episode"})
    message = str(error.value)
    assert "'type' is not accepted here" in message
    assert "season, episode, album or track" in message
    assert "builder_level" in message


def test_an_unknown_params_key_is_refused():
    """``extra="forbid"`` itself -- the class docstring's reason this model
    exists at all -- rather than one of the tailored ``_REFUSED_KEYS``/``sort``
    branches, which is what this test used to exercise (Task 4 review,
    Fix-round Carry 3: it duplicated ``test_the_key_is_sort_by_and_not_sort``
    on the same input, answered by the same ``sort`` branch, and its name
    describes neither)."""
    with pytest.raises(ValidationError):
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "nonsense": 1})


def test_the_key_is_sort_by_and_not_sort():
    """The naming warning, exercised. ``sort`` on the DEFINITION is the
    collection's Plex display order; ``sort_by`` in PARAMS is the query's
    order, which decides membership when a limit is present. Writing ``sort``
    here is a typo with a plausible-looking effect, so it refuses -- naming
    both keys and which is which (Task 4 review, Minor 10: this used to
    validate a *good* ``sort_by`` and duplicate the test below instead of
    exercising the refusal its own name describes)."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "sort": "title.asc"})
    message = str(error.value)
    assert "sort_by" in message
    assert "display order" in message


def test_a_scalar_sort_by_becomes_a_one_element_list():
    params = PlexSearchParams.model_validate(
        {"all": {"genre": "Horror"}, "sort_by": "added.desc"}
    )
    assert params.sort_by == ["added.desc"]


def test_an_unknown_sort_name_refuses_at_load():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate(
            {"all": {"genre": "Horror"}, "sort_by": "titel.asc"}
        )
    assert "titel.asc" in str(error.value)


def test_a_sort_only_one_libtype_has_still_loads():
    """It cannot refuse here: a definition with no ``libraries:`` key runs
    against every library in the pass, so which table applies is only known at
    build time. The build-time refusal is tested below."""
    PlexSearchParams.model_validate(
        {"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"}
    )


def test_a_limit_of_zero_refuses():
    with pytest.raises(ValidationError):
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "limit": 0})


def test_a_bad_attribute_inside_the_block_refuses_at_load_naming_the_key():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"aspect": "1.78"}})
    message = str(error.value)
    assert "aspect" in message
    assert "params.all" in message
    assert "plex_search" in message


def test_a_bad_modifier_inside_the_block_refuses_at_load():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre.begins": "Hor"}})
    assert "genre" in str(error.value)


def test_a_search_accepts_studio_regex_at_load():
    """Row 178's own exemplar attribute. Parsing alone does not need a
    library -- the vocabulary expansion happens at BUILD time -- so this only
    proves load-time acceptance; test_collection_search_url.py proves the
    render."""
    params = PlexSearchParams.model_validate({"all": {"studio.regex": "pictures$"}})
    [predicate] = params.group.children
    assert predicate.operator == "regex"


# --- the BUILD-time half ------------------------------------------------------
#
# ``BuilderContext``/``SourceClients``/``PlexSectionAccess`` are imported at the
# top of the file with everything else rather than here, where the plan's block
# put them: ruff's E4 set is selected repo-wide (pyproject.toml:73) and a
# mid-file import is E402.
#
# No ``@pytest.mark.asyncio`` anywhere below: this suite runs pytest-asyncio in
# ``asyncio_mode = "auto"`` (pyproject.toml:51), so an ``async def test_`` is
# collected as one already and the marker would be noise.


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


class FakeFilter:
    """One row of ``listFilters``. plexapi's ``FilteringFilter`` carries
    ``.filter``, ``.filterType``, ``.key``, ``.title`` and ``.type``
    (plexapi/library.py:2878-2897); ``get_search_key`` reads two of them and so
    does this service, so the fake carries two."""

    def __init__(self, filter_name, title):
        self.filter = filter_name
        self.title = title


# The 2026-09-06 probe's own answer
# (docs/research/plex-search-probe/listfilters-folder-location.md), with a
# leading non-folder row so a resolver taking the first filter rather than the
# matching one fails every test below. The observed key on this server is
# ``location``, not the plan's placeholder ``source``.
FOLDER_FILTERS = [FakeFilter("genre", "Genre"), FakeFilter("location", "Folder Location")]


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeSection:
    """Counts what it was asked, so 'one lookup per pass' is a measurement.

    ``raise_on`` maps a field name -- or, since search-tail E-1, a
    ``(field, libtype)`` pair -- to the exception INSTANCE
    ``listFilterChoices`` should raise for it -- an instance, not a class,
    because Minor 3's narrowing test needs to raise something that is not a
    plexapi error at all (a plain ``TypeError``, standing in for a bug in our
    own code) alongside cases that are. The pair form exists because the live
    server answers ``actor`` at one libtype and refuses it at another, and a
    fake that cannot say so cannot pin which scope the resolver asked.

    ``filters`` and ``raise_filters`` are search tail H's (roadmap row 176):
    ``listFilters`` is a SECOND network read, of the library's filter SCHEMA
    rather than one filter's values, and it has its own memo key, its own two
    wraps and its own refusal -- so it needs its own call log
    (``filter_type_calls``) or "one schema read per pass" is unmeasurable.
    A libtype absent from ``filters`` answers an empty list, which is what a
    server with no folder filter looks like.
    """

    key = 1

    def __init__(self, choices=None, items=None, raise_on=None,
                 filters=None, raise_filters=None):
        self._choices = choices or {}
        self._items = items or [FakeItem(11), FakeItem(12)]
        self._raise_on = dict(raise_on or {})
        self._filters = dict(filters or {})
        self._raise_filters = dict(raise_filters or {})
        self.filter_calls = []
        self.filter_type_calls = []
        self.fetch_calls = []

    def listFilters(self, libtype=None):
        self.filter_type_calls.append(libtype)
        if libtype in self._raise_filters:
            raise self._raise_filters[libtype]
        return list(self._filters.get(libtype, []))

    def listFilterChoices(self, field, libtype=None):
        self.filter_calls.append((field, libtype))
        if (field, libtype) in self._raise_on:
            raise self._raise_on[(field, libtype)]
        if field in self._raise_on:
            raise self._raise_on[field]
        return self._choices.get((field, libtype), [])

    def fetchItems(self, key):
        self.fetch_calls.append(key)
        return self._items


def context(section, *, library_type="Movie", config=None, run_cache=None, definition=None):
    return BuilderContext(
        library="Movies",
        library_type=library_type,
        config=config or {},
        run_cache=run_cache if run_cache is not None else {},
        sources=SourceClients(plex=PlexSectionAccess(section, lambda: {})),
        definition=definition,
    )


GENRES = [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


async def test_the_builder_sends_the_query_to_the_sections_all_endpoint():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(section, config={"all": {"genre": "Horror"}})
    result = await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort&genre=1138"
    ]
    assert result.ids == [("plex", "11"), ("plex", "12")]


async def test_current_year_in_a_plex_search_resolves_to_the_real_year():
    """Row 171's ``plex_search`` half (search-tails-2 Task 3, controller
    ruling). ``year`` is searchable and ``_as_current_year`` is not gated by
    ``searching``, so ``year: current_year`` parsed without error even before
    this fix -- and then reached ``search_url``'s plain ``str(value)``
    fallback carrying the unresolved ``_CurrentYear`` sentinel, rendering its
    own ``repr()`` (``year=_CurrentYear(offset=0)``) into the query Plex
    actually received. ``PlexSearchBuilder.build`` now resolves it against
    the run's own moment before calling ``build_search_url``, which stays
    pure and unchanged."""
    section = FakeSection()
    ctx = context(section, config={"all": {"year": "current_year"}})
    await PlexSearchBuilder().build(ctx)
    year = dt.datetime.now().year
    assert section.fetch_calls == [
        f"/library/sections/1/all?type=1&sort=titleSort&year={year}"
    ]


async def test_current_year_with_an_offset_resolves_too():
    section = FakeSection()
    ctx = context(section, config={"all": {"year": "current_year-5"}})
    await PlexSearchBuilder().build(ctx)
    year = dt.datetime.now().year - 5
    assert section.fetch_calls == [
        f"/library/sections/1/all?type=1&sort=titleSort&year={year}"
    ]


async def test_today_in_a_plex_search_date_predicate_resolves_to_the_real_moment():
    """``_Today`` shares ``_CurrentYear``'s exact gap shape -- an unresolved
    sentinel with no ``search_url.py`` branch of its own -- and the same fix
    (``filters.resolve_search_values``) closes it too. The resolved value is
    a bare ``YYYY-MM-DD`` -- the same shape every other date on this path
    renders (``_as_date`` returns a ``dt.date``), and the shape Kometa's own
    driver renders too -- its ``.before``/``.after`` branch is
    ``return_as="%Y-%m-%d"`` (``tests/oracle/9b/kometa_build_filter.py:814``)
    -- not a full ISO timestamp. Tolerant of ``today``/``yesterday`` rather
    than a frozen clock, to survive a midnight boundary during the run."""
    today = dt.date.today()
    section = FakeSection()
    ctx = context(section, config={"all": {"release.after": "today"}})
    await PlexSearchBuilder().build(ctx)

    (call,) = section.fetch_calls
    prefix = "/library/sections/1/all?type=1&sort=titleSort&originallyAvailableAt%3E%3E="
    assert call.startswith(prefix)
    assert "_Today" not in call
    resolved = call[len(prefix):]
    assert resolved in {today.isoformat(), (today - dt.timedelta(days=1)).isoformat()}


async def test_a_tag_value_is_looked_up_once_per_pass_and_cached():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    run_cache = {}
    for _ in range(3):
        ctx = context(
            section,
            config={"all": {"genre": ["Horror", "Drama"]}},
            run_cache=run_cache,
        )
        await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("genre", "movie")]


async def test_a_failed_lookup_is_cached_too_so_a_dead_field_is_asked_once():
    """``BuilderContext.run_cache``'s own docstring requires this: a builder
    that memoises must memoise the failure, or a dead source is re-fetched once
    per collection. ``NotFound`` -- not a generic exception -- because that is
    what ``listFilterChoices`` actually raises for a field the library does
    not have; see Minor 3 below for why the catch is narrowed to it."""
    section = FakeSection(raise_on={"genre": NotFound("no such filter field")})
    run_cache = {}
    for _ in range(3):
        ctx = context(section, config={"all": {"genre": "Horror"}}, run_cache=run_cache)
        with pytest.raises(Exception):
            await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("genre", "movie")]


async def test_a_coding_bug_in_the_lookup_is_not_mistaken_for_a_missing_filter():
    """Minor 3. ``listFilterChoices`` raising a ``TypeError`` (or any other
    non-plexapi exception) is a bug in this module's own call, not a fact
    about what the library supports -- so it must neither be memoised as one
    nor reported with the "Plex has no ... filter" wording that says it is.
    Narrowing the catch to plexapi's own ``NotFound``/``BadRequest`` (what
    ``listFilterChoices`` documents itself as raising) is what keeps a
    ``TypeError`` from being cached as "Plex has no 'genre' filter" for the
    rest of the pass."""
    section = FakeSection(raise_on={"genre": TypeError("boom")})
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(TypeError) as error:
        await PlexSearchBuilder().build(ctx)
    assert "boom" in str(error.value)
    assert "Plex has no" not in str(error.value)


async def test_a_transport_failure_in_the_lookup_is_wrapped_class_name_only():
    """Fix-round Carry 1. Narrowing ``_raw_choices``'s catch to
    ``(NotFound, BadRequest)`` (Minor 3, above) silently dropped the
    class-name-only wrap for a THIRD kind of failure: ``PlexServer.query``
    hands the request straight to a bare ``requests`` call, so a dropped
    connection is a ``requests.RequestException``, not a plexapi one, and
    reached the engine's logger with its own message -- which can carry a
    tokenised URL -- intact. Secrets hygiene, the same property
    ``test_a_plex_failure_is_reported_by_class_name_and_nothing_else`` pins
    for the ``fetchItems`` call, pinned here for the resolver's lookup too."""
    class Boom(requests.ConnectionError):
        def __str__(self):
            return "http://plex.example:32400/library?X-Plex-Token=SECRET"

    section = FakeSection(raise_on={"genre": Boom()})
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "Boom" in message
    assert "SECRET" not in message
    assert "X-Plex-Token" not in message


async def test_a_parse_error_in_the_lookup_is_wrapped_class_name_only():
    """Roadmap row 205. plexapi's ``utils.parseXMLString`` (utils.py:836-844,
    v4.18.2) catches the first ``ParseError`` only to retry ``fromstring`` on
    a cleaned string, and that retry is UNGUARDED -- a body that is not XML at
    all (a reverse proxy answering 200 with an HTML error page) fails the
    retry the same way, and the second ``ParseError`` -- a ``SyntaxError``
    subclass, in neither ``PlexApiException`` nor ``RequestException`` --
    escaped both catch tuples: the operator got a raw traceback instead of
    the named refusal, and the pass re-attempted a read that would fail again
    instead of memoising the failure."""
    section = FakeSection(
        raise_on={"genre": ElementTree.ParseError("syntax error: line 1, column 0")}
    )
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(PlexSearchUnavailable) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "ParseError" in message
    assert "syntax error" not in message


async def test_a_misspelled_tag_value_refuses_at_build_naming_value_and_attribute():
    """Roadmap row 158, answered on this path. Kometa raises
    ``Plex Error: genre: Horrror not found`` (builder.py:4433); before 9b this
    service compared case-insensitively at evaluation time instead, so a typo
    built an empty collection rather than failing."""
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(section, config={"all": {"genre": "Horrror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "Horrror" in message
    assert "genre" in message


async def test_the_lookup_matches_kometas_four_spellings():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    for written in ("Horror", "horror", "1138"):
        ctx = context(section, config={"all": {"genre": written}})
        await PlexSearchBuilder().build(ctx)
    assert all("genre=1138" in call for call in section.fetch_calls)


async def test_a_show_library_asks_the_rescoped_field_at_the_rescoped_libtype():
    section = FakeSection(choices={("resolution", "episode"): [FakeChoice("1080", "1080")]})
    ctx = context(section, library_type="Show", config={"all": {"resolution": "1080"}})
    await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("resolution", "episode")]
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=titleSort&episode.resolution=1080"
    ]


async def test_a_show_only_attribute_refuses_at_build_on_a_movie_library():
    section = FakeSection()
    ctx = context(section, config={"all": {"network": "HBO"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "libraries:" in str(error.value)


async def test_a_show_only_sort_refuses_at_build_on_a_movie_library():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(
        section,
        config={"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"},
    )
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "episode_added.desc" in str(error.value)


async def test_a_show_only_sort_builds_on_a_show_library_and_asks_nothing_first():
    """The other half of the gate. It refuses above and passes here, and the
    refusal costs no ``listFilterChoices`` at all -- the sort is checked before
    the first tag value is resolved, because a wrong-libtype sort is knowable
    without asking Plex anything."""
    section = FakeSection(choices={("genre", "show"): GENRES})
    ctx = context(
        section,
        library_type="Show",
        config={"all": {"genre": "Drama"}, "sort_by": "episode_added.desc"},
    )
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=episode.addedAt%3Adesc&show.genre=9"
    ]

    refused = FakeSection(choices={("genre", "movie"): GENRES})
    with pytest.raises(Exception):
        await PlexSearchBuilder().build(
            context(
                refused,
                config={"all": {"genre": "Drama"}, "sort_by": "episode_added.desc"},
            )
        )
    assert refused.filter_calls == []


async def test_a_music_library_refuses_by_name():
    """Minor 2. This gate now goes through the package's own
    ``require_library_type`` rather than a hand-rolled check, so the message
    is that helper's shared shape (``builds Movie or Show collections``)
    rather than a bespoke sentence."""
    from autoposter.collections.builders.base import LibraryTypeMismatch

    section = FakeSection()
    ctx = context(section, library_type="Artist", config={"all": {"genre": "Horror"}})
    with pytest.raises(LibraryTypeMismatch) as error:
        await PlexSearchBuilder().build(ctx)
    assert "Movie or Show" in str(error.value)


async def test_a_context_with_no_library_accessor_says_so():
    ctx = BuilderContext(
        library="Movies", library_type="Movie", config={"all": {"genre": "Horror"}}
    )
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "no library accessor" in str(error.value)


async def test_a_plex_failure_is_reported_by_class_name_and_nothing_else():
    """Secrets hygiene: a plexapi exception's message can carry a tokenised
    URL, so the refusal carries the class name and no other part of it."""
    class Boom(Exception):
        def __str__(self):
            return "http://plex.example:32400/library?X-Plex-Token=SECRET"

    section = FakeSection(choices={("genre", "movie"): GENRES})

    def explode(key):
        raise Boom()

    section.fetchItems = explode
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "Boom" in message
    assert "SECRET" not in message
    assert "X-Plex-Token" not in message


async def test_an_episode_builder_level_searches_at_type_four():
    """Search-tail E-2, and the whole row in one assertion: the definition's
    ``builder_level`` becomes the search's ``type=``, while the predicate is
    still scoped by the LIBRARY's kind (``episode.title``, not a bare
    ``title``). ``episode_title`` is a ``str`` row, so nothing here needs a tag
    lookup."""
    section = FakeSection()
    ctx = context(
        section, library_type="Show",
        config={"all": {"episode_title.begins": "Pilot"}},
        definition=SimpleNamespace(builder_level="episode"),
    )
    result = await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=4&sort=titleSort&episode.title%3C=Pilot"
    ]
    assert result.level == "episode"


async def test_a_season_builder_level_searches_at_type_three():
    section = FakeSection()
    ctx = context(
        section, library_type="Show",
        config={"all": {"episode_title.begins": "Pilot"}},
        definition=SimpleNamespace(builder_level="season"),
    )
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls[0].startswith("/library/sections/1/all?type=3&")


async def test_an_item_builder_level_is_byte_identical_to_before():
    """Gate-off byte-identity, stated as a test rather than claimed: a
    definition that writes nothing, and a context with no definition at all
    (a direct caller), both build the URL they built before E-2."""
    for definition in (None, SimpleNamespace(builder_level="item")):
        section = FakeSection(choices={("genre", "movie"): GENRES})
        ctx = context(
            section, config={"all": {"genre": "Horror"}}, definition=definition
        )
        result = await PlexSearchBuilder().build(ctx)
        assert section.fetch_calls == [
            "/library/sections/1/all?type=1&sort=titleSort&genre=1138"
        ]
        assert result.level == "item"


async def test_a_non_item_level_on_a_movie_library_refuses_inside_build():
    """The refusal has to live HERE, beside ``require_library_type``, and not
    in the engine: the engine's own "seasons and episodes exist only in a Show
    library" guard runs AFTER ``builder.build(ctx)``, so a Movie library with
    ``builder_level: episode`` would otherwise reach ``SORT_TYPES["episode"]``
    and send a ``type=4`` query at a movie section before anything refused."""
    section = FakeSection()
    ctx = context(
        section, library_type="Movie",
        config={"all": {"genre": "Horror"}},
        definition=SimpleNamespace(builder_level="episode"),
    )
    with pytest.raises(LibraryTypeMismatch) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "episode" in message and "Movie library" in message
    assert section.fetch_calls == [], "nothing may be asked of Plex before the refusal"


async def test_a_language_code_expands_to_every_variant_the_library_carries():
    section = FakeSection(choices={("audioLanguage", "movie"): [
        FakeChoice("Spanish", "es-419"),
        FakeChoice("Spanish (Mexico)", "es-MX"),
        FakeChoice("Spanish", "spa"),
        FakeChoice("English", "en"),
    ]})
    ctx = context(section, config={"all": {"audio_language": "es"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort"
        "&audioLanguage=es-419&and=1&audioLanguage=es-MX&and=1&audioLanguage=spa"
    ]


async def test_an_exact_language_value_targets_only_itself():
    section = FakeSection(choices={("audioLanguage", "movie"): [
        FakeChoice("Spanish", "es-419"),
        FakeChoice("Spanish (Mexico)", "es-MX"),
    ]})
    ctx = context(section, config={"all": {"audio_language": "es-419"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort&audioLanguage=es-419"
    ]


def test_the_roadmap_row_this_phase_closes_says_so_and_files_its_tail():
    """A closure note that only says "delivered" hides the scope it delivered
    at -- and hides the tail, which is the part the next reader needs."""
    import pathlib
    import re

    roadmap = pathlib.Path(
        "docs/design/2026-08-22-full-parity-roadmap.md"
    ).read_text(encoding="utf-8")
    by_number = {
        int(re.match(r"^\|\s*(\d+)\s*\|", line).group(1)): line
        for line in roadmap.splitlines()
        if re.match(r"^\|\s*(\d+)\s*\|", line)
    }

    # Row 101 closes, at a stated scope and with both proofs named.
    row = by_number[101]
    assert "**answered 9b (v1):** delivered" in row
    assert "19 of Kometa's 55 non-music search attributes ship" in row
    assert "fifteen pinned golden URIs" in row
    assert "9/9 agreement" in row

    # Row 96's arithmetic, corrected rather than restated. Every number here
    # was computed over the fetched Kometa v2.4.8 files, not recalled.
    row = by_number[96]
    assert "not a rounding difference" in row
    assert "are not nested" in row
    for number in ("**70**", "**55**", "**53**", "**26**", "**44**", "**29**"):
        assert number in row, number

    # Row 154 stays OPEN. The 651/651 agreement is exposure-zero, and the row
    # has to say so or the next reader cites it as clock agreement.
    row = by_number[154]
    assert "leaves it OPEN" in row
    assert "could not tell" in row

    # The tail this phase files rather than builds, contiguous and complete.
    numbered = sorted(by_number)
    assert [n for n in numbered if 169 <= n <= 183] == list(range(169, 184)), (
        "rows 169-183 are not contiguous"
    )
    # The nine per-family rows account for all 36 attributes v1 leaves undone.
    counts = {169: "**4**", 170: "**2**", 171: "**1**", 172: "**5**",
              173: "**20**", 174: "**1**", 175: "**1**", 176: "**1**",
              177: "**1**"}
    for number, count in counts.items():
        assert count in by_number[number], number
    assert sum(int(c.strip("*")) for c in counts.values()) == 36


# --- the public enumeration seam (phase 10a) ---------------------------------
#
# These go through the file's own ``FakeSection``/``context`` helpers rather
# than the second fake the plan's block sketched: the existing fake already
# carries both hooks the plan wanted added (``raise_on`` maps a field to the
# exception INSTANCE ``listFilterChoices`` raises, ``filter_calls`` is the
# call log), and a parallel fake would be exactly the drift the seam itself
# exists to avoid.


def test_choices_hands_back_every_key_and_title_the_library_reports():
    """The seam phase 10a's dynamic engine enumerates through. ``__call__``
    answers "which key is this written word"; this answers "what does this
    library HAVE", which is the question one-collection-per-value asks."""
    section = FakeSection(choices={("genre", "movie"): GENRES})
    resolver = LibraryTagResolver(context(section), section, "movie")

    assert resolver.choices("genre") == (("1138", "Horror"), ("9", "Drama"))


def test_choices_and_call_share_one_round_trip_per_pass():
    """The whole reason the seam lives on this class: a pass that enumerates a
    family AND resolves a written value for some other definition pays for one
    ``listFilterChoices`` between them, because both go through the same
    memoised ``_raw_choices``."""
    section = FakeSection(choices={("genre", "movie"): GENRES})
    resolver = LibraryTagResolver(context(section), section, "movie")

    resolver.choices("genre")
    resolver.choices("genre")
    assert resolver("genre", "Horror") == ("1138",)
    assert section.filter_calls == [("genre", "movie")]


def test_choices_asks_a_show_library_at_the_scope_the_row_names():
    """``field_for`` is what decides the libtype scope, so the three media
    attributes are enumerated at the EPISODE libtype on a show library --
    which is Kometa's own ``get_tags(f"episode.{field}")`` (plex.py:920-921)
    and is not a special case here, just the dotted field being split."""
    section = FakeSection()
    resolver = LibraryTagResolver(context(section, library_type="Show"), section, "show")

    resolver.choices("audio_language")
    resolver.choices("genre")
    assert section.filter_calls == [
        ("audioLanguage", "episode"), ("genre", "show"),
    ]


def test_choices_memoises_the_failure_like_every_other_lookup():
    """A dead filter is memoised as a failure, so a family of forty keys does
    not re-ask forty times -- ``BuilderContext.run_cache``'s own docstring
    requires it, and the seam gets it for free by going through
    ``_raw_choices``."""
    section = FakeSection(raise_on={"genre": NotFound("no such filter")})
    resolver = LibraryTagResolver(context(section), section, "movie")

    with pytest.raises(PlexSearchUnavailable) as first:
        resolver.choices("genre")
    with pytest.raises(PlexSearchUnavailable):
        resolver.choices("genre")
    assert section.filter_calls == [("genre", "movie")]
    assert "NotFound" in str(first.value)
    assert "no such filter" not in str(first.value)


def test_choices_answers_both_members_as_str():
    """Plex answers some keys as integers (``decade`` is the shipped example:
    ``choice.key`` is 1980 and ``choice.title`` is "1980s"). A caller matching
    an operator's written ``include:`` entry against these compares like with
    like only if both members arrive as ``str``."""
    section = FakeSection(choices={("decade", "movie"): [FakeChoice("1980s", 1980)]})
    resolver = LibraryTagResolver(context(section), section, "movie")

    assert resolver.choices("decade") == (("1980", "1980s"),)


# --- search tail E-1: the two enumeration special cases (roadmap row 173) -----
#
# Transcribed from a LIVE probe, not guessed: 2026-09-04, the operator's
# 'TV Shows' section, plexapi ``listFilterChoices``, read-only. ``actor``
# answers 787 choices at the show libtype and NotFound at the episode
# libtype; ``collection`` answers 11 at show, NotFound at season, and ZERO
# choices at episode. ``live_show_section`` reproduces exactly those five
# answers, so every test below runs against what the server actually does --
# and a resolver that regressed into asking Plex for a filter it lacks fails
# here with the NotFound the server would have raised.


def live_show_section():
    return FakeSection(
        choices={
            ("actor", "show"): [FakeChoice("Uma Thurman", "6")],
            ("collection", "show"): [FakeChoice("Pilots", "302")],
            ("collection", "episode"): [],
        },
        raise_on={
            ("actor", "episode"): NotFound("no such filter field"),
            ("collection", "season"): NotFound("no such filter field"),
        },
    )


async def test_episode_actor_is_enumerated_as_the_bare_actor_field_at_the_librarys_own_libtype():
    """Kometa's ``get_tags_translation = {"episode.actor": "actor"}``
    (plex.py:194, applied in ``get_search_choices`` at :1304): the ONE field
    whose enumeration is de-scoped to the library's own type, because Plex
    has no episode-level actor filter. Enumerate at ``show``, apply at the
    episode search level -- the URL term is still ``episode.actor=``."""
    section = live_show_section()
    ctx = context(section, library_type="Show", config={"all": {"episode_actor": "Uma Thurman"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=titleSort&episode.actor=6"
    ]
    assert section.filter_calls == [("actor", "show")]


async def test_episode_actor_and_actor_share_one_round_trip_per_pass():
    """Both rows enumerate the same ``(show, actor)`` listing, so the memo
    key is the same and a definition naming both pays for one call -- which
    is also Kometa's arithmetic, since both go through ``get_tags("actor")``."""
    section = live_show_section()
    ctx = context(
        section, library_type="Show",
        config={"all": {"actor": "Uma Thurman", "episode_actor": "Uma Thurman"}},
    )
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=titleSort&show.actor=6&and=1&episode.actor=6"
    ]
    assert section.filter_calls == [("actor", "show")]


async def test_episode_collection_is_enumerated_at_the_show_level_only():
    """Kometa's ``get_tags`` special case (plex.py:1360-1363): a key ending
    ``/collection?type=4`` is answered as the un-typed listing minus the
    type-4 and type-3 keys. On a show library that subtraction leaves the
    type-2 listing -- which is why the live probe found the episode listing
    EMPTY and the season one absent. Transcribed as its result: one call, at
    ``show``, and never the empty ``episode`` listing that would refuse
    every written value as unknown."""
    section = live_show_section()
    ctx = context(section, library_type="Show", config={"all": {"episode_collection": "Pilots"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=titleSort&episode.collection=302"
    ]
    assert section.filter_calls == [("collection", "show")]


async def test_season_collection_is_asked_at_the_season_scope_which_the_live_server_refuses():
    """The NON-override, pinned so the table above cannot quietly grow a third
    entry. Kometa splits ``season.collection`` and asks ``listFilters("season")``
    (plex.py:1347-1355); this server has no such filter, and Kometa answers
    ``plex_search attribute: season_collection not supported``. So does this
    service -- as the resolver's class-name-only wrap, memoised for the pass,
    with the server's own message kept out of it."""
    section = live_show_section()
    ctx = context(section, library_type="Show", config={"all": {"season_collection": "Pilots"}})
    with pytest.raises(PlexSearchUnavailable) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "season_collection" in message
    assert "NotFound" in message
    assert "no such filter field" not in message
    assert section.filter_calls == [("collection", "season")]
    assert section.fetch_calls == []


def test_the_enumeration_table_is_get_tags_translation_plus_the_documented_merge():
    """Exactly two entries: Kometa's one dict line (plex.py:194) and the
    result of its one ``get_tags`` special case (:1360-1363). Every key is a
    field some table row actually renders, so the table cannot name a scope
    nothing asks for."""
    from autoposter.collections.builders.plex_search import ENUMERATES_AS
    from autoposter.collections.filters import FILTER_ATTRIBUTES

    assert ENUMERATES_AS == {
        "episode.actor": "actor",
        "episode.collection": "collection",
    }
    rendered = {row.search_field for row in FILTER_ATTRIBUTES}
    for field, bare in ENUMERATES_AS.items():
        assert field in rendered, field
        assert "." not in bare, bare


def test_choices_for_episode_actor_is_the_show_level_actor_list():
    """The enumeration seam (10a's ``choices``) goes through the same
    ``_field_and_scope``, so a dynamic family over ``episode_actor`` would
    enumerate the show-level actors too, rather than asking a scope Plex
    refuses."""
    section = live_show_section()
    resolver = LibraryTagResolver(context(section, library_type="Show"), section, "show")

    assert resolver.choices("episode_actor") == (("6", "Uma Thurman"),)
    assert section.filter_calls == [("actor", "show")]


# --- search tail H: the FIELD discovery (roadmap row 176) ---------------------
#
# The repo's first ``listFilters`` call. Every other run-time Plex read in this
# stack is ``listFilterChoices`` -- a VALUE enumeration -- and this one asks
# which FIELDS the library has at all, for the one row whose field the table
# cannot hold. Its memo is its own key space, its refusal its own sentence.


def test_the_field_discovery_takes_the_filter_kometas_first_clause_matches():
    """``f.filter == "source"`` is Kometa's FIRST clause (plex.py:1291), and it
    fires on its own -- the title here folds to nothing like the attribute name,
    so a transcription that kept only the title clause fails.

    Not what THIS server answers: the 2026-09-06 probe found the key is
    ``location``, matched by the second clause. Which is exactly why the field
    is read rather than written down, and why both clauses are pinned."""
    section = FakeSection(filters={"movie": [
        FakeFilter("genre", "Genre"), FakeFilter("source", "Where It Came From"),
    ]})
    resolver = LibraryTagResolver(context(section), section, "movie")

    assert resolver.discover_field("folder_location", "movie") == "source"
    assert section.filter_type_calls == ["movie"]


def test_the_field_discovery_falls_back_to_the_folded_filter_title():
    """Kometa's SECOND clause: the displayed title lowercased with spaces turned
    to underscores (plex.py:1291). This is THIS server's answer -- the probe
    found ``location``/``Folder Location``, which the first clause does not
    match at all -- so a transcription that kept only the first would refuse the
    very library row 176 was filed for."""
    section = FakeSection(filters={"movie": FOLDER_FILTERS})
    resolver = LibraryTagResolver(context(section), section, "movie")

    assert resolver.discover_field("folder_location", "movie") == "location"


def test_the_field_discovery_forces_the_episode_scope_on_a_show_library():
    """Kometa's re-scope (plex.py:1288-1289, :1297): Plex exposes no folder
    filter above the episode, so a show library is asked at the EPISODE libtype
    and the answer comes back PREFIXED. The prefix does double duty -- it is the
    URL term and the enumeration scope -- so this one assertion is also what
    makes ``_field_and_scope`` enumerate the values at the right level."""
    section = FakeSection(filters={"episode": FOLDER_FILTERS})
    resolver = LibraryTagResolver(context(section, library_type="Show"), section, "show")

    assert resolver.discover_field("folder_location", "show") == "episode.location"
    assert section.filter_type_calls == ["episode"]


def test_the_field_discovery_forces_the_episode_scope_when_the_search_type_already_is_one():
    """Task 2 review, Minor 3. The m-3 resolution's third row: a show library
    whose SEARCH type is already ``episode`` (``builder_level: episode``) asks
    ``listFilters("episode")`` and answers the SAME prefixed field as row 2's
    item-level show search -- ``is_show and filter_type == "show"`` is false
    here (``filter_type`` is already ``"episode"``, not re-scoped from
    ``"show"``), so this is the one reachable case where the re-scope
    condition being edited would silently stop prefixing rather than fail
    loudly. Behaviour was already correct; only the assertion was missing."""
    section = FakeSection(filters={"episode": FOLDER_FILTERS})
    resolver = LibraryTagResolver(
        context(section, library_type="Show"), section, "show", search_type="episode"
    )

    assert resolver.discover_field("folder_location", "show") == "episode.location"
    assert section.filter_type_calls == ["episode"]


def test_the_field_discovery_asks_the_season_libtype_as_it_stands():
    """``builder_level: season`` is the ONE case where the search type and the
    library kind give different answers: Kometa re-scopes ``show`` and asks
    ``season`` as-is (plex.py:1287-1289), and lets Plex's silence raise. Mirrored
    rather than refused at parse time, because whether a season libtype carries a
    folder filter is a property of the server, not of the config."""
    section = FakeSection(filters={"episode": FOLDER_FILTERS})
    resolver = LibraryTagResolver(
        context(section, library_type="Show"), section, "show", search_type="season"
    )

    with pytest.raises(PlexSearchUnavailable) as error:
        resolver.discover_field("folder_location", "show")
    assert section.filter_type_calls == ["season"]
    assert "season" in str(error.value)


def test_the_field_discovery_is_one_schema_read_for_the_whole_pass():
    """Its own memo key (``plex_search:field:{library}:{filter_type}:
    {attribute}``), not ``_raw_choices``'s: a different call, a different key
    space, a different sentence. Two definitions naming the attribute pay for
    one read."""
    section = FakeSection(
        filters={"movie": FOLDER_FILTERS},
        choices={("location", "movie"): [FakeChoice("/mnt/media/Movies", "1")]},
    )
    ctx = context(section)
    resolver = LibraryTagResolver(ctx, section, "movie")

    assert resolver.discover_field("folder_location", "movie") == "location"
    assert resolver.discover_field("folder_location", "movie") == "location"
    assert resolver("folder_location", "/mnt/media/Movies") == ("1",)
    assert section.filter_type_calls == ["movie"]
    assert ctx.run_cache["plex_search:field:Movies:movie:folder_location"] == "location"


def test_the_field_memo_key_is_scoped_by_attribute_not_shared_across_them():
    """Task 2 review, Important 1. The key used to be
    ``plex_search:field:{library}:{filter_type}`` -- naming only the library
    and the (post-re-scope) filter type, never the attribute -- while both the
    answer and the row-213 refusal are attribute-dependent (the match clause,
    the ``NotFound``/``BadRequest`` wrap and the refusal sentence all read
    ``attribute``). Two rows sharing a library and a filter type would
    therefore share a cache entry: the second row's ``discover_field`` call
    would be served the FIRST row's answer, or its memoised refusal naming the
    FIRST row's attribute, without ever asking Plex about the second. Only one
    ``DISCOVERED`` row exists in the shipped table, so this parametrises the
    resolver directly (``discover_field`` takes ``attribute`` as an argument
    and needs no second table row to expose the bug) rather than adding a fake
    row to it."""
    section = FakeSection(filters={"movie": FOLDER_FILTERS})
    resolver = LibraryTagResolver(context(section), section, "movie")

    assert resolver.discover_field("folder_location", "movie") == "location"

    with pytest.raises(PlexSearchUnavailable) as error:
        resolver.discover_field("some_other_attribute", "movie")
    message = str(error.value)
    assert "some_other_attribute" in message
    assert "folder_location" not in message

    # And the first row's own cached answer is untouched by the second's miss.
    assert resolver.discover_field("folder_location", "movie") == "location"


def test_a_library_with_no_folder_filter_refuses_by_name_and_memoises_the_miss():
    """Roadmap row 213's law and ``BuilderContext.run_cache``'s own requirement
    at once. The sentence names the attribute and a libtype token from a CLOSED
    set and nothing else -- Kometa's own message dumps the server's whole filter
    schema into it (``available_filters``, plex.py:1294-1295) and must not be
    copied. The miss is memoised beside the successes, or a dead lookup is
    re-made once per definition."""
    section = FakeSection(filters={"movie": [FakeFilter("genre", "Genre")]})
    resolver = LibraryTagResolver(context(section), section, "movie")

    with pytest.raises(PlexSearchUnavailable) as first:
        resolver.discover_field("folder_location", "movie")
    with pytest.raises(PlexSearchUnavailable):
        resolver.discover_field("folder_location", "movie")

    assert section.filter_type_calls == ["movie"]
    message = str(first.value)
    assert "folder_location" in message
    assert "movie" in message
    assert "libraries:" in message
    assert "genre" not in message, "the server's filter schema must not be served"


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (NotFound("no such libtype"), "no folder filter"),
        (requests.ConnectionError("connection reset by 10.0.0.1?X-Plex-Token=abc"),
         "would not answer"),
    ],
    ids=["not-found", "unreachable"],
)
def test_the_field_discovery_wraps_a_plex_failure_by_class_name_only(error, fragment):
    """The same two-clause split ``_raw_choices`` makes, for the same two
    reasons: ``NotFound``/``BadRequest`` is "this library has no such filter
    list" and is a fact about the LIBRARY, while a transport failure or an
    unparseable body is "Plex did not answer" and is not. Both are wrapped
    class-name-only, because either message can carry a tokenised URL."""
    section = FakeSection(raise_filters={"movie": error})
    resolver = LibraryTagResolver(context(section), section, "movie")

    with pytest.raises(PlexSearchUnavailable) as raised:
        resolver.discover_field("folder_location", "movie")
    message = str(raised.value)
    assert fragment in message
    assert type(error).__name__ in message
    assert "X-Plex-Token" not in message
    assert str(error) not in message


async def test_the_builder_sends_a_discovered_folder_field_in_its_query():
    """The builder's own entry point, end to end: params -> discovery -> URL ->
    ``fetchItems``. ``ENUMERATES_AS`` gains no entry for this field and must
    not: its two rows DE-scope a dotted field, and this prefix exists to FORCE
    a scope."""
    from autoposter.collections.builders.plex_search import ENUMERATES_AS

    section = FakeSection(
        filters={"movie": FOLDER_FILTERS},
        choices={("location", "movie"): [FakeChoice("/mnt/media/Movies", "1")]},
    )
    ctx = context(section, config={"all": {"folder_location": "/mnt/media/Movies"}})

    await PlexSearchBuilder().build(ctx)

    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort&location=1"
    ]
    assert "episode.location" not in ENUMERATES_AS
    assert section.filter_calls == [("location", "movie")]
