import asyncio

import httpx
import pytest_asyncio
from sqlalchemy import select

from autoposter.db.models import ProviderCache as ProviderCacheRow
from autoposter.providers.base import POSTER, ArtRequest
from autoposter.providers.cache import ProviderCache, build_cache_key
from autoposter.providers.fetch import fetch_json
from autoposter.providers.tvdb import TVDBClient


@pytest_asyncio.fixture
async def cache(session_factory):
    return ProviderCache(session_factory)


# --- build_cache_key -------------------------------------------------------


def test_build_cache_key_strips_credential_params():
    with_key = build_cache_key("GET", "https://webservice.fanart.tv/v3.2/tv/1", {"api_key": "secret1"})
    other_key = build_cache_key("GET", "https://webservice.fanart.tv/v3.2/tv/1", {"api_key": "secret2"})
    assert with_key == other_key


def test_build_cache_key_never_contains_the_credential_value():
    key = build_cache_key(
        "GET", "https://webservice.fanart.tv/v3.2/tv/1", {"api_key": "super-secret-value"}
    )
    assert "super-secret-value" not in key


def test_build_cache_key_is_order_independent():
    a = build_cache_key("GET", "https://example.com", {"b": "2", "a": "1"})
    b = build_cache_key("GET", "https://example.com", {"a": "1", "b": "2"})
    assert a == b


def test_build_cache_key_is_stable_across_separately_constructed_instances(session_factory):
    # The key builder carries no per-instance state, which is what makes it stable
    # across processes and restarts — constructing two caches must not change it.
    ProviderCache(session_factory)
    ProviderCache(session_factory)
    first = build_cache_key("GET", "https://api.themoviedb.org/3/movie/1/images", {"a": "1"})
    second = build_cache_key("GET", "https://api.themoviedb.org/3/movie/1/images", {"a": "1"})
    assert first == second


def test_build_cache_key_differs_for_different_urls():
    a = build_cache_key("GET", "https://example.com/1", {})
    b = build_cache_key("GET", "https://example.com/2", {})
    assert a != b


# --- ProviderCache -----------------------------------------------------------


async def test_get_returns_none_when_absent(cache):
    assert await cache.get("missing-key") is None


async def test_set_then_get_roundtrips(cache):
    await cache.set("k1", {"found": True, "payload": {"posters": []}}, ttl_seconds=3600)
    assert await cache.get("k1") == {"found": True, "payload": {"posters": []}}


async def test_expired_entry_is_not_served(cache):
    await cache.set("k2", {"found": True, "payload": {}}, ttl_seconds=-5)
    assert await cache.get("k2") is None


async def test_concurrent_sets_for_the_same_key_do_not_raise(cache):
    await asyncio.gather(
        cache.set("k3", {"found": True, "payload": {"a": 1}}, ttl_seconds=3600),
        cache.set("k3", {"found": True, "payload": {"a": 2}}, ttl_seconds=3600),
    )
    # Whichever write won, exactly one row exists and a value is readable.
    assert await cache.get("k3") is not None


# --- fetch_json --------------------------------------------------------------


def _transport(calls, payload=None, status=200):
    async def handler(request):
        calls.append(request.url.path)
        if status == 404:
            return httpx.Response(404)
        return httpx.Response(200, json=payload if payload is not None else {})

    return httpx.MockTransport(handler)


async def test_fetch_json_miss_then_hit_uses_the_transport_once(cache):
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, payload={"posters": [1]}))
    kwargs = dict(
        method="GET", url="https://api.example.com/x", params={"a": "1"},
        request=lambda: client.get("https://api.example.com/x", params={"a": "1"}),
        cache=cache, ttl_seconds=3600,
    )

    first = await fetch_json(**kwargs)
    second = await fetch_json(**kwargs)

    assert first == {"posters": [1]}
    assert second == {"posters": [1]}
    assert len(calls) == 1


async def test_fetch_json_expired_entry_triggers_a_refetch(cache):
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, payload={"posters": []}))
    kwargs = dict(
        method="GET", url="https://api.example.com/y", params=None,
        request=lambda: client.get("https://api.example.com/y"),
        cache=cache,
    )

    await fetch_json(**kwargs, ttl_seconds=-5)  # stored already expired
    await fetch_json(**kwargs, ttl_seconds=3600)

    assert len(calls) == 2


async def test_fetch_json_404_is_cached_and_not_refetched(cache):
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, status=404))
    kwargs = dict(
        method="GET", url="https://api.example.com/missing", params=None,
        request=lambda: client.get("https://api.example.com/missing"),
        cache=cache, ttl_seconds=3600,
    )

    first = await fetch_json(**kwargs)
    second = await fetch_json(**kwargs)

    assert first is None
    assert second is None
    assert len(calls) == 1


async def test_fetch_json_200_with_no_artwork_keys_is_cached_and_not_refetched(cache):
    # Real Fanart case: HTTP 200 but the payload carries no poster keys at all.
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, payload={"name": "Some Show"}))
    kwargs = dict(
        method="GET", url="https://webservice.fanart.tv/v3.2/tv/999", params={"api_key": "x"},
        request=lambda: client.get(
            "https://webservice.fanart.tv/v3.2/tv/999", params={"api_key": "x"}
        ),
        cache=cache, ttl_seconds=3600,
    )

    first = await fetch_json(**kwargs)
    second = await fetch_json(**kwargs)

    assert first == {"name": "Some Show"}
    assert second == {"name": "Some Show"}
    assert len(calls) == 1


async def test_fetch_json_without_a_cache_makes_a_request_every_time():
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, payload={"posters": []}))
    kwargs = dict(
        method="GET", url="https://api.example.com/z", params=None,
        request=lambda: client.get("https://api.example.com/z"),
        cache=None, ttl_seconds=3600,
    )

    await fetch_json(**kwargs)
    await fetch_json(**kwargs)

    assert len(calls) == 2


async def test_fetch_json_credential_query_param_is_absent_from_the_stored_key(cache, session):
    # Same endpoint, two different (bogus) credential values: they must resolve to
    # the same cache entry, and the raw secret must never land in the stored row.
    calls = []
    client = httpx.AsyncClient(transport=_transport(calls, payload={"tvposter": []}))

    async def call_with(api_key):
        params = {"api_key": api_key}
        return await fetch_json(
            method="GET", url="https://webservice.fanart.tv/v3.2/tv/1", params=params,
            request=lambda: client.get(
                "https://webservice.fanart.tv/v3.2/tv/1", params=params
            ),
            cache=cache, ttl_seconds=3600,
        )

    await call_with("secret-key-one")
    await call_with("secret-key-two")

    assert len(calls) == 1  # second call was served from the cache despite a different key

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    for row in rows:
        assert "secret-key-one" not in row.key
        assert "secret-key-two" not in row.key


# --- TVDB login exclusion -----------------------------------------------------


async def test_tvdb_login_is_never_cached(cache):
    # If /login were ever routed through the cache (it must not be — it's a
    # credential with its own lifetime, see TVDBClient._login), a second, cold
    # client sharing the same provider_cache would silently reuse the first
    # client's stale token instead of logging in for itself.
    calls = {"login": 0, "artworks": 0}

    async def handler(request):
        if request.url.path == "/v4/login":
            calls["login"] += 1
            return httpx.Response(200, json={"data": {"token": f"tok-{calls['login']}"}})
        calls["artworks"] += 1
        return httpx.Response(200, json={"data": {"artworks": []}})

    transport = httpx.MockTransport(handler)

    # First client (e.g. first process): logs in once, its artworks response is cached.
    client_one = TVDBClient("key", httpx.AsyncClient(transport=transport), cache=cache)
    await client_one.fetch(ArtRequest(art_kind=POSTER, is_movie=False, tvdb_id=999))
    assert calls["login"] == 1
    assert calls["artworks"] == 1

    # A brand-new, cold client instance for a *different* series (so its artworks
    # call is a genuine cache miss and authentication is actually exercised) must
    # still perform its own real login call.
    client_two = TVDBClient("key", httpx.AsyncClient(transport=transport), cache=cache)
    await client_two.fetch(ArtRequest(art_kind=POSTER, is_movie=False, tvdb_id=1000))
    assert calls["login"] == 2
    assert calls["artworks"] == 2
