"""The public IMDb list and watchlist builders, and the GraphQL transport.

Never touches the real API -- MockTransport only. The fixtures under
``tests/fixtures/collections/imdb_list_*.json`` and ``imdb_watchlist*.json``
are recordings of what ``api.graphql.imdb.com`` actually answered on
2026-08-25 (list ``ls055350410``, a public watchlist, and a private one), so
what is pinned here is IMDb's shape rather than a plausible-looking guess at
it. Two guesses that a from-memory fixture would have frozen in were wrong:
the edge field is ``title`` and not ``listItem``, and there is no
``user(id:)`` root -- a watchlist is reached through ``predefinedList``.

The one thing the recordings do not carry is the ``titleType`` selection, which
was added afterwards: the recorded list really is five films, so its entries are
typed ``movie`` here, and the mixed list the filter is actually about is built
inline where it is used.
"""
import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY, BuilderContext, SourceClients
from autoposter.collections.imdb_lists import (
    LIST_QUERY,
    MAX_PAGES,
    PAGE_SIZE,
    WATCHLIST_QUERY,
    ImdbListDrift,
    ImdbListRefused,
    fetch_list,
)

FIXTURES = Path(__file__).parent / "fixtures" / "collections"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _body(request) -> dict:
    return json.loads(request.content)


def _paged(seen: list | None = None, pages=("imdb_list_p1.json", "imdb_list_p2.json")):
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


# The mixed list the title-type filter exists for: every type a Movie library
# can own, every type a Show library can own, and the three kinds of entry
# neither can -- an episode, a video game, and one IMDb named no type for.
MIXED = [
    ("tt1000001", "movie"),
    ("tt1000002", "tvMovie"),
    ("tt1000003", "short"),
    ("tt1000004", "video"),
    ("tt2000001", "tvSeries"),
    ("tt2000002", "tvMiniSeries"),
    ("tt3000001", "tvEpisode"),
    ("tt3000002", "videoGame"),
    ("tt3000003", None),
]


def _typed(entries, has_next=False):
    """One page of ``(id, titleType)`` entries, in IMDb's list shape.

    A None type is served the way IMDb serves one -- the ``titleType`` object
    itself null -- rather than as an absent key, because that is the shape that
    would otherwise slip through a ``.get`` chain as a truthy dict.
    """
    edges = [
        {"title": {"id": value, "titleType": {"id": kind} if kind else None}}
        for value, kind in entries
    ]
    return _answers({"data": {"list": {"titleListItemSearch": {
        "total": len(edges),
        "pageInfo": {"hasNextPage": has_next, "endCursor": None},
        "edges": edges,
    }}}})


def _ctx(http, library_type: str = "Movie", **params) -> BuilderContext:
    library = "Movies" if library_type == "Movie" else "TV Shows"
    return BuilderContext(
        library=library, library_type=library_type, http=http, config=params,
        sources=SourceClients(),
    )


# --- the transport: request shape ---------------------------------------------


async def test_the_client_name_header_is_sent():
    """Without it the endpoint answers 403 -- the same header ``charts.py``
    calls not optional, and this module reuses rather than re-declares."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await fetch_list(http, "ls055350410")

    assert seen[0].headers["x-imdb-client-name"] == "imdb-web-next"
    assert str(seen[0].url) == "https://api.graphql.imdb.com/"


async def test_the_pinned_query_and_variables_are_sent_verbatim():
    """The query text is the contract with IMDb, and it is asserted whole
    rather than by keyword: a query that still contains 'titleListItemSearch'
    but lost its ``pageInfo`` would pass a substring check and silently stop
    paging."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await fetch_list(http, "ls055350410")

    first = _body(seen[0])
    assert first["query"] == LIST_QUERY
    assert first["variables"] == {"id": "ls055350410", "first": PAGE_SIZE}
    assert "sort" not in first["query"], (
        "omitting `sort` is what keeps IMDb's own list order; naming one would "
        "silently reorder every existing collection"
    )
    assert "titleType { id }" in first["query"], (
        "the type is what tells an episode id from a film's; a query that stopped "
        "asking for it would send every entry back to being unfilterable"
    )


async def test_the_second_page_is_asked_for_with_the_first_pages_cursor():
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        await fetch_list(http, "ls055350410")

    cursor = load("imdb_list_p1.json")["data"]["list"]["titleListItemSearch"][
        "pageInfo"]["endCursor"]
    assert len(seen) == 2
    assert _body(seen[1])["variables"] == {
        "id": "ls055350410", "first": PAGE_SIZE, "after": cursor
    }


async def test_paging_stops_when_the_last_page_says_so():
    """Page 2's recorded ``hasNextPage`` is false, so there is no third
    request -- the loop reads the flag rather than counting empty pages."""
    seen: list = []
    async with httpx.AsyncClient(transport=_paged(seen)) as http:
        ids = await fetch_list(http, "ls055350410")

    assert len(seen) == 2
    assert len(ids) == 5


# --- the transport: order and content -----------------------------------------


async def test_entries_are_returned_in_list_order_across_pages():
    """``(id, title type)`` pairs, and the type comes back alongside the id
    rather than being fetched per title -- one entry, one round trip's share."""
    async with httpx.AsyncClient(transport=_paged()) as http:
        entries = await fetch_list(http, "ls055350410")

    assert entries == [
        ("tt0039152", "movie"), ("tt0057569", "movie"),  # page 1, recorded order
        ("tt0013442", "movie"),
        ("tt0047162", "movie"), ("tt0023694", "movie"),  # page 2
    ]


async def test_an_entry_imdb_names_no_type_for_is_data_not_drift():
    """The id is pinned and a wrong one raises; the type is not. A null
    ``titleType`` is one entry the builder drops, where raising would take the
    whole collection down over a field IMDb owes nobody."""
    async with httpx.AsyncClient(transport=_typed([("tt0039152", None)])) as http:
        assert await fetch_list(http, "ls055350410") == [("tt0039152", None)]


async def test_a_list_that_really_is_empty_is_data_not_a_failure():
    """The one empty result this module will hand back: IMDb positively
    reporting no entries and no next page. Same judgement ``tvdb_list``
    makes."""
    payload = {"data": {"list": {"titleListItemSearch": {
        "total": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": [],
    }}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        assert await fetch_list(http, "ls055350410") == []


# --- drift fails loudly --------------------------------------------------------


async def test_the_edge_shape_this_module_pins_is_the_one_it_demands():
    """THE drift test. ``{"listItem": {"id": …}}`` is what the IMDb list
    schema plausibly looks like -- it is the shape this module was first
    written against and the live endpoint rejected -- so it is exactly the
    subtly-wrong response an upstream change would produce. An implementation
    that read the id defensively (``edge.get("title", {}).get("id")``, skip if
    missing) would return an EMPTY list here, and empty one layer down means
    "remove every member"."""
    payload = {"data": {"list": {"titleListItemSearch": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "edges": [{"listItem": {"id": "tt0039152"}}],
    }}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift) as caught:
            await fetch_list(http, "ls055350410")

    message = str(caught.value)
    assert "listItem" in message, "the drift raise must name the shape it found"
    assert "ls055350410" in message


@pytest.mark.parametrize(
    "search,expected",
    [
        ({"pageInfo": {"hasNextPage": False}, "edges": None}, "'edges'"),
        ({"pageInfo": {"hasNextPage": False}, "edges": {}}, "'edges'"),
        ({"edges": []}, "'pageInfo'"),
        ({"pageInfo": {"hasNextPage": False}, "edges": [{"title": {"id": 12345}}]}, "12345"),
        ({"pageInfo": {"hasNextPage": False}, "edges": [{"title": {"id": "nm0000138"}}]}, "nm0000138"),
        ({"pageInfo": {"hasNextPage": False}, "edges": ["tt0039152"]}, "tt0039152"),
    ],
)
async def test_an_unexpected_shape_raises_naming_what_was_found(search, expected):
    payload = {"data": {"list": {"titleListItemSearch": search}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift) as caught:
            await fetch_list(http, "ls055350410")

    assert expected in str(caught.value)


async def test_a_missing_search_object_raises():
    payload = {"data": {"list": {"id": "ls055350410"}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift, match="titleListItemSearch"):
            await fetch_list(http, "ls055350410")


async def test_a_renamed_root_field_raises_naming_the_fields_that_were_there():
    payload = {"data": {"titleList": {"titleListItemSearch": {
        "pageInfo": {"hasNextPage": False}, "edges": [],
    }}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift) as caught:
            await fetch_list(http, "ls055350410")

    assert "titleList" in str(caught.value)


async def test_a_body_that_is_not_an_object_raises():
    async with httpx.AsyncClient(transport=_answers([1, 2, 3])) as http:
        with pytest.raises(ImdbListDrift):
            await fetch_list(http, "ls055350410")


async def test_another_page_promised_without_a_cursor_raises():
    """Rather than looping forever, or silently keeping half a list."""
    payload = {"data": {"list": {"titleListItemSearch": {
        "pageInfo": {"hasNextPage": True, "endCursor": None},
        "edges": [{"title": {"id": "tt0039152"}}],
    }}}}
    async with httpx.AsyncClient(transport=_answers(payload)) as http:
        with pytest.raises(ImdbListDrift, match="endCursor"):
            await fetch_list(http, "ls055350410")


async def test_a_list_that_does_not_exist_is_refused_not_emptied():
    """IMDb answers a null node with no error for an id it does not know."""
    async with httpx.AsyncClient(transport=_answers({"data": {"list": None}})) as http:
        with pytest.raises(ImdbListRefused, match="ls055350410"):
            await fetch_list(http, "ls055350410")


async def test_a_graphql_errors_array_is_refused_even_on_a_200():
    """A private watchlist comes back as HTTP 200 with ``errors`` and a null
    node -- the recorded ``imdb_watchlist_private.json``. Checking the status
    code alone would read that as success."""
    async with httpx.AsyncClient(
        transport=_answers(load("imdb_watchlist_private.json"))
    ) as http:
        with pytest.raises(ImdbListRefused, match="Permission denied"):
            await fetch_list(http, "ls055350410")


async def test_an_http_error_raises_rather_than_returning_nothing():
    async with httpx.AsyncClient(transport=_answers({}, status=403)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_list(http, "ls055350410")


async def test_the_page_loop_is_capped():
    """A cursor that never terminates -- a bug at either end -- must cost a
    bounded number of requests, not an unbounded one."""
    seen: list = []
    payload = {"data": {"list": {"titleListItemSearch": {
        "pageInfo": {"hasNextPage": True, "endCursor": "more"},
        "edges": [{"title": {"id": "tt0039152"}}],
    }}}}
    async with httpx.AsyncClient(transport=_answers(payload, seen)) as http:
        ids = await fetch_list(http, "ls055350410")

    assert len(seen) == MAX_PAGES
    assert len(ids) == MAX_PAGES


# --- imdb_list -----------------------------------------------------------------


async def test_the_list_builder_namespaces_every_id():
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await REGISTRY["imdb_list"].build(_ctx(http, list="ls055350410"))

    assert result.ids == [
        ("imdb", "tt0039152"), ("imdb", "tt0057569"), ("imdb", "tt0013442"),
        ("imdb", "tt0047162"), ("imdb", "tt0023694"),
    ]
    assert result.summary is None and result.poster_kind is None


async def test_the_list_builder_runs_on_a_show_library_too():
    """Still no library-type guard -- a list is allowed on either library. The
    recorded list is five films, so a Show library keeps none of them, and that
    is the filter answering rather than the builder refusing."""
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await REGISTRY["imdb_list"].build(
            _ctx(http, library_type="Show", list="ls055350410")
        )

    assert result.ids == []


# --- the title-type filter ------------------------------------------------------
#
# THE production bug this fixes. Three of the shipped universe lists are episode
# dumps -- Kometa's Arrowverse list is 977 ``tvEpisode`` entries and one
# ``video``, with no series on it at all -- and an episode's ``tt`` id is in
# neither library's guid space, so every one of them could only ever be reported
# unresolved. The filter is about what an id CAN mean, not what the operator
# meant, which is why it is not optional and not configurable.


async def test_a_movie_library_keeps_only_what_it_could_own():
    """Films, TV movies, shorts and standalone videos: Plex files all four
    under Movies, each with its own ``imdb://`` guid. Series, episodes, games
    and the untyped entry cannot resolve there and never reach the resolver."""
    async with httpx.AsyncClient(transport=_typed(MIXED)) as http:
        result = await REGISTRY["imdb_list"].build(_ctx(http, list="ls055350410"))

    assert result.ids == [
        ("imdb", "tt1000001"), ("imdb", "tt1000002"),
        ("imdb", "tt1000003"), ("imdb", "tt1000004"),
    ]


async def test_a_show_library_keeps_only_series_and_mini_series():
    """``tvMovie`` is excluded on purpose and is the interesting half: Plex
    files one as a movie, so its id in a Show collection is as unresolvable as
    an episode's. Same call ``imdb_search.TITLE_TYPE_IDS`` makes."""
    async with httpx.AsyncClient(transport=_typed(MIXED)) as http:
        result = await REGISTRY["imdb_list"].build(
            _ctx(http, library_type="Show", list="ls055350410")
        )

    assert result.ids == [("imdb", "tt2000001"), ("imdb", "tt2000002")]


async def test_the_kept_entries_stay_in_list_order():
    """Source order is the collection's custom order, so dropping an entry must
    not reorder the ones around it -- the mixed list interleaves a dropped
    ``tvEpisode`` between two kept series."""
    entries = [
        ("tt2000001", "tvSeries"),
        ("tt3000001", "tvEpisode"),
        ("tt2000002", "tvMiniSeries"),
    ]
    async with httpx.AsyncClient(transport=_typed(entries)) as http:
        result = await REGISTRY["imdb_list"].build(
            _ctx(http, library_type="Show", list="ls055350410")
        )

    assert result.ids == [("imdb", "tt2000001"), ("imdb", "tt2000002")]


async def test_a_type_this_repository_does_not_know_is_dropped():
    """An unrecognised type is far likelier to be a new bulk type than a film
    IMDb re-labelled, and the debug line below is what makes the guess visible.
    Keeping it instead would put the unresolvable ids straight back."""
    async with httpx.AsyncClient(transport=_typed([("tt4000001", "musicVideo")])) as http:
        result = await REGISTRY["imdb_list"].build(_ctx(http, list="ls055350410"))

    assert result.ids == []


async def test_what_was_dropped_is_logged_by_count_and_by_type(caplog):
    """Not just the count. A list quietly losing entries to a type id nobody has
    seen before is IMDb drift, and this line is the only place it can surface."""
    logger = "autoposter.collections.builders.imdb_lists"
    with caplog.at_level(logging.DEBUG, logger=logger):
        async with httpx.AsyncClient(transport=_typed(MIXED)) as http:
            await REGISTRY["imdb_list"].build(_ctx(http, list="ls055350410"))

    assert "dropped 5 entries" in caplog.text
    assert "tvEpisode" in caplog.text and "videoGame" in caplog.text
    assert "tvSeries" in caplog.text and "tvMiniSeries" in caplog.text
    assert "unknown" in caplog.text, "an entry with no type at all is named too"
    assert "ls055350410" in caplog.text and "Movies" in caplog.text


async def test_nothing_is_logged_when_the_whole_list_is_kept(caplog):
    """The line reports an exception, not a routine. A list of films on a Movie
    library drops nothing and says nothing."""
    logger = "autoposter.collections.builders.imdb_lists"
    with caplog.at_level(logging.DEBUG, logger=logger):
        async with httpx.AsyncClient(transport=_paged()) as http:
            result = await REGISTRY["imdb_list"].build(_ctx(http, list="ls055350410"))

    assert len(result.ids) == 5
    assert "dropped" not in caplog.text


@pytest.mark.parametrize(
    "value",
    [
        "ur000000001",                                 # the user id, by mistake
        "https://www.imdb.com/list/ls055350410/",       # the whole URL, pasted
        "ls055350410/",
        "LS055350410",
        "ls",
        "055350410",
    ],
)
async def test_a_value_that_is_not_a_list_id_is_refused(value):
    with pytest.raises(ValidationError, match="IMDb list id"):
        await REGISTRY["imdb_list"].build(_ctx(None, list=value))


async def test_the_list_builder_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_list"].build(
            _ctx(None, list="ls055350410", limit=10)
        )


async def test_the_list_builder_requires_a_list():
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_list"].build(_ctx(None))


# --- imdb_watchlist ------------------------------------------------------------


async def test_the_watchlist_builder_reads_a_public_watchlist():
    seen: list = []
    transport = _answers(load("imdb_watchlist.json"), seen)
    async with httpx.AsyncClient(transport=transport) as http:
        result = await REGISTRY["imdb_watchlist"].build(_ctx(http, user="ur000000001"))

    assert result.ids == [
        ("imdb", "tt0039152"), ("imdb", "tt0057569"), ("imdb", "tt0013442"),
    ]
    body = _body(seen[0])
    assert body["query"] == WATCHLIST_QUERY
    assert body["variables"] == {"user": "ur000000001", "first": PAGE_SIZE}
    assert "WATCH_LIST" in body["query"], (
        "the enum value is WATCH_LIST -- the endpoint rejects 'WATCHLIST'"
    )


async def test_a_private_watchlist_is_refused_naming_the_user():
    """The recorded FORBIDDEN response. It arrives as HTTP 200, so an
    implementation that trusted the status code would build an empty
    collection and remove every member of the last good one."""
    transport = _answers(load("imdb_watchlist_private.json"))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ImdbListRefused) as caught:
            await REGISTRY["imdb_watchlist"].build(_ctx(http, user="ur000000002"))

    assert "ur000000002" in str(caught.value)


async def test_an_unknown_user_is_refused():
    """IMDb answers a null node with no error for a user it does not know."""
    transport = _answers({"data": {"predefinedList": None}})
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ImdbListRefused, match="ur99999999"):
            await REGISTRY["imdb_watchlist"].build(_ctx(http, user="ur99999999"))


@pytest.mark.parametrize(
    "value",
    [
        "ls055350410",                                  # the list id, by mistake
        "https://www.imdb.com/user/ur000000001/watchlist",
        "UR000000001",
        "ur",
        "000000001",
    ],
)
async def test_a_value_that_is_not_a_user_id_is_refused(value):
    with pytest.raises(ValidationError, match="IMDb user id"):
        await REGISTRY["imdb_watchlist"].build(_ctx(None, user=value))


async def test_the_watchlist_builder_refuses_params_it_does_not_understand():
    with pytest.raises(ValidationError):
        await REGISTRY["imdb_watchlist"].build(
            _ctx(None, user="ur000000001", sort="added")
        )


# --- registration ---------------------------------------------------------------


def test_both_builders_are_registered_under_their_own_names():
    for name in ("imdb_list", "imdb_watchlist"):
        assert REGISTRY[name].type_name == name
