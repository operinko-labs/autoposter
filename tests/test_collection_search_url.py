"""``search_url`` -- one term per value type, and the assembly around them.

These are unit tests over Kometa's branches, not the oracle. The oracle
(``tests/test_collection_search_oracle.py``) is what proves the whole URL right;
this file is what says WHICH branch broke when it does.
"""
import datetime as dt

import pytest

from autoposter.collections.filters import evaluate, parse_filters
from autoposter.collections.search_sorts import EPISODE_SORTS, SEASON_SORTS
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
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
    # though the real resolver answers both from one ``actor`` listing.
    ("season_collection", "Specials"): ("301",),
    ("season_label", "Overlay"): ("3",),
    ("episode_collection", "Pilots"): ("302",),
    ("episode_label", "Overlay"): ("3",),
    ("episode_actor", "Uma Thurman"): ("6",),
    # Roadmap row 176. Keyed by the ROW name like every other entry; the
    # discovered FIELD is a separate answer, from ``discover_field`` below.
    ("folder_location", "/mnt/media/Movies"): ("1",),
    ("folder_location", "/mnt/media/TV"): ("2",),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def discover_field(attribute, libtype, /):
    """``TagResolver``'s third member as a fixture (roadmap row 176).

    The live resolver reads this from ``listFilters``; here it is the answer the
    2026-09-06 probe recorded (``location``, not the plan's placeholder
    ``source``), with Kometa's own show-library re-scope applied
    (``episode.<field>``, plex.py:1288-1297). A plain function attribute rather
    than a class, because ``resolve`` is a function everywhere else in this file
    and every other test would otherwise change shape for one row.

    Two arguments, not three: the SEARCH type is resolver STATE in production
    (``LibraryTagResolver(..., search_type=...)``), not a per-call argument, and
    a bare function fixture has nowhere to hold one. Every ``url(...)`` call
    below that names ``folder_location`` leaves ``search_type`` at its default,
    so the search type and the library kind agree and this two-argument answer
    is the one the live resolver gives (round-1 review m-3).
    """
    return "episode.location" if libtype == "show" else "location"


resolve.discover_field = discover_field


def url(raw, *, libtype="movie", search_type=None, base="all", **kwargs):
    group = parse_filters(raw, field="params", searching=True, base=base)
    return build_search_url(
        group, libtype=libtype, search_type=search_type, resolve_tag=resolve, **kwargs
    )


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


def test_the_inclusive_date_pair_renders_as_plexs_single_angle_wire_string():
    """Roadmap row 157's wire half. ``%3E`` and ``%3C`` are the SINGLE-angle
    strings -- the ones Kometa reaches from ``.gte``/``.lte`` on an int, a
    float or a duration, and from ``.ends``/``.begins`` on a string. Plex
    honours them on a date field too, which was measured rather than assumed:
    a read-only probe of the production server on 2026-09-08 over a 1963-item
    movie section returned 101 for ``originallyAvailableAt%3E=D``, 99 for the
    strict ``%3E%3E=D`` and 2 for ``=D`` (99 + 2 = 101), and 1864 / 1862 / 2
    the same way on the other edge. The probe's own nonsense control
    (``notAFieldAtAll%3E=8``) returned the whole 1963, so an unhonoured
    modifier would have shown up as 1963 and did not.

    THE ONE EXCEPTION, and it is the whole of ``search_url``'s edit for this
    row: ``.to`` on a MOMENT row renders as the STRICT ``%3C%3C=`` at the day
    AFTER the written one. Plex reads a bare date on ``addedAt`` as that day's
    MIDNIGHT -- the probe measured ``addedAt%3C=A`` and ``addedAt%3C%3C=A`` at
    the same 1862, both excluding everything added during day A -- so ``%3C=A``
    would be "at or before A 00:00" and would drop an item added at 09:15 that
    day. ``.to`` means the whole calendar day, so the wire boundary is the
    start of A+1 and the comparison is strict. The last assertion below is
    that difference, in one line: same operator, same value, two field types,
    two different wire strings.
    """
    assert url({"release.from": "2000-01-01"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3E=2000-01-01"
    )
    assert url({"release.to": "12/25/2020"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3C=2020-12-25"
    )
    assert url({"added.from": "2026-01-10"}) == (
        "?type=1&sort=titleSort&addedAt%3E=2026-01-10"
    )
    assert url({"added.to": "2026-01-10"}) == (
        "?type=1&sort=titleSort&addedAt%3C%3C=2026-01-11"
    )
    assert url({"added.from": "2024-01-01"}, libtype="show") == (
        "?type=2&sort=titleSort&show.addedAt%3E=2024-01-01"
    )
    assert url({"added.to": "2024-12-31"}, libtype="show") == (
        "?type=2&sort=titleSort&show.addedAt%3C%3C=2025-01-01"
    )
    assert url({"last_played.to": "2026-02-28"}) == (
        "?type=1&sort=titleSort&lastViewedAt%3C%3C=2026-03-01"
    )


# Roadmap row 157's acceptance. A same-name-different-filter defect is this
# row's named failure: the pair is one spelling reaching two independent
# implementations -- a python comparison in ``filters._matches_one`` and a
# query parameter Plex evaluates -- and the two agreeing everywhere EXCEPT on
# the boundary is exactly the bug an inclusive-boundary operator would have.
# So the boundary is what is asserted, once per FIELD TYPE, because the two
# field types put the item's own value in different places: a date-only field
# is always midnight, a moment field carries a time of day.
BOUNDARY_NOW = dt.datetime(2026, 9, 8, 12, 0)


def test_the_from_to_boundary_agrees_between_the_two_halves_on_a_date_only_field():
    """``release`` -> ``originallyAvailableAt``: Plex stores a bare date, so
    ``_as_moment`` reads BOTH sides as that day's midnight and the boundary
    day is inside both operators.

    The wire half means the same thing, measured: on the probe's boundary
    ``D = 2025-09-04``, ``originallyAvailableAt=D`` returned 2 items,
    ``%3E%3E=D`` (strict after) returned 99 and ``%3E=D`` returned 101 -- the
    strict set PLUS the two items dated D. On the other edge ``%3C%3C=D``
    returned 1862 and ``%3C=D`` returned 1864, the same two items. So an item
    dated exactly D is INCLUDED by both wire predicates, which is what the
    client evaluation below says too.

    NOT covered here: ``.from: today`` disagrees between the two halves by up
    to 24 hours, because the client compares against ``now`` (a moment) while
    the wire compares against ``now.date()`` (a midnight) -- inherited from
    the pre-existing ``_Today`` convention (``.after: today`` has the same
    split) and out of scope for roadmap row 157 (review Important 3).
    """
    on_the_boundary = {"release": dt.date(2024, 1, 1)}
    the_day_before = {"release": dt.date(2023, 12, 31)}
    the_day_after = {"release": dt.date(2024, 1, 2)}

    # client half: the boundary day is IN, on both operators
    after = parse_filters({"release.from": "2024-01-01"})
    before = parse_filters({"release.to": "2024-01-01"})
    assert evaluate(after, on_the_boundary, now=BOUNDARY_NOW) is True
    assert evaluate(after, the_day_before, now=BOUNDARY_NOW) is False
    assert evaluate(before, on_the_boundary, now=BOUNDARY_NOW) is True
    assert evaluate(before, the_day_after, now=BOUNDARY_NOW) is False

    # wire half: the inclusive single-angle string, at the same boundary value
    assert url({"release.from": "2024-01-01"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3E=2024-01-01"
    )
    assert url({"release.to": "2024-01-01"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3C=2024-01-01"
    )


def test_the_from_to_boundary_agrees_between_the_two_halves_on_a_moment_field():
    """``added`` -> ``addedAt``: Plex stores a time of day, so the two edges of
    the pair sit at different KINDS of boundary and each half has to be built
    to land on the same one.

    ``.from: A`` is inclusive at the MOMENT ``A 00:00`` -- everything added
    during day A is at or after midnight, so the whole day is in and the
    evening before is out. ``.to: A`` is inclusive at the calendar DAY: it
    keeps everything added through the end of day A, 09:15 included, and drops
    the first instant of day A+1. Written as a pair, ``added.from: A`` plus
    ``added.to: B`` is the closed range an operator writing two dates means.
    The strict, moment-symmetric reading is still available and unchanged,
    under the older spelling ``.after``/``.before`` (roadmap row 154,
    ``_as_moment``'s docstring, and
    ``test_collection_filter_oracle.py::test_the_added_boundary_is_decided_at_the_moment_not_the_date``).

    The wire half lands on the same two boundaries, and the probe is why. For
    ``.from``: on the probe's boundary ``A = 2026-01-10``, ``addedAt%3E=A``,
    ``addedAt%3E%3E=A`` and ``addedAt%3E%3E=A-1day`` ALL returned 101 -- Plex
    reads a bare date on a moment field as that day's MIDNIGHT, so ``%3E=A``
    is "at or after ``A 00:00``", the client comparison exactly. For ``.to``
    that same midnight reading is what rules ``%3C=A`` OUT: ``addedAt%3C=A``
    returned 1862, identical to the strict ``%3C%3C=A``, i.e. both forms
    exclude everything added during day A -- they would drop the 09:15 item
    this test keeps. So ``.to`` renders at the day AFTER and strictly:
    ``%3C%3C=A+1`` is "strictly before ``(A+1) 00:00``", the client's own
    comparison. The same day-after form was measured on the date field, where
    a same-day equality count exists to check it against: ``%3C%3C=D+1day``
    returned 1864 = ``%3C=D`` = ``%3C%3C=D`` (1862) + ``=D`` (2).

    NOT covered here: ``.from: today`` disagrees between the two halves on a
    moment field, by up to 24 hours -- the client compares against ``now`` (a
    moment, time of day included) while the wire compares against
    ``now.date()`` (that day's midnight). Amendment 1 happens to make ``.to:
    today`` AGREE (both halves land on the same calendar day); this test
    itself exercises a literal date, ``A = 2026-01-10``, never ``today``, so
    that agreement is asserted here in prose, not by an assertion. The
    surviving asymmetry is on ``.from`` and is inherited from the
    pre-existing ``_Today`` convention (``.after: today`` has the same
    split) -- out of scope for roadmap row 157 (review Important 3).
    """
    at_midnight = {"added": dt.datetime(2026, 1, 10, 0, 0)}
    during_the_day = {"added": dt.datetime(2026, 1, 10, 9, 15)}
    the_last_minute = {"added": dt.datetime(2026, 1, 10, 23, 59)}
    the_evening_before = {"added": dt.datetime(2026, 1, 9, 22, 40)}
    the_next_midnight = {"added": dt.datetime(2026, 1, 11, 0, 0)}

    after = parse_filters({"added.from": "2026-01-10"})
    before = parse_filters({"added.to": "2026-01-10"})

    # `.from` takes the whole of day A, midnight included
    assert evaluate(after, at_midnight, now=BOUNDARY_NOW) is True
    assert evaluate(after, during_the_day, now=BOUNDARY_NOW) is True
    assert evaluate(after, the_evening_before, now=BOUNDARY_NOW) is False

    # `.to` takes the whole calendar day A -- 09:15 IS kept, which is the one
    # cell of this row's decision table -- and stops
    # at the first instant of A+1.
    assert evaluate(before, at_midnight, now=BOUNDARY_NOW) is True
    assert evaluate(before, during_the_day, now=BOUNDARY_NOW) is True
    assert evaluate(before, the_last_minute, now=BOUNDARY_NOW) is True
    assert evaluate(before, the_evening_before, now=BOUNDARY_NOW) is True
    assert evaluate(before, the_next_midnight, now=BOUNDARY_NOW) is False

    # wire half, at the same two boundaries: `.from` at A inclusive, `.to`
    # strictly before A+1. The 09:15 item is inside `addedAt%3C%3C=2026-01-11`
    # and outside `addedAt%3C=2026-01-10`, which is the whole reason the
    # rendering differs from the date-only row above.
    assert url({"added.from": "2026-01-10"}) == (
        "?type=1&sort=titleSort&addedAt%3E=2026-01-10"
    )
    assert url({"added.to": "2026-01-10"}) == (
        "?type=1&sort=titleSort&addedAt%3C%3C=2026-01-11"
    )


# Roadmap row 157's Important 2 review finding: `MOMENT_DATE_ROWS` was
# referenced by no test, so two of its four members (`episode_added`,
# `episode_last_played`) rendered `.to` unpinned -- dropping either from the
# frozenset kept the whole suite green while the row silently fell onto the
# date-only branch. Each entry here is a HARDCODED (row, libtype, term)
# triple, not derived from `MOMENT_DATE_ROWS` -- so if a row is ever dropped
# from that set, `search_url._arguments` renders it the date-only way
# (`%3C=A` instead of `%3C%3C=A+1`) and the row's own case here goes red,
# rather than silently disappearing from the parametrize. `added` and
# `last_played` repeat the boundary already pinned above (kept for a single
# per-row table a reader can scan); `episode_added` and `episode_last_played`
# are the two the review found missing. `release` and `episode_air_date` are
# the date-only rows with a search half -- `last_episode_aired` is date-only
# too but `search_field=None` (facts tier), so it has no wire half to render.
MOMENT_TO_RENDERS = [
    ({"added.to": "2026-01-10"}, "movie", "addedAt%3C%3C=2026-01-11"),
    ({"last_played.to": "2026-01-10"}, "movie", "lastViewedAt%3C%3C=2026-01-11"),
    ({"episode_added.to": "2026-01-10"}, "show", "episode.addedAt%3C%3C=2026-01-11"),
    (
        {"episode_last_played.to": "2026-01-10"}, "show",
        "episode.lastViewedAt%3C%3C=2026-01-11",
    ),
]

DATE_ONLY_TO_RENDERS = [
    ({"release.to": "2026-01-10"}, "movie", "originallyAvailableAt%3C=2026-01-10"),
    (
        {"episode_air_date.to": "2026-01-10"}, "show",
        "episode.originallyAvailableAt%3C=2026-01-10",
    ),
]


@pytest.mark.parametrize(
    ("raw", "libtype", "term"), MOMENT_TO_RENDERS,
    ids=[next(iter(raw)) for raw, _, _ in MOMENT_TO_RENDERS],
)
def test_every_moment_date_row_renders_to_as_strict_at_the_day_after(raw, libtype, term):
    sort_type = "2" if libtype == "show" else "1"
    assert url(raw, libtype=libtype) == f"?type={sort_type}&sort=titleSort&{term}"


@pytest.mark.parametrize(
    ("raw", "libtype", "term"), DATE_ONLY_TO_RENDERS,
    ids=[next(iter(raw)) for raw, _, _ in DATE_ONLY_TO_RENDERS],
)
def test_every_date_only_row_renders_to_as_inclusive_lte(raw, libtype, term):
    sort_type = "2" if libtype == "show" else "1"
    assert url(raw, libtype=libtype) == f"?type={sort_type}&sort=titleSort&{term}"


def test_an_existing_date_definitions_url_is_unchanged_by_the_inclusive_pair():
    """Storm-guard for roadmap row 157, as a test rather than as an argument.

    ``smart.smart_definition_hash`` (smart.py:225) and
    ``reconcile.definition_hash`` (reconcile.py:92) both fold the BUILT search
    URL, so a stored fingerprint moves if and only if a URL moves. Adding
    operator NAMES to a table cannot move one -- no existing config can be
    using a name that did not parse -- and these three literals are what says
    so: the strict pair, the relative window, and the show-library rescope,
    each byte-identical to what this renderer produced before row 157.
    """
    assert url({"release.after": "2000-01-01", "added.before": "12/25/2020"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3E%3E=2000-01-01"
        "&and=1&addedAt%3C%3C=2020-12-25"
    )
    assert url({"added": 30}) == "?type=1&sort=titleSort&addedAt%3E%3E=-30d"
    assert url({"added.after": "2024-01-01"}, libtype="show") == (
        "?type=2&sort=titleSort&show.addedAt%3E%3E=2024-01-01"
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
    (``sort_type = builder_level = "show"``, builder.py:994-995, :4122-4123).
    Collecting the episodes themselves is E-2's selector."""
    assert url(raw, libtype="show") == f"?type=2&sort=titleSort&{term}"


def test_every_family_e_row_refuses_on_a_movie_library_naming_the_kind():
    """Kometa refuses nineteen of the twenty by name on a movie library
    (``is_movie and final_attr in show_only_searches``,
    kometa_build_filter.py:919) rather than sending a query a library with no
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


def test_the_search_type_defaults_to_the_library_kind():
    """The whole reason the split is invisible: every caller that passes only a
    ``libtype`` gets the URL it got before, which is what makes all 22 oracle
    goldens byte-identical and is the roadmap's own claim for this row."""
    assert url({"genre": "Horror"}) == url({"genre": "Horror"}, search_type="movie")
    assert url({"genre": "Horror"}, libtype="show") == (
        url({"genre": "Horror"}, libtype="show", search_type="show")
    )


def test_an_episode_search_types_by_the_level_and_scopes_by_the_library():
    """The split, in one string. ``type=4`` comes from the SEARCH level and
    ``episode.title``/``show.unmatched`` from the LIBRARY's kind -- Kometa
    applies ``show_translation`` because ``self.library.is_show``, whatever the
    level is (modules/builder.py:4176-4181). Composed against ``EPISODE_SORTS``
    rather than a retyped literal because that table is pinned by value against
    the vendored driver, and a second spelling of it here would be a second
    transcription."""
    assert url(
        {"episode_title.begins": "Pilot", "show_unmatched": False},
        libtype="show", search_type="episode",
    ) == (
        "?type=4&sort=" + EPISODE_SORTS["title.asc"]
        + "&episode.title%3C=Pilot&and=1&show.unmatched!=1"
    )


def test_a_season_search_carries_type_three():
    assert url(
        {"season_collection": "Specials"}, libtype="show", search_type="season",
    ) == "?type=3&sort=" + SEASON_SORTS["season.asc"] + "&season.collection=301"


def test_a_search_type_never_changes_which_attributes_are_legal():
    """Job 4 keeps the LIBRARY kind, and it has to: every family-E row is
    ``search_kinds=("show",)`` (they are library-kind columns, recon §2), so a
    search type reaching ``_render_predicate`` would refuse all twenty of them.
    A movie-only attribute is still refused on a show library at episode level,
    and by the library's kind.

    ``duration``, not the earlier ``resolution``: ``resolution`` is
    ``search_kinds=_BOTH`` (filters.py:770-792, and already proved legal on a
    show library by ``test_a_show_library_gets_the_rescoped_fields`` above),
    so it never raises here regardless of ``search_type`` -- confirmed
    empirically (``pytest ... -k test_a_search_type_never_changes`` failed
    ``DID NOT RAISE`` with ``resolution``). ``duration`` is
    ``search_kinds=("movie",)`` and is what the docstring's "movie-only
    attribute" actually names."""
    with pytest.raises(SearchAttributeNotAvailable) as error:
        url({"duration.gt": 90}, libtype="show", search_type="episode")
    message = str(error.value)
    assert "movie" in message
    assert "show library" in message


def test_a_discovered_field_row_takes_its_field_from_the_resolver_not_the_table():
    """Roadmap row 176, and the ONE line in this module that asks for a field
    (``_render_predicate``). ``folder_location``'s table row holds the
    ``DISCOVERED`` sentinel and ``field_for`` raises on it, so a renderer that
    kept the old call would fail loudly rather than send the sentinel -- but the
    thing this pins is the OTHER half: the show library's field is
    ``episode.location``, not ``location``, and a renderer that dropped the
    prefix would send a field Plex answers with nothing rather than with an
    error."""
    assert url({"folder_location": "/mnt/media/Movies"}) == (
        "?type=1&sort=titleSort&location=1"
    )
    assert url({"folder_location": "/mnt/media/TV"}, libtype="show") == (
        "?type=2&sort=titleSort&episode.location=2"
    )


def test_every_other_row_still_takes_its_field_from_the_table():
    """The sentinel is a branch on one row, not a redirection of all of them: a
    resolver with no ``discover_field`` at all still renders every shipped row,
    which is what keeps ``TagResolver``'s new member optional in practice the
    way ``choices`` is."""

    def bare(attribute, value, /):
        return CHOICES.get((attribute, value), ())

    from autoposter.collections.filters import parse_filters
    from autoposter.collections.search_url import build_search_url

    group = parse_filters({"genre": "Horror"}, field="params", searching=True, base="all")
    assert build_search_url(group, libtype="movie", resolve_tag=bare) == (
        "?type=1&sort=titleSort&genre=1138"
    )


def test_folder_location_regex_expands_over_the_discovered_fields_vocabulary():
    """``.regex`` rides along for free and must be proven to: the branch calls
    ``resolve_tag.choices(row.name)``, which goes through ``_field_and_scope``
    and therefore through the discovery, so a pattern is tested against the
    TITLES of the discovered field's own values."""

    def choices(attribute, /):
        assert attribute == "folder_location"
        return (("1", "/mnt/media/Movies"), ("2", "/mnt/media/TV"))

    resolve.choices = choices
    try:
        assert url({"folder_location.regex": "^/mnt/media/M"}) == (
            "?type=1&sort=titleSort&location=1"
        )
        with pytest.raises(TagValueNotFound, match="matched none of"):
            url({"folder_location.regex": "^/nope"})
    finally:
        del resolve.choices
