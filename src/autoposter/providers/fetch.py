"""Shared JSON-fetching seam for provider clients.

TMDB, TVDB and Fanart each do the same thing around their own HTTP call: check
for a 404, raise on any other error status, decode JSON, and optionally read/write
that response through the cache. Only the request itself differs per client (TVDB
needs an auth header and a 401 retry; TMDB and Fanart just need a plain GET), so
this module takes the request as a callable and owns everything else. parse_*
functions always receive plain decoded JSON, whether or not caching is on.
"""
from collections.abc import Awaitable, Callable, Mapping

import httpx

from autoposter.providers.cache import ProviderCache, build_cache_key


async def fetch_json(
    *,
    method: str,
    url: str,
    params: Mapping[str, object] | None,
    request: Callable[[], Awaitable[httpx.Response]],
    cache: ProviderCache | None,
    ttl_seconds: int,
    cacheable: Callable[[object], bool] | None = None,
) -> dict | None:
    """Run one JSON request, optionally through the cache.

    Returns the decoded payload, or None for a 404 — the caller's confirmed
    "nothing there" case, which every client already turns into an empty
    candidate list. The cache key is derived only from ``method``/``url``/
    ``params``, never from ``request``'s headers, which is where every client's
    credentials actually live; a negative (404) result is cached with the same
    TTL as a positive one, distinct from "not cached" (see ProviderCache.get).

    ``cacheable`` lets a caller veto the cache WRITE for a decoded 2xx payload
    it recognises as a refusal in disguise (roadmap row 147: MDBList answers a
    spent daily budget with 200 and an error body, and caching that served the
    refusal as an answer for the whole TTL). None means every 2xx is cached,
    exactly as before; the 404 write is not consulted — a confirmed "nothing
    there" is an answer, not a refusal.
    """
    key = None
    if cache is not None:
        key = build_cache_key(method, url, params)
        cached = await cache.get(key)
        if cached is not None:
            return cached["payload"] if cached["found"] else None

    response = await request()
    if response.status_code == 404:
        if key is not None:
            await cache.set(key, {"found": False, "payload": None}, ttl_seconds)
        return None
    response.raise_for_status()
    payload = response.json()
    if key is not None and (cacheable is None or cacheable(payload)):
        await cache.set(key, {"found": True, "payload": payload}, ttl_seconds)
    return payload
