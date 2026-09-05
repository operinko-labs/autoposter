"""``search_url`` -- one term per value type, and the assembly around them.

These are unit tests over Kometa's branches, not the oracle. The oracle
(``tests/test_collection_search_oracle.py``) is what proves the whole URL right;
this file is what says WHICH branch broke when it does.
"""
import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import (
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)

# The library's tag vocabulary, as a fixture. Real Plex keys are opaque
# integers for most families and the value itself for a few (resolution,
# contentRating, the languages) -- both shapes are represented so a renderer
# that assumed one would fail here.
CHOICES = {
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("label", "Overlay"): ("3",),
    ("collection", "Marvel"): ("77",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
    # Search-tail E-1's five tag rows. Keyed by the ROW NAME, which is what
    # ``_arguments`` hands the resolver (``resolve_tag(row.name, value)``),
    # so ``episode_actor`` and ``actor`` are separate entries here even
    # though the real resolver answers both from one ``actor`` listing (T2).
    ("season_collection", "Specials"): ("301",),
    ("season_label", "Overlay"): ("3",),
    ("episode_collection", "Pilots"): ("302",),
    ("episode_label", "Overlay"): ("3",),
    ("episode_actor", "Uma Thurman"): ("6",),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def url(raw, *, libtype="movie", base="all", **kwargs):
    group = parse_filters(raw, field="params", searching=True, base=base)
    return build_search_url(group, libtype=libtype, resolve_tag=resolve, **kwargs)


def test_a_single_tag_term_carries_the_resolved_key_not_the_written_word():
    """Kometa sends the KEY for a tag in a search (validate_attribute's
    ``title=not plex_search``, builder.py:4412) -- the written word is only
    ever the lookup input."""
    assert url({"genre": "Horror"}) == "?type=1&sort=titleSort&genre=1138"


def test_a_multi_value_tag_joins_with_the_BLOCKS_conjunction():
    """Not with a fixed OR. Under ``all:`` a list is an AND
    (builder.py:4248) -- which is the single most surprising thing in this
    grammar and is Kometa's, not ours."""
    assert url({"content_rating": ["PG-13", "R"]}) == (
        "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
    )
    assert url({"content_rating": ["PG-13", "R"]}, base="any") == (
        "?type=1&sort=titleSort&push=1&contentRating=5&or=1&contentRating=7&pop=1"
    )


def test_a_language_expansion_becomes_one_term_per_variant():
    assert url({"audio_language": "es"}) == (
        "?type=1&sort=titleSort&audioLanguage=es-419&and=1&audioLanguage=es-MX"
        "&and=1&audioLanguage=spa"
    )


def test_an_unresolvable_tag_names_the_value_and_the_attribute():
    with pytest.raises(TagValueNotFound) as error:
        url({"genre": "Horrror"})
    message = str(error.value)
    assert "Horrror" in message
    assert "genre" in message


# --- .regex vocabulary expansion (roadmap row 178) ---------------------------
#
# A separate resolver from ``resolve``/``CHOICES`` above: those existing 30+
# tests pass a bare function with only ``__call__``, and nothing here changes
# that -- ``choices`` is only ever invoked for a ``.regex`` predicate, which
# no pre-existing test writes.

VOCAB = {
    "genre": (("1138", "Horror"), ("9", "Drama"), ("55", "Sci-Fi Horror")),
    "studio": (("12", "A24"), ("34", "Studio Ghibli"), ("56", "Warner Bros.")),
}


class _RegexResolver:
    def __call__(self, attribute, value, /):
        return CHOICES.get((attribute, value), ())

    def choices(self, attribute, /):
        return VOCAB.get(attribute, ())


def regex_url(raw, *, libtype="movie", base="all", **kwargs):
    group = parse_filters(raw, field="params", searching=True, base=base)
    return build_search_url(
        group, libtype=libtype, resolve_tag=_RegexResolver(), **kwargs
    )


def test_a_regex_search_sends_every_matching_titles_key():
    assert regex_url({"genre.regex": "Horror"}) == (
        "?type=1&sort=titleSort&genre=1138&and=1&genre=55"
    )


def test_a_regex_search_is_case_sensitive_like_filters_regex_is():
    """SETTLED-BY-ORACLE for ``filters:``'s own ``.regex`` (``_as_regex``,
    filters.py:1349-1373) -- the search-side expansion reuses the same
    compiled pattern with no flags, so the two stay consistent with each
    other even though they are different mechanisms. A lower-case pattern
    against the title-cased vocabulary (``"Horror"``, ``"Studio Ghibli"``)
    matches nothing, which is the same "no keys resolved" refusal an
    unmatched pattern gets anywhere else in this file."""
    with pytest.raises(TagValueNotFound):
        regex_url({"genre.regex": "^horror$"})


def test_a_regex_search_on_studio_matches_against_the_title_not_the_key():
    assert regex_url({"studio.regex": "^Studio"}) == (
        "?type=1&sort=titleSort&studio=34"
    )


def test_an_unmatched_regex_search_pattern_names_the_pattern():
    with pytest.raises(TagValueNotFound) as error:
        regex_url({"genre.regex": "^Nothing Matches This$"})
    assert "Nothing Matches This" in str(error.value)
    assert "genre" in str(error.value)


def test_a_regex_search_with_no_vocabulary_for_the_attribute_names_it():
    with pytest.raises(TagValueNotFound):
        regex_url({"resolution.regex": "1080"})


def test_a_string_value_is_quoted_and_a_tag_value_is_not():
    assert url({"studio.begins": "Warner Bros"}) == (
        "?type=1&sort=titleSort&studio%3C=Warner%20Bros"
    )
    assert url({"studio.not": "Hallmark & Co"}) == (
        "?type=1&sort=titleSort&studio!=Hallmark%20%26%20Co"
    )


def test_a_bare_string_is_a_contains_with_no_modifier_at_all():
    assert url({"studio": "A24"}) == "?type=1&sort=titleSort&studio=A24"


def test_an_int_range_uses_the_encoded_comparison():
    assert url({"year.gte": 2000}) == "?type=1&sort=titleSort&year%3E=2000"
    assert url({"year.lt": 1980}) == "?type=1&sort=titleSort&year%3C%3C=1980"


def test_a_float_renders_as_a_float_including_the_trailing_zero():
    """``util.parse(datatype="float")`` is ``float(str(value))``
    (util.py:861), so Kometa sends ``8.0`` for a written ``8``. Matching that
    exactly is the difference between an identical URL and a similar one."""
    assert url({"critic_rating.gte": 8}) == "?type=1&sort=titleSort&rating%3E=8.0"


def test_a_duration_range_is_minutes_times_sixty_thousand_as_a_float():
    assert url({"duration.gt": 90}) == (
        "?type=1&sort=titleSort&duration%3E%3E=5400000.0"
    )


def test_a_relative_date_window_is_negative_and_carries_its_unit():
    assert url({"added": 30}) == "?type=1&sort=titleSort&addedAt%3E%3E=-30d"
    assert url({"added.not": 30}) == "?type=1&sort=titleSort&addedAt%3C%3C=-30d"


def test_the_months_unit_is_rewritten_to_mon_on_the_wire():
    """Kometa writes ``o`` in the config and ``mon`` on the wire
    (builder.py:4226-4227) -- Plex's spelling, not Kometa's."""
    assert url({"release.not": "6o"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3C%3C=-6mon"
    )
    # every other unit passes through unchanged
    assert url({"release": "2y"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3E%3E=-2y"
    )


def test_an_absolute_date_is_iso_whichever_way_it_was_written():
    assert url({"added.before": "12/25/2020"}) == (
        "?type=1&sort=titleSort&addedAt%3C%3C=2020-12-25"
    )


def test_rated_puts_the_negation_on_the_argument_not_the_modifier():
    assert url({"critic_rating.rated": True}) == "?type=1&sort=titleSort&rating!=-1"
    assert url({"critic_rating.rated": False}) == "?type=1&sort=titleSort&rating=-1"


def test_a_boolean_puts_the_negation_on_the_argument_too():
    assert url({"unplayed": True}) == "?type=1&sort=titleSort&unwatched=1"
    assert url({"progress": False}) == "?type=1&sort=titleSort&inProgress!=1"


def test_a_show_library_gets_the_rescoped_fields():
    assert url({"genre": "Drama"}, libtype="show") == (
        "?type=2&sort=titleSort&show.genre=9"
    )
    assert url({"resolution": "1080"}, libtype="show") == (
        "?type=2&sort=titleSort&episode.resolution=1080"
    )
    assert url({"added.after": "2024-01-01"}, libtype="show") == (
        "?type=2&sort=titleSort&show.addedAt%3E%3E=2024-01-01"
    )


def test_a_movie_only_attribute_refuses_on_a_show_library():
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    with pytest.raises(SearchAttributeNotAvailable) as error:
        url({"duration.gt": 90}, libtype="show")
    message = str(error.value)
    assert "duration" in message
    assert "libraries:" in message


def test_a_show_only_attribute_refuses_on_a_movie_library():
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    with pytest.raises(SearchAttributeNotAvailable):
        url({"network": "HBO"}, libtype="movie")


def test_a_mapping_shaped_nesting_gets_one_push_pop_pair():
    assert url({"year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8}}) == (
        "?type=1&sort=titleSort&year%3E=2000&and=1&push=1&studio=A24&and=1"
        "&rating%3E=8.0&pop=1"
    )


def test_a_list_shaped_nesting_gets_one_pair_per_element_joined_by_the_parent():
    """The nesting divergence, at the byte level. Each element carries the
    WRITTEN key's conjunction inside its own push/pop, and the elements are
    joined by the CONTAINING block's -- so an ``any:`` list inside an ``all:``
    base is ANDed at the top and ORed inside. builder.py:4207-4217."""
    assert url({
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }) == (
        "?type=1&sort=titleSort&contentRating=5&and=1"
        "&push=1&studio=A24&or=1&year%3E=2020&pop=1"
        "&and=1&push=1&genre=1138&pop=1"
    )


def test_an_any_base_wraps_the_whole_body():
    """builder.py:4288 -- an ``all`` base has its trailing ``&`` stripped, an
    ``any`` base is wrapped in a push/pop instead."""
    assert url({"genre": "Horror", "studio": "A24"}, base="any") == (
        "?type=1&sort=titleSort&push=1&genre=1138&or=1&studio=A24&pop=1"
    )


def test_the_limit_sits_between_the_type_and_the_sort():
    assert url({"year.gte": 2010}, limit=100, sort_by=["critic_rating.desc", "title.asc"]) == (
        "?type=1&limit=100&sort=rating%3Adesc%2CtitleSort&year%3E=2010"
    )


def test_a_zero_limit_emits_no_limit_at_all_which_is_kometas_test():
    """``if limit``, not ``if limit is not None`` (builder.py:4289). The params
    model refuses ``limit: 0`` before this module ever sees one, so the only
    way here is a direct call -- and the byte this used to emit, ``limit=0&``,
    is one Kometa never sends and Plex answers with nothing."""
    assert url({"year.gte": 2010}, limit=0) == "?type=1&sort=titleSort&year%3E=2010"
    assert url({"year.gte": 2010}, limit=None) == "?type=1&sort=titleSort&year%3E=2010"


def test_a_wrong_libtype_sort_refuses_here_rather_than_in_sort_argument():
    """``sort_argument`` indexes the libtype's table directly, so before this
    gate was wired an ``episode_added.desc`` against a movie library reached
    the engine as a bare ``KeyError`` -- a dead source with no explanation.
    Tested at THIS layer and not only at the builder's, because the builder is
    not the only caller and the guard has to hold for the others."""
    from autoposter.collections.search_sorts import SortNotAvailable

    with pytest.raises(SortNotAvailable) as error:
        url({"genre": "Horror"}, sort_by=["episode_added.desc"])
    message = str(error.value)
    assert "episode_added.desc" in message
    assert "libraries:" in message

    assert url({"genre": "Drama"}, libtype="show", sort_by=["episode_added.desc"]) == (
        "?type=2&sort=episode.addedAt%3Adesc&show.genre=9"
    )


def test_no_built_url_ever_carries_includeCollections():
    """Roadmap Notes-for-9b item 1. The name reads as 'also send each item's
    <Collection> children'; what it actually does is MIX Collection objects
    into the result set, changing what the query returns. 9a's probe hit it
    while trying to rescue the ``collection`` attribute. Nothing in this
    module may reach for it, and this test is the standing guard."""
    built = [
        url({"collection": "Marvel"}),
        url({"collection": "Marvel"}, libtype="show"),
        url({"genre": "Horror", "any": [{"studio": "A24"}, {"label": "Overlay"}]}),
    ]
    for one in built:
        assert "includeCollections" not in one
        assert "include" not in one


def test_a_group_with_no_terms_raises_rather_than_building_an_empty_query():
    """Defensive: the parser refuses an empty block, so this is unreachable
    from a config. Left in because the alternative -- returning
    ``?type=1&sort=titleSort&`` -- is a query that means THE WHOLE LIBRARY,
    which is the single worst thing a broken filter could quietly become."""
    from autoposter.collections.filters import FilterGroup

    empty = FilterGroup(op="all", children=(), field="params")
    with pytest.raises(SearchProducedNothing):
        build_search_url(empty, libtype="movie", resolve_tag=resolve)


def test_a_filters_parsed_tree_refuses_at_the_relative_window_rather_than_crashing():
    """The one way a caller can hand this module a value it cannot render: a
    bare date parsed with ``searching=False`` is a plain int of days, not a
    ``RelativeWindow``. This used to be an ``assert`` -- stripped under
    ``python -O``, and reading as an internal invariant rather than as the
    caller error it is."""
    group = parse_filters({"added": 30}, field="filters")
    with pytest.raises(TypeError) as error:
        build_search_url(group, libtype="movie", resolve_tag=resolve)
    message = str(error.value)
    assert "searching=True" in message
    assert "filters.added" in message


# --- search tail E-1: the twenty show-only rows (roadmap row 173) -------------
#
# One render per row, on a SHOW library, against the string Kometa's
# ``build_filter`` produces for the same key -- derived from the vendored
# driver's branches and pinned all-at-once by oracle config 22. Each case
# exercises the row's most distinctive operator: the tag rows through
# resolution (``.not`` on one of them, ``!`` against the resolved key), the
# string row through ``.begins`` (``%3C``), the three dates through the bare
# window, ``.after`` and ``.not``, ``episode_plays`` through the plain-int
# range, the three floats through ``.gte``/``.lt``/``.rated``,
# ``episode_year`` through ``.gte``, and every boolean both ways.
FAMILY_E_RENDERS = [
    ({"season_collection": "Specials"}, "season.collection=301"),
    ({"season_label": "Overlay"}, "season.label=3"),
    ({"episode_collection": "Pilots"}, "episode.collection=302"),
    ({"episode_label.not": "Overlay"}, "episode.label!=3"),
    ({"episode_title.begins": "Pilot"}, "episode.title%3C=Pilot"),
    ({"episode_actor": "Uma Thurman"}, "episode.actor=6"),
    ({"episode_added": 30}, "episode.addedAt%3E%3E=-30d"),
    ({"episode_air_date.after": "2024-01-01"}, "episode.originallyAvailableAt%3E%3E=2024-01-01"),
    ({"episode_last_played.not": "2y"}, "episode.lastViewedAt%3C%3C=-2y"),
    ({"episode_plays.gt": 3}, "episode.viewCount%3E%3E=3"),
    ({"episode_user_rating.gte": 7}, "episode.userRating%3E=7.0"),
    ({"episode_critic_rating.lt": 5}, "episode.rating%3C%3C=5.0"),
    ({"episode_audience_rating.rated": True}, "episode.audienceRating!=-1"),
    ({"episode_year.gte": 2010}, "episode.year%3E=2010"),
    ({"episode_unplayed": True}, "episode.unwatched=1"),
    ({"episode_duplicate": False}, "episode.duplicate!=1"),
    ({"episode_progress": True}, "episode.inProgress=1"),
    ({"episode_unmatched": False}, "episode.unmatched!=1"),
    ({"show_unmatched": False}, "show.unmatched!=1"),
    ({"unplayed_episodes": True}, "show.unwatchedLeaves=1"),
]


@pytest.mark.parametrize(
    ("raw", "term"), FAMILY_E_RENDERS,
    ids=[next(iter(raw)) for raw, _ in FAMILY_E_RENDERS],
)
def test_a_family_e_row_renders_at_the_show_level_as_kometa_renders_it(raw, term):
    """``type=2`` and the show default sort, then the term: a show library
    searched for shows HAVING a matching episode or season, which is what
    Kometa's ``build_filter`` emits for these keys under a show collection
    (``sort_type = builder_level = "show"``, builder.py:4118-4119). Collecting
    the episodes themselves is E-2's selector."""
    assert url(raw, libtype="show") == f"?type=2&sort=titleSort&{term}"


def test_every_family_e_row_refuses_on_a_movie_library_naming_the_kind():
    """Kometa refuses nineteen of the twenty by name on a movie library
    (``is_movie and final_attr in show_only_searches``,
    kometa_build_filter.py:905) rather than sending a query a library with no
    episodes answers with nothing; ``episode_actor`` it would send, and this
    table refuses it too -- a DECLARED DIVERGENCE, argued on its row note and
    pinned here so it stays deliberate. All twenty refuse before any tag
    lookup: the kind gate in ``_render_predicate`` runs ahead of
    ``resolve_tag``."""
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    for raw, _ in FAMILY_E_RENDERS:
        with pytest.raises(SearchAttributeNotAvailable) as error:
            url(raw, libtype="movie")
        message = str(error.value)
        assert next(iter(raw)).split(".")[0] in message
        assert "show" in message
        assert "libraries:" in message
