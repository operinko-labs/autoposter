"""``plex_search`` -- the builder, its params, and its refusals.

The URL itself is proven elsewhere (``tests/test_collection_search_oracle.py``,
byte-identical against Kometa). What is proven here is the surface an operator
touches: which spellings load, which refuse and what they say, what the builder
asks Plex, and how many times it asks.
"""
import pytest
import requests
from plexapi.exceptions import NotFound
from pydantic import ValidationError

from autoposter.collections.builders.base import BuilderContext, SourceClients
from autoposter.collections.builders.plex_search import (
    PlexSearchParams,
    PlexSearchBuilder,
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
    kometa_build_filter.py:953), reproduced alongside the blank one."""
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


def test_a_search_regex_refuses_at_load_pointing_at_filters():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"studio.regex": "pictures$"}})
    assert "filters:" in str(error.value)


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


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeSection:
    """Counts what it was asked, so 'one lookup per pass' is a measurement.

    ``raise_on`` maps a field name to the exception INSTANCE
    ``listFilterChoices`` should raise for it -- an instance, not a class,
    because Minor 3's narrowing test needs to raise something that is not a
    plexapi error at all (a plain ``TypeError``, standing in for a bug in our
    own code) alongside cases that are.
    """

    key = 1

    def __init__(self, choices=None, items=None, raise_on=None):
        self._choices = choices or {}
        self._items = items or [FakeItem(11), FakeItem(12)]
        self._raise_on = dict(raise_on or {})
        self.filter_calls = []
        self.fetch_calls = []

    def listFilterChoices(self, field, libtype=None):
        self.filter_calls.append((field, libtype))
        if field in self._raise_on:
            raise self._raise_on[field]
        return self._choices.get((field, libtype), [])

    def fetchItems(self, key):
        self.fetch_calls.append(key)
        return self._items


def context(section, *, library_type="Movie", config=None, run_cache=None):
    return BuilderContext(
        library="Movies",
        library_type=library_type,
        config=config or {},
        run_cache=run_cache if run_cache is not None else {},
        sources=SourceClients(plex=PlexSectionAccess(section, lambda: {})),
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
