"""Public IMDb lists and public watchlists, over the endpoint ``charts.py`` uses.

Same transport as the charts: the same ``api.graphql.imdb.com`` endpoint, the
same mandatory ``x-imdb-client-name`` header (without it the endpoint answers
403), a raw POST rather than ``providers.fetch.fetch_json`` -- that helper is
JSON-over-GET and caches by URL, neither of which fits a GraphQL POST -- and
the same raise-on-everything posture, because these collections use sync
semantics and an empty membership one layer down means "remove every member".

**The queries below were verified against the live endpoint on 2026-08-25**,
not recalled, and the fixtures under ``tests/fixtures/collections/`` are
recordings of what it answered. That mattered: introspection is refused
("Unauthorized introspection request"), so the schema was walked through the
validator's own error messages, and two plausible-looking guesses were wrong.
``edges { listItem { id } }`` does not exist (the edge field is ``title``),
and there is no ``user(id: …)`` root at all -- ``Query.user`` takes no
arguments and means *the authenticated viewer*, so another person's watchlist
is reachable only through ``predefinedList(classType: WATCH_LIST, userId:
"ur…")``. Those are exactly the mistakes a fixture written from memory would
have pinned as correct.

**Drift fails loudly.** The phase's named risk is IMDb changing this shape
under us, and the failure mode that risk actually produces is not an
exception -- it is ``payload["data"]["list"]["titleListItemSearch"]["edges"]``
quietly becoming ``[]`` or ``None`` and every affected collection being
emptied on the next pass. So every level of the response is checked and
anything unexpected raises ``ImdbListDrift`` **naming the shape that was
found**, and the only empty list this module will hand back is one IMDb
positively reported as empty (``edges: []`` with ``hasNextPage: false``),
which is data rather than failure -- the judgement ``tvdb_list`` already
makes.

Two failures are told apart because the engine's log line carries the
exception class name and nothing else: ``ImdbListRefused`` is IMDb answering
"no such list" or "that watchlist is private", which an operator fixes in
their config or their IMDb privacy settings, and ``ImdbListDrift`` is this
module's pins no longer matching IMDb, which only a code change fixes.
"""
import logging
import re

import httpx

from autoposter.collections.charts import GRAPHQL_URL, HEADERS

logger = logging.getLogger(__name__)

__all__ = [
    "LIST_QUERY",
    "MAX_PAGES",
    "PAGE_SIZE",
    "WATCHLIST_QUERY",
    "ImdbListDrift",
    "ImdbListRefused",
    "fetch_list",
    "fetch_watchlist",
]

# 250 is what the endpoint served without complaint (and what the Top 250
# chart asks for); 500 was accepted too, but a page size the API might start
# capping silently is a page size that would silently truncate a list.
PAGE_SIZE = 250
# The same cap ``providers/tmdb_lists.py`` puts on its page loop, for the same
# reason: a runaway or looping cursor must not turn one definition into an
# unbounded number of requests. 2,500 ids is far past any list an operator
# would sanely sync, and ``definition.limit`` trims after resolution anyway.
MAX_PAGES = 10

# No ``sort:`` argument, deliberately. Omitting it returns the list in its own
# order -- verified by comparing a single ``first: 250`` request against the
# same list walked three at a time, which agreed position for position -- and
# that order is the collection's custom order. Naming a sort would silently
# reorder every existing collection.
LIST_QUERY = (
    "query ListItems($id: ID!, $first: Int!, $after: String) {"
    " list(id: $id) { titleListItemSearch(first: $first, after: $after) {"
    " total pageInfo { hasNextPage endCursor } edges { title { id } } } } }"
)

# ``$after`` is ``String``, not ``ID``: the endpoint rejects the ``ID`` form
# outright ("used in position expecting type String"), which is the kind of
# detail only a live request can settle.
WATCHLIST_QUERY = (
    "query WatchlistItems($user: ID!, $first: Int!, $after: String) {"
    " predefinedList(classType: WATCH_LIST, userId: $user) { id"
    " titleListItemSearch(first: $first, after: $after) {"
    " total pageInfo { hasNextPage endCursor } edges { title { id } } } } }"
)

_IMDB_ID = re.compile(r"^tt\d+$")

# How much of an unexpected value goes in the message. Enough to recognise the
# shape, bounded so a drifted response cannot put a megabyte in a log line.
_EXCERPT = 200


class ImdbListRefused(Exception):
    """IMDb has no such list, or will not show it to an anonymous caller.

    An operator-fixable condition: a mistyped id, a deleted list, a watchlist
    whose owner has it set to private. Its own class rather than a
    ``ValueError`` so the engine's class-name-only log line tells this apart
    from the drift below -- one is a config problem, the other is ours.
    """


class ImdbListDrift(Exception):
    """The response did not have the shape this module pins.

    Raised in preference to returning fewer ids, or none. See the module
    docstring: a silently-degrading list builder empties live collections.
    """


def _excerpt(value: object) -> str:
    """A bounded repr of whatever IMDb sent instead of what was expected."""
    text = repr(value)
    return text if len(text) <= _EXCERPT else text[:_EXCERPT] + "…"


def _connection(payload: object, root: str, subject: str) -> tuple[list, dict]:
    """``(edges, pageInfo)`` out of one response, or a raise naming the shape."""
    if not isinstance(payload, dict):
        raise ImdbListDrift(
            f"{subject}: IMDb answered {_excerpt(payload)}, not a JSON object"
        )
    errors = payload.get("errors")
    if errors:
        # IMDb reports a private watchlist as HTTP 200 with an ``errors`` array
        # and ``data.<root>: null``, so the status code alone never sees it.
        # The message is IMDb's own and carries no credential -- this endpoint
        # is unauthenticated, which is the whole reason it is public-only.
        raise ImdbListRefused(f"{subject}: IMDb refused the request -- {_excerpt(errors)}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ImdbListDrift(
            f"{subject}: the response carried no 'data' object -- got {_excerpt(payload)}"
        )
    if root not in data:
        raise ImdbListDrift(
            f"{subject}: the response has no {root!r} field; 'data' carried "
            f"{sorted(data)}"
        )
    node = data[root]
    if node is None:
        raise ImdbListRefused(
            f"{subject} does not exist, or is not public -- IMDb answered null"
        )
    if not isinstance(node, dict):
        raise ImdbListDrift(f"{subject}: {root!r} was {_excerpt(node)}, not an object")
    search = node.get("titleListItemSearch")
    if not isinstance(search, dict):
        raise ImdbListDrift(
            f"{subject}: expected an object under 'titleListItemSearch', got "
            f"{_excerpt(search)}"
        )
    edges = search.get("edges")
    if not isinstance(edges, list):
        raise ImdbListDrift(
            f"{subject}: 'edges' was {_excerpt(edges)}, not a list"
        )
    page_info = search.get("pageInfo")
    if not isinstance(page_info, dict):
        raise ImdbListDrift(
            f"{subject}: 'pageInfo' was {_excerpt(page_info)}, not an object"
        )
    return edges, page_info


def _ids(edges: list, subject: str, offset: int) -> list[str]:
    """The title ids of one page, in page order.

    Every edge must be the pinned ``{"title": {"id": "tt…"}}``. An edge that
    is not is a raise rather than a skip: one skipped entry is a title
    silently missing from the collection, and a shape change would skip all of
    them at once.
    """
    ids: list[str] = []
    for position, edge in enumerate(edges, start=offset + 1):
        title = edge.get("title") if isinstance(edge, dict) else None
        value = title.get("id") if isinstance(title, dict) else None
        if not isinstance(value, str) or not _IMDB_ID.match(value):
            raise ImdbListDrift(
                f"{subject}: entry {position} is not the {{'title': {{'id': "
                f"'tt…'}}}} shape this module pins -- got {_excerpt(edge)}"
            )
        ids.append(value)
    return ids


async def _fetch(
    http: httpx.AsyncClient, query: str, root: str, variables: dict, subject: str
) -> list[str]:
    """Walk one list's cursor pages and return its ids in list order."""
    ids: list[str] = []
    after: str | None = None
    for page in range(1, MAX_PAGES + 1):
        page_variables = dict(variables, first=PAGE_SIZE)
        if after is not None:
            page_variables["after"] = after
        response = await http.post(
            GRAPHQL_URL, headers=HEADERS, json={"query": query, "variables": page_variables}
        )
        response.raise_for_status()
        edges, page_info = _connection(response.json(), root, subject)
        ids += _ids(edges, subject, len(ids))
        if not page_info.get("hasNextPage"):
            return ids
        after = page_info.get("endCursor")
        if not isinstance(after, str) or not after:
            raise ImdbListDrift(
                f"{subject}: page {page} reports another page but carried no "
                f"'endCursor' -- got {_excerpt(page_info.get('endCursor'))}"
            )
    logger.warning(
        "%s: stopped at the %d-page cap with %d id(s); IMDb says there are more",
        subject, MAX_PAGES, len(ids),
    )
    return ids


async def fetch_list(http: httpx.AsyncClient, list_id: str) -> list[str]:
    """The public list's IMDb ids, in list order."""
    return await _fetch(
        http, LIST_QUERY, "list", {"id": list_id}, f"IMDb list {list_id!r}"
    )


async def fetch_watchlist(http: httpx.AsyncClient, user_id: str) -> list[str]:
    """The user's public watchlist ids, in watchlist order.

    Public only. There is no anonymous route to a private one, and IMDb says
    so with a FORBIDDEN error rather than an empty list -- which
    ``_connection`` turns into ``ImdbListRefused`` naming the watchlist.
    """
    return await _fetch(
        http,
        WATCHLIST_QUERY,
        "predefinedList",
        {"user": user_id},
        f"the public IMDb watchlist of {user_id!r}",
    )
