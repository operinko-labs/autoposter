"""The public IMDb list and watchlist builders, and the GraphQL transport.

Never touches the real API -- MockTransport only. The fixtures under
``tests/fixtures/collections/imdb_list_*.json`` and ``imdb_watchlist*.json``
are recordings of what ``api.graphql.imdb.com`` actually answered on
2026-08-25 (list ``ls055350410``, a public watchlist, and a private one), so
what is pinned here is IMDb's shape rather than a plausible-looking guess at
it. Two guesses that a from-memory fixture would have frozen in were wrong:
the edge field is ``title`` and not ``listItem``, and there is no
``user(id:)`` root -- a watchlist is reached through ``predefinedList``.
"""
import json
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


async def test_ids_are_returned_in_list_order_across_pages():
    async with httpx.AsyncClient(transport=_paged()) as http:
        ids = await fetch_list(http, "ls055350410")

    assert ids == [
        "tt0039152", "tt0057569", "tt0013442",  # page 1, in the recorded order
        "tt0047162", "tt0023694",               # page 2
    ]


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
    """No library-type guard: an IMDb list may hold films and series at once,
    and the library already decides which half of it resolves."""
    async with httpx.AsyncClient(transport=_paged()) as http:
        result = await REGISTRY["imdb_list"].build(
            _ctx(http, library_type="Show", list="ls055350410")
        )

    assert len(result.ids) == 5


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
