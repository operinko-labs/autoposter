"""``imdb_search``: IMDb's advanced title search, as a builder.

Never touches the real API -- MockTransport only. The fixtures under
``tests/fixtures/collections/imdb_search_*.json`` are recordings of what
``api.graphql.imdb.com`` actually answered on 2026-08-25 to the query pinned in
``src/autoposter/collections/imdb_graphql.py`` as ``SEARCH_QUERY``, and
``RECORDED_CONSTRAINTS``/``RECORDED_SORT`` below are the variables that produced
them. Introspection is refused on this endpoint, so the whole constraint schema
was walked through the GraphQL validator's own error messages -- the walk log is
in ``.superpowers/sdd/task-3-report.md``.

Two things that walk found are worth stating here, because they are what most of
this file exists to hold in place:

- **IMDb does not validate constraint *values*.** ``anyTitleTypeIds: ["zzz"]``
  and ``allGenreIds: ["ZzzNotAGenre"]`` both come back HTTP 200 with
  ``total: 0`` and no error. A mis-spelled genre is therefore indistinguishable
  from an honest empty result, and an empty result one layer down means "remove
  every member" -- so the vocabulary is pinned and validated here instead.
- **``POPULARITY`` sorts a rank, so ``ASC`` is most-popular-first.** Sending
  ``sortOrder: DESC`` for "most popular" returns the *least* popular titles that
  match, which looks like a working collection full of the wrong films.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.builders.base import LibraryTypeMismatch
from autoposter.collections.builders.imdb_search import GENRES, SORTS
from autoposter.collections.imdb_graphql import (
    MAX_PAGES,
    PAGE_SIZE,
    SEARCH_QUERY,
    ImdbListDrift,
    ImdbListRefused,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"

# The variables that produced ``imdb_search_p1.json``/``imdb_search_p2.json``,
# transcribed from the recording session. ``test_the_recorded_query_is_the_one
# _this_builder_sends`` is what keeps the fixture a recording of this builder's
# request rather than of some query nobody sends any more.
RECORDED_CONSTRAINTS = {
    "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
    "genreConstraint": {"allGenreIds": ["Film-Noir"]},
    "userRatingsConstraint": {
        "aggregateRatingRange": {"min": 8.0, "max": 10.0},
        "ratingsCountRange": {"min": 50000},
    },
    "releaseDateConstraint": {
        "releaseDateRange": {"start": "1940-01-01", "end": "1959-12-31"}
    },
}
RECORDED_SORT = {"sortBy": "USER_RATING", "sortOrder": "DESC"}
RECORDED_PARAMS = {
    "type": "movie",
    "genres": ["Film-Noir"],
    "rating_gte": 8.0,
    "rating_lte": 10.0,
    "votes_gte": 50000,
    "released_after": "1940-01-01",
    "released_before": "1959-12-31",
    "sort": "rating.desc",
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(request) -> dict:
    return json.loads(request.content)


def _paged(seen: list | None = None,
           pages=("imdb_search_p1.json", "imdb_search_p2.json")):
    """Serve page 1 to a cursorless request and page 2 to a cursored one."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        cursored = "after" in _body(request)["variables"]
        return httpx.Response(200, json=load(pages[1 if cursored else 0]))

    return httpx.MockTransport(handler)


def _answers(payload, seen: list | None = None, status: int = 200):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _ctx(http, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, http=http, config=params,
        sources=SourceClients(),
    )


async def _build(http, library_type: str = "Movie", **params):
    return await REGISTRY["imdb_search"].build(_ctx(http, library_type, **params))


# --- the request shape --------------------------------------------------------


async def test_the_client_name_header_is_sent():
    """Without it the endpoint answers 403 -- the same header ``charts.py``
    calls not optional, and this builder reuses rather than re-declares."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, genres=["Film-Noir"])

    assert seen[0].headers["x-imdb-client-name"] == "imdb-web-next"
    assert str(seen[0].url) == "https://api.graphql.imdb.com/"


async def test_the_pinned_query_is_sent_verbatim():
    """The query text is the contract with IMDb, and it is asserted whole rather
    than by keyword: a query that still says 'advancedTitleSearch' but lost its
    ``pageInfo`` would pass a substring check and silently stop paging."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, genres=["Film-Noir"])

    assert _body(seen[0])["query"] == SEARCH_QUERY


async def test_the_recorded_query_is_the_one_this_builder_sends():
    """The fixtures are a recording, and this is what keeps them one: the
    variables the builder produces for ``RECORDED_PARAMS`` must be exactly the
    variables that were on the wire when ``imdb_search_p1.json`` came back."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, **RECORDED_PARAMS)

    variables = _body(seen[0])["variables"]
    assert variables["constraints"] == RECORDED_CONSTRAINTS
    assert variables["sort"] == RECORDED_SORT
    assert variables["first"] == PAGE_SIZE


@pytest.mark.parametrize(
    "params,constraint",
    [
        ({"genres": ["Film-Noir"]}, {"genreConstraint": {"allGenreIds": ["Film-Noir"]}}),
        (
            {"genres": ["Crime", "Drama"]},
            {"genreConstraint": {"allGenreIds": ["Crime", "Drama"]}},
        ),
        (
            {"rating_gte": 7.5},
            {"userRatingsConstraint": {"aggregateRatingRange": {"min": 7.5}}},
        ),
        (
            {"rating_lte": 4.0},
            {"userRatingsConstraint": {"aggregateRatingRange": {"max": 4.0}}},
        ),
        (
            {"rating_gte": 7.5, "rating_lte": 9.0},
            {"userRatingsConstraint": {"aggregateRatingRange": {"min": 7.5, "max": 9.0}}},
        ),
        (
            {"votes_gte": 50000},
            {"userRatingsConstraint": {"ratingsCountRange": {"min": 50000}}},
        ),
        (
            {"rating_gte": 7.5, "votes_gte": 50000},
            {
                "userRatingsConstraint": {
                    "aggregateRatingRange": {"min": 7.5},
                    "ratingsCountRange": {"min": 50000},
                }
            },
        ),
        (
            {"released_after": "1940-01-01"},
            {"releaseDateConstraint": {"releaseDateRange": {"start": "1940-01-01"}}},
        ),
        (
            {"released_before": "1959-12-31"},
            {"releaseDateConstraint": {"releaseDateRange": {"end": "1959-12-31"}}},
        ),
        (
            {"released_after": "1940-01-01", "released_before": "1959-12-31"},
            {
                "releaseDateConstraint": {
                    "releaseDateRange": {"start": "1940-01-01", "end": "1959-12-31"}
                }
            },
        ),
    ],
)
async def test_every_constraint_reaches_the_wire(params, constraint):
    """One case per param, asserted as whole-dict equality rather than
    containment: a constraint silently dropped fails, and so does one nobody
    asked for. IMDb answers an unsent constraint by not filtering, which is the
    failure this parametrisation exists to catch."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, **params)

    assert _body(seen[0])["variables"]["constraints"] == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **constraint,
    }


@pytest.mark.parametrize(
    "library_type,expected",
    [("Movie", ["movie"]), ("Show", ["tvSeries", "tvMiniSeries"])],
)
async def test_the_title_type_follows_the_library_when_type_is_not_written(
    library_type, expected
):
    """A search with no title-type constraint spans all 31 million IMDb titles,
    9.5 million of them episodes, so ``first: 250`` would be almost entirely
    noise the resolver then throws away. The library already says which media
    type the pass wants."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, library_type, genres=["Film-Noir"])

    assert _body(seen[0])["variables"]["constraints"]["titleTypeConstraint"] == {
        "anyTitleTypeIds": expected
    }


async def test_an_explicit_type_sends_the_same_ids_as_the_library_would():
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, "Show", type="tv", genres=["Crime"])

    assert _body(seen[0])["variables"]["constraints"]["titleTypeConstraint"] == {
        "anyTitleTypeIds": ["tvSeries", "tvMiniSeries"]
    }


# --- sort ---------------------------------------------------------------------


async def test_the_default_sort_is_most_popular_first():
    """And "most popular first" is ``POPULARITY`` **ascending**: IMDb's
    popularity field is a rank, where 1 is the most popular title. Sending DESC
    here returns the least popular titles that match -- a collection that looks
    populated and is entirely wrong. This assertion is the pin on that."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, genres=["Film-Noir"])

    assert _body(seen[0])["variables"]["sort"] == {
        "sortBy": "POPULARITY", "sortOrder": "ASC"
    }


@pytest.mark.parametrize(
    "sort,expected",
    [
        ("popularity.desc", {"sortBy": "POPULARITY", "sortOrder": "ASC"}),
        ("popularity.asc", {"sortBy": "POPULARITY", "sortOrder": "DESC"}),
        ("rating.desc", {"sortBy": "USER_RATING", "sortOrder": "DESC"}),
        ("rating.asc", {"sortBy": "USER_RATING", "sortOrder": "ASC"}),
        ("votes.desc", {"sortBy": "USER_RATING_COUNT", "sortOrder": "DESC"}),
        ("release_date.desc", {"sortBy": "RELEASE_DATE", "sortOrder": "DESC"}),
        ("release_date.asc", {"sortBy": "RELEASE_DATE", "sortOrder": "ASC"}),
        ("runtime.desc", {"sortBy": "RUNTIME", "sortOrder": "DESC"}),
    ],
)
async def test_each_sort_reaches_the_wire_as_the_pair_it_names(sort, expected):
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, genres=["Film-Noir"], sort=sort)

    assert _body(seen[0])["variables"]["sort"] == expected


def test_every_sort_name_is_a_key_direction_pair_over_the_walked_vocabulary():
    """The sort vocabulary is the walked one -- these five enum values were
    accepted by the live endpoint and every other guess was rejected -- and each
    is offered in both directions so the table cannot grow a one-way option
    nobody notices is missing."""
    assert {by for by, _ in SORTS.values()} == {
        "POPULARITY", "USER_RATING", "USER_RATING_COUNT", "RELEASE_DATE", "RUNTIME"
    }
    for name in SORTS:
        key, _, direction = name.rpartition(".")
        assert direction in ("asc", "desc")
        assert f"{key}.{'asc' if direction == 'desc' else 'desc'}" in SORTS


async def test_an_unknown_sort_is_refused_naming_the_known_ones():
    with pytest.raises(ValidationError, match="popularity.desc"):
        await _build(None, genres=["Crime"], sort="alphabetical")


# --- paging, order and the cap ------------------------------------------------


async def test_the_second_page_is_asked_for_with_the_first_pages_cursor():
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, **RECORDED_PARAMS)

    cursor = load("imdb_search_p1.json")["data"]["advancedTitleSearch"][
        "pageInfo"]["endCursor"]
    assert len(seen) == 2
    assert _body(seen[1])["variables"]["after"] == cursor
    assert _body(seen[1])["variables"]["constraints"] == RECORDED_CONSTRAINTS


async def test_ids_are_returned_in_search_order_across_pages():
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await _build(http, **RECORDED_PARAMS)

    assert result.ids == [
        ("imdb", "tt0043014"), ("imdb", "tt0036775"),  # page 1, recorded order
        ("imdb", "tt0041959"),                          # page 2
    ]
    assert result.summary is None and result.poster_kind is None


async def test_paging_stops_when_the_last_page_says_so():
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, **RECORDED_PARAMS)

    assert len(seen) == 2


async def test_the_page_loop_is_capped_and_warns():
    """A cursor that never terminates -- a bug at either end -- must cost a
    bounded number of requests and say so, not truncate in silence."""
    seen: list = []
    payload = {"data": {"advancedTitleSearch": {
        "pageInfo": {"hasNextPage": True, "endCursor": "more"},
        "edges": [{"node": {"title": {"id": "tt0043014"}}}],
    }}}
    async with httpx.AsyncClient(transport=_answers(payload, seen)) as http:
        result = await _build(http, genres=["Film-Noir"])

    assert len(seen) == MAX_PAGES
    assert len(result.ids) == MAX_PAGES


async def test_the_cap_warning_names_the_search(caplog):
    payload = {"data": {"advancedTitleSearch": {
        "pageInfo": {"hasNextPage": True, "endCursor": "more"},
        "edges": [{"node": {"title": {"id": "tt0043014"}}}],
    }}}
    with caplog.at_level(logging.WARNING, logger="autoposter.collections.imdb_graphql"):
        async with httpx.AsyncClient(transport=_answers(payload)) as http:
            await _build(http, genres=["Film-Noir"])

    assert any("imdb_search" in record.getMessage() for record in caplog.records)


# --- drift fails loudly -------------------------------------------------------


async def test_the_list_edge_shape_is_not_accepted_on_the_search_root():
    """THE drift test. ``{"title": {"id": …}}`` is the shape the *list* roots
    answer with and the shape this repository already pins twice, so it is
    exactly the subtly-wrong response an upstream change -- or a copy-paste --
    would produce here. An implementation that read the id defensively
    (``edge.get("node", {})…``, skip if missing) would return an EMPTY list, and
    empty one layer down means "remove every member"."""
    payload = {"data": {"advancedTitleSearch": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "edges": [{"title": {"id": "tt0043014"}}],
    }}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift) as caught:
            await _build(http, genres=["Film-Noir"])

    message = str(caught.value)
    assert "'title'" in message, "the drift raise must name the shape it found"
    assert "{'node': {'title': {'id': 'tt…'}}}" in message, (
        "and the shape it demanded, built from the pinned edge path"
    )


@pytest.mark.parametrize(
    "connection,expected",
    [
        ({"pageInfo": {"hasNextPage": False}, "edges": None}, "'edges'"),
        ({"pageInfo": {"hasNextPage": False}, "edges": {}}, "'edges'"),
        ({"edges": []}, "'pageInfo'"),
        (
            {"pageInfo": {"hasNextPage": False},
             "edges": [{"node": {"title": {"id": 12345}}}]},
            "12345",
        ),
        (
            {"pageInfo": {"hasNextPage": False},
             "edges": [{"node": {"title": {"id": "nm0000138"}}}]},
            "nm0000138",
        ),
        (
            {"pageInfo": {"hasNextPage": False},
             "edges": [{"node": {"id": "tt0043014"}}]},
            "tt0043014",
        ),
    ],
)
async def test_an_unexpected_shape_raises_naming_what_was_found(connection, expected):
    payload = {"data": {"advancedTitleSearch": connection}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift) as caught:
            await _build(http, genres=["Film-Noir"])

    assert expected in str(caught.value)


async def test_a_renamed_root_field_raises_naming_the_fields_that_were_there():
    payload = {"data": {"titleSearch": {
        "pageInfo": {"hasNextPage": False}, "edges": [],
    }}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift, match="titleSearch"):
            await _build(http, genres=["Film-Noir"])


async def test_another_page_promised_without_a_cursor_raises():
    payload = {"data": {"advancedTitleSearch": {
        "pageInfo": {"hasNextPage": True, "endCursor": None},
        "edges": [{"node": {"title": {"id": "tt0043014"}}}],
    }}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift, match="endCursor"):
            await _build(http, genres=["Film-Noir"])


async def test_a_graphql_errors_array_is_refused_even_on_a_200():
    """The recorded ``imdb_search_refused.json``: IMDb answers a constraint it
    will not accept with HTTP **200**, an ``errors`` array and
    ``data.advancedTitleSearch: null``. Checking the status code alone would
    read that as an empty search and empty the collection."""
    async with httpx.AsyncClient(
        transport=_answers(load("imdb_search_refused.json"))
    ) as http:
        with pytest.raises(ImdbListRefused) as caught:
            await _build(http, genres=["Film-Noir"])

    message = str(caught.value)
    assert "IMDb refused the request" in message
    assert "imdb_search" in message
    assert message.endswith("…"), (
        "IMDb's error carries a nested Lambda payload, so the excerpt is bounded "
        "-- a drifted response must not be able to put a megabyte in a log line"
    )


async def test_a_null_result_with_no_error_is_drift_not_emptied():
    """A null ``advancedTitleSearch`` with no ``errors`` array is not a shape
    IMDb has ever answered this query with -- ``ImdbListDrift``, not
    ``ImdbListRefused``: an operator cannot fix an unrecognised shape, only a
    code change can, and the engine's log line carries the exception class
    name and nothing else."""
    async with httpx.AsyncClient(
        transport=_answers({"data": {"advancedTitleSearch": None}})
    ) as http:
        with pytest.raises(ImdbListDrift, match="imdb_search"):
            await _build(http, genres=["Film-Noir"])


async def test_an_http_error_raises_rather_than_returning_nothing():
    async with httpx.AsyncClient(transport=_answers({}, status=403)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await _build(http, genres=["Film-Noir"])


async def test_a_search_that_really_is_empty_is_data_not_a_failure():
    """The one empty result this builder will hand back: IMDb positively
    reporting no matches and no next page."""
    payload = {"data": {"advancedTitleSearch": {
        "total": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [],
    }}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        result = await _build(http, genres=["Film-Noir"], rating_gte=9.9)

    assert result.ids == []


# --- the one-constraint guard -------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"sort": "rating.desc"},
        {"type": "movie"},
        {"type": "movie", "sort": "votes.desc"},
    ],
)
async def test_a_search_with_no_filtering_constraint_is_refused(params):
    """An unconstrained advancedTitleSearch is a chart in disguise: every movie
    IMDb knows, ordered by popularity, truncated at the page cap. ``imdb_chart``
    already builds that deliberately, and in ``sync_mode`` this one would write
    2,500 arbitrary titles into a collection that was meant to say something.
    Neither ``type`` nor ``sort`` filters anything, so neither counts."""
    with pytest.raises(ValidationError, match="imdb_chart"):
        await _build(None, **params)


async def test_one_filtering_constraint_is_enough():
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await _build(http, votes_gte=1000)

    assert result.ids


# --- params validation --------------------------------------------------------


async def test_the_builder_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await _build(None, genres=["Crime"], keywords=["heist"])


@pytest.mark.parametrize("value", ["Crimee", "crime ", "Sci Fi", "", "Action|Comedy"])
async def test_an_unknown_genre_is_refused_at_load(value):
    """IMDb answers an unknown genre id with ``total: 0`` and no error, so an
    unvalidated typo builds an empty collection that looks like a working one.
    The vocabulary is pinned; a value outside it never reaches the wire."""
    with pytest.raises(ValidationError, match="genre"):
        await _build(None, genres=[value])


async def test_genres_are_matched_case_insensitively_and_sent_in_imdbs_spelling():
    """``film-noir`` is what an operator writes; ``Film-Noir`` is the only
    spelling IMDb matches."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, genres=["film-noir", "CRIME"])

    assert _body(seen[0])["variables"]["constraints"]["genreConstraint"] == {
        "allGenreIds": ["Film-Noir", "Crime"]
    }


def test_the_genre_vocabulary_is_the_one_that_was_walked():
    """Every name here returned a non-zero ``total`` from the live endpoint on
    2026-08-25, and a name IMDb does not know returns zero -- so non-zero is
    proof the id is real. Sorted and unique, so a duplicate cannot hide."""
    assert list(GENRES) == sorted(set(GENRES))
    assert "Film-Noir" in GENRES and "Sci-Fi" in GENRES and "Reality-TV" in GENRES
    assert len(GENRES) == 28


async def test_an_empty_genre_list_is_refused():
    """``genres: []`` is YAML for "I meant to write some", and it would
    otherwise count as a filtering constraint that filters nothing."""
    with pytest.raises(ValidationError):
        await _build(None, genres=[])


@pytest.mark.parametrize("value", [0, 0.9, 10.1, 11, -1])
async def test_a_rating_outside_imdbs_scale_is_refused(value):
    with pytest.raises(ValidationError):
        await _build(None, rating_gte=value)


async def test_a_rating_window_that_excludes_everything_is_refused():
    with pytest.raises(ValidationError, match="rating_gte"):
        await _build(None, rating_gte=9.0, rating_lte=7.0)


async def test_a_release_window_that_excludes_everything_is_refused():
    with pytest.raises(ValidationError, match="released_after"):
        await _build(None, released_after="2000-01-01", released_before="1990-01-01")


@pytest.mark.parametrize("value", [-1, "many"])
async def test_a_vote_floor_that_is_not_a_count_is_refused(value):
    with pytest.raises(ValidationError):
        await _build(None, votes_gte=value)


async def test_a_vote_floor_of_zero_is_refused():
    """``votes_gte: 0`` filters nothing while still satisfying the
    one-constraint guard -- the exact hole ``rating_gte``'s ``ge=1.0`` floor
    closes on the rating side, so the vote floor gets IMDb's minimum count of
    one rather than zero for the same reason."""
    with pytest.raises(ValidationError):
        await _build(None, votes_gte=0)


@pytest.mark.parametrize("value", ["show", "series", "Movie", "film", "tvSeries"])
async def test_a_type_outside_movie_or_tv_is_refused(value):
    with pytest.raises(ValidationError, match="type"):
        await _build(None, type=value, genres=["Crime"])


# --- library cross-check ------------------------------------------------------


@pytest.mark.parametrize(
    "library_type,value", [("Movie", "tv"), ("Show", "movie")]
)
async def test_a_type_that_contradicts_the_library_is_refused(library_type, value):
    """Not a silent no-op: a ``type: tv`` search resolved against a Movie library
    does not error, it simply matches nothing -- and "matched nothing" is what a
    correct collection of titles the library does not own looks like too."""
    with pytest.raises(LibraryTypeMismatch) as caught:
        await _build(None, library_type, type=value, genres=["Crime"])

    assert "type" in str(caught.value) and value in str(caught.value)


async def test_a_library_that_is_neither_movie_nor_show_is_refused():
    with pytest.raises(LibraryTypeMismatch, match="Artist"):
        await _build(None, "Artist", genres=["Crime"])


# --- registration -------------------------------------------------------------


def test_the_builder_is_registered_under_its_own_name():
    assert REGISTRY["imdb_search"].type_name == "imdb_search"


# --- rows 258-265: the eight probe-gated families -----------------------------
#
# Session 4 of ``.superpowers/sdd/p258_imdb_probe.log`` (2026-09-08) is the only
# evidence here: sessions 1-3 are the probe script's own wrapper bug and are
# void. Each ``PROBED_*`` literal below is the exact constraint object session 4
# put on the wire, beside the total IMDb answered with. A sibling field the
# document transcribes for the same family (``excludeKeywords``,
# ``notInAnyList``, ``winnerFilter``, ...) is the SAME GraphQL input type and
# ships on the transcription's authority, never on the wire's -- the tests say
# which is which.

PROBED_RUNTIME = {"runtimeConstraint": {"runtimeRangeMinutes": {"min": 80, "max": 90}}}
PROBED_CERTIFICATE = {
    "certificateConstraint": {
        "anyRegionCertificateRatings": [{"region": "US", "rating": "PG-13"}]
    }
}


async def _constraints(**params):
    """The ``constraints`` variable one build put on the wire.

    Every family test below asserts on the REQUEST, so the recorded two-page
    transport is the only answer any of them wants and none of them needs to
    pass one in.
    """
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await _build(http, **params)
    return _body(seen[0])["variables"]["constraints"]


def _messages(caught) -> list:
    """The validator sentences, without pydantic's ``input_value=`` tail.

    ``str(ValidationError)`` appends the offending input; ``errors()[i]["msg"]``
    is the sentence this repository wrote and nothing else, which is what the
    row 213 "never echoes the operator's value" rule is asserted against.
    """
    return [error["msg"] for error in caught.value.errors()]


async def test_the_probed_runtime_shape_reaches_the_wire():
    """Row 258, session 4: 15,589 titles against a control of 78,732. Byte-equal
    against the object that was on the wire, not a containment check."""
    assert await _constraints(runtime_gte=80, runtime_lte=90) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **PROBED_RUNTIME,
    }


@pytest.mark.parametrize(
    "params,window",
    [
        ({"runtime_gte": 80}, {"min": 80}),
        ({"runtime_lte": 90}, {"max": 90}),
    ],
)
async def test_a_one_sided_runtime_window_sends_only_that_bound(params, window):
    """Transcription-only: session 4 probed both bounds together. The one-sided
    forms are the same ``runtimeRangeMinutes`` input object with one key, which
    is how the shipped ``aggregateRatingRange`` already behaves."""
    assert await _constraints(**params) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        "runtimeConstraint": {"runtimeRangeMinutes": window},
    }


@pytest.mark.parametrize("field", ["runtime_gte", "runtime_lte"])
async def test_a_runtime_bound_below_one_minute_is_refused(field):
    """A bound of zero filters nothing while still satisfying the one-constraint
    guard -- the hole ``votes_gte``'s ``ge=1`` floor already closes."""
    with pytest.raises(ValidationError) as caught:
        await _build(None, **{field: 0})

    assert any("whole minutes and must be at least 1" in m for m in _messages(caught))


async def test_a_runtime_window_that_excludes_everything_is_refused():
    with pytest.raises(ValidationError) as caught:
        await _build(None, runtime_gte=200, runtime_lte=90)

    messages = _messages(caught)
    assert any("`runtime_gte` is above `runtime_lte`" in m for m in messages)
    assert not any("200" in m for m in messages), (
        "row 213: the sentence names the two keys, never the operator's numbers"
    )


async def test_runtime_alone_satisfies_the_one_constraint_guard():
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await _build(http, runtime_gte=80)

    assert result.ids


async def test_the_probed_certificate_shape_reaches_the_wire():
    """Row 259, session 4: 1,790 titles. The region is carried PER VALUE, paired
    with the rating in one object -- not a top-level field and not a
    constraint-wide setting (the document's §2 row 23, §5.1)."""
    assert await _constraints(
        content_rating=[{"region": "US", "rating": "PG-13"}]
    ) == {"titleTypeConstraint": {"anyTitleTypeIds": ["movie"]}, **PROBED_CERTIFICATE}


async def test_a_bare_content_rating_means_the_us_region():
    """Kometa's own default (``modules/builder.py:2502-2506``): a bare string
    becomes ``{region: US, rating: <the string>}``. Written as its own test
    because it is the shape most operators will actually write, and it must
    produce the byte the probe proved."""
    assert await _constraints(content_rating=["PG-13"]) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **PROBED_CERTIFICATE,
    }


async def test_a_content_rating_region_other_than_us_is_sent_as_written():
    """Transcription-only: session 4 probed ``US``. The region is upper-cased,
    because IMDb matches these case-sensitively and answers one it does not know
    with an empty result rather than an error."""
    assert await _constraints(content_rating=[{"region": "gb", "rating": "15"}]) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        "certificateConstraint": {
            "anyRegionCertificateRatings": [{"region": "GB", "rating": "15"}]
        },
    }


@pytest.mark.parametrize(
    "value,sentence,echo",
    [
        ([99], "either a rating on its own", "99"),
        ([{"region": "GB"}], "need a `rating:` key", "GB"),
        ([{"rating": "Qq99", "region": "Zzz"}], "2-letter country code", "Zzz"),
        ([{"rating": "", "region": "GB"}], "need a `rating:` key", "GB"),
        (
            {"region": "GB", "rating": "TV-MA"},
            "either a rating on its own",
            "TV-MA",
        ),
        (
            {"region": "GB", "rating": "TV-MA"},
            "either a rating on its own",
            "GB",
        ),
        ("TV-MA", "either a rating on its own", "TV-MA"),
        (
            [{"reigon": "GB", "rating": "15"}],
            "either a rating on its own",
            "reigon",
        ),
    ],
)
async def test_a_malformed_content_rating_is_refused_without_echoing_it(
    value, sentence, echo
):
    """Config-LOAD refusals with fixed sentences (C1/row 213): the sentence names
    the key and the shape, and the operator's own text never appears in it."""
    with pytest.raises(ValidationError) as caught:
        await _build(None, content_rating=value)

    messages = _messages(caught)
    assert any(sentence in m for m in messages)
    assert not any(echo in m for m in messages)


async def test_content_rating_alone_satisfies_the_one_constraint_guard():
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await _build(http, content_rating=["PG-13"])

    assert result.ids


async def test_a_padded_rating_is_stripped_before_it_reaches_the_wire():
    """Mutation gap: dropping the ``.strip()`` (while keeping the emptiness
    check) would ship whitespace to IMDb and return zero titles -- so this
    asserts the exact byte on the wire rather than just that the field loads."""
    assert await _constraints(content_rating=[" PG-13 "]) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **PROBED_CERTIFICATE,
    }


# --- country, language and keyword (rows 260, 261, 262) -----------------------


PROBED_COUNTRY = {"originCountryConstraint": {"anyCountries": ["US"]}}
PROBED_LANGUAGE = {"languageConstraint": {"anyLanguages": ["en"]}}
PROBED_KEYWORD = {"keywordConstraint": {"anyKeywords": ["time-travel"]}}


@pytest.mark.parametrize(
    "params,probed",
    [
        ({"country_any": ["US"]}, PROBED_COUNTRY),
        ({"language_any": ["en"]}, PROBED_LANGUAGE),
        ({"keyword_any": ["time-travel"]}, PROBED_KEYWORD),
    ],
)
async def test_the_probed_shape_reaches_the_wire_for_each_of_these_families(
    params, probed
):
    """Rows 260, 261 and 262, session 4 -- 24,320, 34,699 and 188 titles against
    a control of 78,732. One probed field per family; every sibling below is the
    same GraphQL input object on the transcription's authority."""
    assert await _constraints(**params) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **probed,
    }


@pytest.mark.parametrize(
    "param,value,obj,field",
    [
        ("country", "US", "originCountryConstraint", "allCountries"),
        ("country_any", "US", "originCountryConstraint", "anyCountries"),
        ("country_not", "US", "originCountryConstraint", "excludeCountries"),
        ("country_origin", "US", "originCountryConstraint", "anyPrimaryCountries"),
        ("language", "en", "languageConstraint", "allLanguages"),
        ("language_any", "en", "languageConstraint", "anyLanguages"),
        ("language_not", "en", "languageConstraint", "excludeLanguages"),
        ("language_primary", "en", "languageConstraint", "anyPrimaryLanguages"),
        ("keyword", "heist", "keywordConstraint", "allKeywords"),
        ("keyword_any", "heist", "keywordConstraint", "anyKeywords"),
        ("keyword_not", "heist", "keywordConstraint", "excludeKeywords"),
    ],
)
async def test_each_suffix_reaches_the_graphql_field_the_document_names(
    param, value, obj, field
):
    """The `.not` forms are real here. Kometa DROPS `.not` for its eight
    text-matching families (the document's §2.1) and none of these three is
    among them, so ``excludeCountries``/``excludeLanguages``/``excludeKeywords``
    are sent rather than silently swallowed."""
    assert await _constraints(**{param: [value]}) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        obj: {field: [value.upper() if obj == "originCountryConstraint" else value]},
    }


@pytest.mark.parametrize(
    "param", ["country", "country_any", "country_not", "country_origin"]
)
async def test_a_country_code_that_is_not_two_letters_is_refused(param):
    with pytest.raises(ValidationError) as caught:
        await _build(None, **{param: ["Zzland"]})

    messages = _messages(caught)
    assert any(f"`{param}` takes IMDb's 2-letter country codes" in m for m in messages)
    assert not any("Zzland" in m for m in messages)


@pytest.mark.parametrize(
    "param", ["language", "language_any", "language_not", "language_primary"]
)
async def test_a_blank_language_is_refused(param):
    with pytest.raises(ValidationError) as caught:
        await _build(None, **{param: ["\t"]})

    messages = _messages(caught)
    assert any(f"`{param}` takes IMDb's language codes" in m for m in messages)
    assert not any("\t" in m for m in messages)


@pytest.mark.parametrize("param", ["keyword", "keyword_any", "keyword_not"])
async def test_a_blank_keyword_is_refused(param):
    with pytest.raises(ValidationError) as caught:
        await _build(None, **{param: ["\t"]})

    messages = _messages(caught)
    assert any(f"`{param}` takes IMDb keyword phrases" in m for m in messages)
    assert not any("\t" in m for m in messages)


async def test_country_codes_are_upper_cased_and_languages_are_lower_cased():
    """IMDb matches both case-sensitively and answers a value it does not know
    with ``total: 0`` and no error, so the case fold happens here rather than
    producing a collection that looks like it works."""
    assert await _constraints(country_any=["us"], language_any=["EN"]) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **PROBED_COUNTRY,
        **PROBED_LANGUAGE,
    }


async def test_keyword_spaces_become_hyphens():
    """Kometa's whole normalisation for this family (the document's §2 row 22),
    and the reason ``time travel`` and ``time-travel`` are the same search."""
    assert await _constraints(keyword_any=["Time Travel"]) == {
        "titleTypeConstraint": {"anyTitleTypeIds": ["movie"]},
        **PROBED_KEYWORD,
    }


@pytest.mark.parametrize(
    "param,value",
    [
        ("country", "US"), ("country_any", "US"), ("country_not", "US"),
        ("country_origin", "US"),
        ("language", "en"), ("language_any", "en"), ("language_not", "en"),
        ("language_primary", "en"),
        ("keyword", "heist"), ("keyword_any", "heist"), ("keyword_not", "heist"),
    ],
)
async def test_each_of_these_families_alone_satisfies_the_one_constraint_guard(
    param, value
):
    """``_needs_at_least_one_constraint`` derives its filtering set from
    ``model_fields`` and excludes only ``type`` and ``sort``, so a new param is
    a filter for free -- this is the assertion that it stayed that way."""
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await _build(http, **{param: [value]})

    assert result.ids


@pytest.mark.parametrize(
    "param,value,sentence",
    [
        ("country", "US", "takes IMDb's 2-letter country codes"),
        ("language", "en", "takes IMDb's language codes"),
        ("keyword", "time-travel", "takes IMDb keyword phrases"),
    ],
)
async def test_a_bare_string_where_the_list_belongs_is_refused_not_iterated(
    param, value, sentence
):
    """A ``str`` is iterable too: unguarded, ``country: US`` would walk its
    characters and build ``["U", "S"]`` rather than refusing the shape -- the
    same trap ``_must_be_a_rating_or_a_region_and_rating`` guards against for
    ``content_rating`` (Task 2's fix round). Row 213: the sentence names the
    key, never the operator's value."""
    with pytest.raises(ValidationError) as caught:
        await _build(None, **{param: value})

    messages = _messages(caught)
    assert any(f"`{param}` {sentence}" in m for m in messages)
    assert not any(value in m for m in messages)
