"""TMDb's *list* surface: the endpoints a collection's membership comes from.

Separate from ``providers/tmdb.py`` (artwork) and ``facts/tmdb_facts.py``
(ratings and descriptive metadata) for the reason those two are separate from
each other: they ask different endpoints for different reasons and share only
the bearer token. What is genuinely new here is paging -- nothing else in this
repository reads more than one page of a TMDb response -- and the rule that a
404 is an error rather than an empty answer.

**Why 404 raises here and returns ``None`` everywhere else.** ``fetch_json``
turns a 404 into ``None``, which every artwork and facts client reads as "TMDb
has nothing for this title" and degrades gracefully. A *collection* cannot:
an empty membership means "remove every member" one layer down
(``lists.reconcile_list_collection``), so a list id that has been deleted or
made private would empty a live collection on the next sync. Every method here
therefore raises ``TmdbListRefused`` naming what was asked for, and the engine
contains that as one dead source.

**Paging.** Two response shapes, and the client honours whichever one the
endpoint uses to say how much there is:

- the paged shape (``/movie/popular``, ``/discover/*``) counts pages in
  ``total_pages`` and holds its entries in ``results``;
- the v3 list-details shape (``/list/{id}``) holds its entries in ``items``
  and counts *members* in ``item_count``.

Reading both stop conditions costs four lines and buys the client an answer
that stays correct if TMDb ever ignores ``page`` on the list endpoint: the
loop would otherwise collect the same first page ``max_pages`` times over.
Both are bounded by ``max_pages`` regardless, because a runaway upstream must
cost a bounded number of requests -- a definition's ``limit`` trims after
resolution and cannot stop a fetch that has already happened.

**Companies, networks and keywords go through ``/discover``**, not through
``/company/{id}/movies`` or ``/keyword/{id}/movies``: TMDb marks those
deprecated in favour of the discover filters, and there is no listing endpoint
for a network at *all*, so discover is the only form that covers all three.
One shape, one paging rule, and the filter name is the only thing that varies.
"""
from collections.abc import Mapping

import httpx

from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

BASE_URL = "https://api.themoviedb.org/3"

# How many pages one call will ever fetch. See the module docstring.
MAX_PAGES = 10

# Chart key -> the endpoint that answers it for each library type.
#
# A chart key is the operator's word ("popular") and the media type is the
# library the pass is running against, so one definition can serve a Movie and
# a Show library. A key with no entry for a library type is a chart that
# library type cannot build -- theatrical release windows have no TV form and
# airing schedules have no movie form -- and the builders' mismatch guard
# reads exactly this table to say so.
CHART_ENDPOINTS: dict[str, dict[str, str]] = {
    "popular": {"Movie": "/movie/popular", "Show": "/tv/popular"},
    "top_rated": {"Movie": "/movie/top_rated", "Show": "/tv/top_rated"},
    "now_playing": {"Movie": "/movie/now_playing"},
    "upcoming": {"Movie": "/movie/upcoming"},
    "airing_today": {"Show": "/tv/airing_today"},
    "on_the_air": {"Show": "/tv/on_the_air"},
    "trending_day": {"Movie": "/trending/movie/day", "Show": "/trending/tv/day"},
    "trending_week": {"Movie": "/trending/movie/week", "Show": "/trending/tv/week"},
}

__all__ = ["CHART_ENDPOINTS", "MAX_PAGES", "TmdbListClient", "TmdbListRefused"]


class TmdbListRefused(Exception):
    """TMDb could not answer this, or answered something unusable.

    One class for the three cases -- the id does not exist, the response has
    no entry array, an entry has no id -- because the engine logs the class
    name either way and the message says which. It carries the path and the
    id asked for and nothing else; the token lives in a header and never
    enters a message.
    """


class TmdbListClient:
    """Reads collection membership from TMDb. The token is a v4 read access
    token, exactly as ``providers/tmdb.py``'s is."""

    name = "TMDB"

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
        max_pages: int = MAX_PAGES,
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_pages = max_pages

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "accept": "application/json"}

    async def _get(self, path: str, params: dict, subject: str) -> dict:
        """One request, through the cache, with 404 turned back into an error.

        The cache key is built from method, URL and params only
        (``build_cache_key``), so the bearer token in the header is never
        persisted and rotating it does not orphan a single cached page.
        """
        url = f"{BASE_URL}{path}"
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(url, params=params, headers=self._headers()),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            raise TmdbListRefused(
                f"{subject} does not exist: TMDb answered 404 for {path}. Building "
                "an empty collection instead would remove every member it has."
            )
        return payload

    async def _paged(self, path: str, params: dict, *, subject: str, items_key: str) -> list[str]:
        """Every page of ``path``, in order, bounded by ``max_pages``."""
        ids: list[str] = []
        for page in range(1, self._max_pages + 1):
            payload = await self._get(path, {**params, "page": page}, subject)
            entries = payload.get(items_key)
            if not isinstance(entries, list):
                raise TmdbListRefused(
                    f"{subject}: TMDb's response carries no {items_key!r} array"
                )
            if not entries:
                # The page past the end. A source that really is empty is data,
                # not a failure -- unlike a 404, which is the id being wrong.
                break
            ids += _ids(entries, subject)
            total_pages = payload.get("total_pages")
            if isinstance(total_pages, int) and page >= total_pages:
                break
            item_count = payload.get("item_count")
            if isinstance(item_count, int) and len(ids) >= item_count:
                break
        return ids

    async def chart(
        self, path: str, *, region: str | None = None, language: str | None = None
    ) -> list[str]:
        """One chart, in rank order. ``path`` comes from ``CHART_ENDPOINTS``.

        ``region`` is the parameter that changes *membership*: TMDb resolves
        ``now_playing`` and ``upcoming`` against one country's release dates.
        Both are omitted entirely when unset rather than sent empty, so an
        unconfigured chart keeps the request -- and therefore the cache key --
        it has always had.
        """
        params: dict[str, object] = {}
        if region:
            params["region"] = region
        if language:
            params["language"] = language
        return await self._paged(
            path, params, subject=f"the TMDb chart at {path}", items_key="results"
        )

    async def list_items(self, list_id: int) -> list[str]:
        """A public v3 list's members, in list order.

        No ``language``: the only thing read out of the response is ids, and
        every extra parameter is a second cache key for an identical answer.
        """
        return await self._paged(
            f"/list/{list_id}", {}, subject=f"TMDb list {list_id}", items_key="items"
        )

    async def collection_parts(self, collection_id: int) -> list[str]:
        """A franchise collection's parts, in TMDb's order.

        Not paged -- ``/collection/{id}`` returns every part in one response --
        so this is the one method that sends no ``page`` at all.
        """
        subject = f"TMDb collection {collection_id}"
        payload = await self._get(f"/collection/{collection_id}", {}, subject)
        parts = payload.get("parts")
        if not isinstance(parts, list):
            raise TmdbListRefused(f"{subject}: TMDb's response carries no 'parts' array")
        return _ids(parts, subject)

    async def discover(self, media_type: str, filters: Mapping[str, object]) -> list[str]:
        """``/discover/{movie,tv}`` under one filter -- a company, a network or
        a keyword. See the module docstring for why this rather than the
        deprecated per-entity listing endpoints."""
        path = f"/discover/{media_type}"
        described = ", ".join(f"{name}={value}" for name, value in sorted(filters.items()))
        return await self._paged(
            path, dict(filters), subject=f"TMDb discover ({described})", items_key="results"
        )


def _ids(entries: list, subject: str) -> list[str]:
    """The ``id`` of every entry, as strings, in the order TMDb listed them.

    An entry without an id raises rather than being skipped: a silently
    dropped member is a slightly smaller collection every pass and no way at
    all to notice, which is the same argument ``text_file`` refuses an
    unparseable line on.
    """
    ids = []
    for entry in entries:
        value = entry.get("id") if isinstance(entry, dict) else None
        if value is None:
            raise TmdbListRefused(
                f"{subject}: an entry in TMDb's response has no 'id'. Skipping it "
                "would build a smaller collection with nothing to show for it."
            )
        ids.append(str(value))
    return ids
