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
