"""Public IMDb lists, watchlists and advanced search, over ``charts.py``'s endpoint.

Same transport as the charts: the same ``api.graphql.imdb.com`` endpoint, the
same mandatory ``x-imdb-client-name`` header (without it the endpoint answers
403), a raw POST rather than ``providers.fetch.fetch_json`` -- that helper is
JSON-over-GET and caches by URL, neither of which fits a GraphQL POST -- and
the same raise-on-everything posture, because the failure that matters cuts
both ways: a wrong non-empty result overwrites the collection, and an empty
one is read one layer down as "make no changes"
(``lists.reconcile_list_collection``) -- so a drift-empty response freezes
the collection instead of erroring, indistinguishable from a healthy no-op
until it silently stops updating.

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

**Three roots, one walker.** 8c added ``advancedTitleSearch`` (row 81), whose
connection sits directly under ``data`` rather than under a named list object,
and whose edges are ``{node: {title: {id}}}`` rather than ``{title: {id}}``.
Both differences are data now -- ``_connection`` takes the path from ``data``
down to the connection and ``_ids`` takes the path from an edge down to the
id -- so the level-by-level validation, the excerpting, the errors-before-
status check, the cursor loop and the page cap are written once and every root
gets the same loud failure. The alternative, a second copy of that walk for the
search root, is exactly the copy that would drift into being defensive.

**The two list roots ask for more than an id.** ``LIST_QUERY`` and
``WATCHLIST_QUERY`` both select each entry's ``titleType { id }``, because a
list of either kind can hold anything IMDb has a page for -- including the ~950
``tvEpisode`` entries on Kometa's Star Trek universe list, whose ids belong to
no Plex library's guid space at all. So ``_fetch`` takes a second, optional edge
path and both list fetchers return ``(id, type)`` pairs where the search root
still returns bare ids -- it constrains the title types on the way out
(``builders/imdb_search.TITLE_TYPE_IDS``) and so is never sent one it did not
ask for. That path is the one piece of the shape below not covered by the
2026-08-25 live recordings; it was read off the live lists during the universe
diagnosis, and unlike the id path it is *not* pinned -- see ``_entries`` for why
a missing type is data.
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
    "SEARCH_QUERY",
    "WATCHLIST_QUERY",
    "ImdbListDrift",
    "ImdbListRefused",
    "fetch_list",
    "fetch_search",
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
#
# ``titleType { id }`` rides along because a public list is not a list of one
# kind of thing. Three of Kometa's universe lists are almost entirely
# ``tvEpisode`` -- Arrowverse ``ls566667558`` is 977 episodes and one ``video``,
# with no series on it at all -- and an episode's ``tt`` id is in neither a
# Movie library's nor a Show library's guid space, so emitting one can only
# produce an unresolved id. The id is the *builder's* to act on
# (``builders/imdb_lists.py``); asking for it is this module's.
LIST_QUERY = (
    "query ListItems($id: ID!, $first: Int!, $after: String) {"
    " list(id: $id) { titleListItemSearch(first: $first, after: $after) {"
    " total pageInfo { hasNextPage endCursor }"
    " edges { title { id titleType { id } } } } } }"
)

# ``$after`` is ``String``, not ``ID``: the endpoint rejects the ``ID`` form
# outright ("used in position expecting type String"), which is the kind of
# detail only a live request can settle.
#
# ``titleType { id }`` rides along for ``LIST_QUERY``'s reason (roadmap row
# 168): a watchlist can hold anything IMDb has a page for, an episode included,
# and an episode's id resolves in no library.
WATCHLIST_QUERY = (
    "query WatchlistItems($user: ID!, $first: Int!, $after: String) {"
    " predefinedList(classType: WATCH_LIST, userId: $user) { id"
    " titleListItemSearch(first: $first, after: $after) {"
    " total pageInfo { hasNextPage endCursor }"
    " edges { title { id titleType { id } } } } } }"
)

# ``constraints`` and ``sort`` travel as variables rather than inline literals
# so the query text stays one constant: an inline-literal query would be a
# different string per definition, which is a contract nothing can assert on.
# Both are declared non-null although the arguments are nullable -- a search
# this module builds always carries a title-type constraint and a sort, and a
# null arriving there is a bug worth a validator error rather than a silently
# unfiltered 31-million-title result. ``$after`` is ``String``, as on the list
# roots; the endpoint rejects the ``ID`` form.
#
# The connection is ``data.advancedTitleSearch`` itself -- there is no named
# search object in between, which is why ``_connection`` takes a path -- and the
# edge is ``{node: {title: {id}}}``, one level deeper than a list's ``{title:
# {id}}``. Verified live 2026-08-25; see this task's report for the walk.
SEARCH_QUERY = (
    "query AdvancedTitleSearch($constraints: AdvancedTitleSearchConstraints!,"
    " $sort: AdvancedTitleSearchSort!, $first: Int!, $after: String) {"
    " advancedTitleSearch(constraints: $constraints, sort: $sort, first: $first,"
    " after: $after) { total pageInfo { hasNextPage endCursor }"
    " edges { node { title { id } } } } }"
)

_IMDB_ID = re.compile(r"^tt\d+$")

# What ``_connection`` says when the root node came back null with no ``errors``
# array. The lists' wording, because for them that is IMDb's way of saying "no
# such list"; the search root passes its own, where "does not exist" would be
# nonsense.
_NULL_ROOT = "does not exist, or is not public -- IMDb answered null"

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


def _connection(
    payload: object,
    path: tuple[str, ...],
    subject: str,
    null_root: str = _NULL_ROOT,
    null_class: type[Exception] = ImdbListRefused,
) -> tuple[list, dict]:
    """``(edges, pageInfo)`` out of one response, or a raise naming the shape.

    ``path`` is the field names from ``data`` down to the connection object:
    ``("list", "titleListItemSearch")`` for a list, ``("advancedTitleSearch",)``
    for the search root, whose connection has no wrapper.

    ``null_class`` is the exception a null root raises -- ``ImdbListRefused`` by
    default, which is right for the lists (a null root there is IMDb saying "no
    such list"), but wrong for the search root, whose null root is an
    unrecognised shape rather than an operator-fixable refusal. The caller picks.
    """
    if not isinstance(payload, dict):
        raise ImdbListDrift(
            f"{subject}: IMDb answered {_excerpt(payload)}, not a JSON object"
        )
    errors = payload.get("errors")
    if errors:
        # IMDb reports a private watchlist -- and a search constraint it will
        # not accept -- as HTTP 200 with an ``errors`` array and a null node, so
        # the status code alone never sees it. The message is IMDb's own and
        # carries no credential: this endpoint is unauthenticated, which is the
        # whole reason it is public-only.
        raise ImdbListRefused(f"{subject}: IMDb refused the request -- {_excerpt(errors)}")
    node = payload.get("data")
    if not isinstance(node, dict):
        raise ImdbListDrift(
            f"{subject}: the response carried no 'data' object -- got {_excerpt(payload)}"
        )
    root, *rest = path
    if root not in node:
        raise ImdbListDrift(
            f"{subject}: the response has no {root!r} field; 'data' carried "
            f"{sorted(node)}"
        )
    node = node[root]
    if node is None:
        raise null_class(f"{subject} {null_root}")
    if not isinstance(node, dict):
        raise ImdbListDrift(f"{subject}: {root!r} was {_excerpt(node)}, not an object")
    for field in rest:
        child = node.get(field)
        if not isinstance(child, dict):
            raise ImdbListDrift(
                f"{subject}: expected an object under {field!r}, got {_excerpt(child)}"
            )
        node = child
    edges = node.get("edges")
    if not isinstance(edges, list):
        raise ImdbListDrift(
            f"{subject}: 'edges' was {_excerpt(edges)}, not a list"
        )
    page_info = node.get("pageInfo")
    if not isinstance(page_info, dict):
        raise ImdbListDrift(
            f"{subject}: 'pageInfo' was {_excerpt(page_info)}, not an object"
        )
    return edges, page_info


def _shape(path: tuple[str, ...]) -> str:
    """``("node", "title", "id")`` as ``{'node': {'title': {'id': 'tt…'}}}``.

    The drift message quotes the shape it demanded next to the one it got, and
    building that literal from the path is what keeps the two in step: a
    hand-written literal would keep naming the old shape after a path change.
    """
    text = "'tt…'"
    for field in reversed(path):
        text = "{%r: %s}" % (field, text)
    return text


def _walk(value: object, path: tuple[str, ...]) -> object:
    """One edge followed down a field path, or None where the path runs out."""
    for field in path:
        value = value.get(field) if isinstance(value, dict) else None
    return value


def _entries(
    edges: list,
    path: tuple[str, ...],
    subject: str,
    offset: int,
    type_path: tuple[str, ...] | None = None,
) -> list[tuple[str, str | None]]:
    """One page's ``(title id, IMDb title type id)`` pairs, in page order.

    The id is pinned. Every edge must be exactly the expected shape --
    ``{"title": {"id": "tt…"}}`` on the list roots, ``{"node": {"title": {"id":
    "tt…"}}}`` on the search root. An edge that is not is a raise rather than a
    skip: one skipped entry is a title silently missing from the collection,
    and a shape change would skip all of them at once.

    The title type is deliberately *not* pinned, and the asymmetry is the
    point. It is asked for on the two list roots only (``type_path`` is None
    for the search root), and its consumer treats an unknown type as one
    entry to drop -- so a missing one costs a single list member, where
    raising would cost the whole collection. Non-string values fold to None
    for the same reason.
    """
    entries: list[tuple[str, str | None]] = []
    for position, edge in enumerate(edges, start=offset + 1):
        value = _walk(edge, path)
        if not isinstance(value, str) or not _IMDB_ID.match(value):
            raise ImdbListDrift(
                f"{subject}: entry {position} is not the {_shape(path)} shape "
                f"this module pins -- got {_excerpt(edge)}"
            )
        title_type = _walk(edge, type_path) if type_path else None
        entries.append((value, title_type if isinstance(title_type, str) else None))
    return entries


async def _fetch(
    http: httpx.AsyncClient,
    query: str,
    path: tuple[str, ...],
    edge_path: tuple[str, ...],
    variables: dict,
    subject: str,
    null_root: str = _NULL_ROOT,
    null_class: type[Exception] = ImdbListRefused,
    type_path: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[tuple[str, str | None]]:
    """Walk one connection's cursor pages and return its entries in source order.

    An entry is ``(id, title type)``, the type being None on every root that
    does not ask for one -- see ``_entries``.
    """
    entries: list[tuple[str, str | None]] = []
    after: str | None = None
    # Kometa's arithmetic, and the reason it is a REQUEST-level cap rather than a
    # slice of the answer: asking for 200 costs one request for 200 rather than
    # one for 250 thrown half away (``modules/imdb.py``'s ``_graphql_json``
    # sets ``first`` to the limit whenever the limit is under a page). The query
    # text is untouched -- ``first`` has always been a variable.
    first = PAGE_SIZE if limit is None else min(limit, PAGE_SIZE)
    for page in range(1, MAX_PAGES + 1):
        page_variables = dict(variables, first=first)
        if after is not None:
            page_variables["after"] = after
        response = await http.post(
            GRAPHQL_URL, headers=HEADERS, json={"query": query, "variables": page_variables}
        )
        response.raise_for_status()
        edges, page_info = _connection(response.json(), path, subject, null_root, null_class)
        entries += _entries(edges, edge_path, subject, len(entries), type_path)
        # Before the ``hasNextPage`` question, not after it: a page that answers
        # more ids than were asked for is truncated here, and a page that
        # promises another one is simply not asked for.
        if limit is not None and len(entries) >= limit:
            return entries[:limit]
        if not page_info.get("hasNextPage"):
            return entries
        after = page_info.get("endCursor")
        if not isinstance(after, str) or not after:
            raise ImdbListDrift(
                f"{subject}: page {page} reports another page but carried no "
                f"'endCursor' -- got {_excerpt(page_info.get('endCursor'))}"
            )
    logger.warning(
        "%s: stopped at the %d-page cap with %d id(s); IMDb says there are more",
        subject, MAX_PAGES, len(entries),
    )
    return entries


async def fetch_list(
    http: httpx.AsyncClient, list_id: str
) -> list[tuple[str, str | None]]:
    """The public list's ``(IMDb id, title type id)`` entries, in list order.

    The type is IMDb's own vocabulary -- ``movie``, ``tvSeries``, ``tvEpisode``
    -- and None for an entry IMDb named no type for. The caller decides what a
    type means; see ``builders/imdb_lists.py`` for why it has to.
    """
    return await _fetch(
        http,
        LIST_QUERY,
        ("list", "titleListItemSearch"),
        ("title", "id"),
        {"id": list_id},
        f"IMDb list {list_id!r}",
        type_path=("title", "titleType", "id"),
    )


async def fetch_watchlist(
    http: httpx.AsyncClient, user_id: str
) -> list[tuple[str, str | None]]:
    """The user's public watchlist ``(IMDb id, title type id)`` entries, in
    watchlist order.

    Public only. There is no anonymous route to a private one, and IMDb says
    so with a FORBIDDEN error rather than an empty list -- which
    ``_connection`` turns into ``ImdbListRefused`` naming the watchlist.

    The type rides along for ``fetch_list``'s reason (roadmap row 168): a
    watchlist can hold anything IMDb has a page for, an episode included,
    and an episode's ``tt`` id belongs to no Plex library's guid space at
    all. The caller decides what a type means; see ``builders/imdb_lists.py``
    for why it has to. Like the list root's, the type is not pinned -- a
    missing one is one entry for the builder to drop, not a raise.
    """
    return await _fetch(
        http,
        WATCHLIST_QUERY,
        ("predefinedList", "titleListItemSearch"),
        ("title", "id"),
        {"user": user_id},
        f"the public IMDb watchlist of {user_id!r}",
        type_path=("title", "titleType", "id"),
    )


async def fetch_search(
    http: httpx.AsyncClient,
    constraints: dict,
    sort: dict,
    subject: str,
    limit: int | None = None,
) -> list[str]:
    """One ``advancedTitleSearch``'s ids, in the order the sort produced them.

    ``constraints`` and ``sort`` are already in IMDb's own vocabulary -- built
    by ``builders/imdb_search.py``, which owns the operator-facing names and the
    validation that keeps a typo from reaching the wire. That split matters
    here: IMDb answers an unknown genre or title-type id with ``total: 0``
    rather than an error, so a constraint value this repository has not pinned
    is indistinguishable from an honest empty result, and an empty result one
    layer down removes every member.

    Ids only, and no title-type filter here either: the search *constraint*
    already names the title types (``builders/imdb_search.TITLE_TYPE_IDS``), so
    IMDb never returns one that was not asked for.

    ``limit`` is the operator's ``limit:``, and it is Kometa's own arithmetic: the
    first page asks for exactly that many when it is under ``PAGE_SIZE``, and the
    walk stops as soon as that many ids are in hand. It is not a slice of a
    complete answer -- a keyword search can match tens of thousands of titles,
    and paying for all of them to keep two hundred is the cost this exists to
    refuse. None means the whole search, up to ``MAX_PAGES``.
    """
    entries = await _fetch(
        http,
        SEARCH_QUERY,
        ("advancedTitleSearch",),
        ("node", "title", "id"),
        {"constraints": constraints, "sort": sort},
        subject,
        null_root="came back null with no error, which is not a shape IMDb has "
        "ever answered this query with",
        null_class=ImdbListDrift,
        limit=limit,
    )
    return [value for value, _ in entries]
