import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from conftest import session_factory_for

from autoposter.db.models import ProviderCache as ProviderCacheRow
from autoposter.facts.mdblist import (
    MDBListClient,
    MDBListRefused,
    NullMDBListClient,
    parse_content_rating,
    parse_list_items,
    parse_ratings,
)
from autoposter.providers.cache import ProviderCache

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_content_rating_is_the_bare_age_as_a_string():
    """The '+' is added by the badge, not stored."""
    assert parse_content_rating(load("mdblist_movie.json")) == "17"


def test_absent_when_commonsense_is_falsy():
    """age_rating can come from non-Common-Sense sources; only use it when gated."""
    payload = load("mdblist_movie.json") | {"commonsense": 0}
    assert parse_content_rating(payload) is None


def test_absent_when_commonsense_key_is_missing():
    payload = {k: v for k, v in load("mdblist_movie.json").items() if k != "commonsense"}
    assert parse_content_rating(payload) is None


def test_absent_when_age_rating_is_missing():
    payload = {k: v for k, v in load("mdblist_movie.json").items() if k != "age_rating"}
    assert parse_content_rating(payload) is None


def test_age_rating_zero_is_not_treated_as_absent():
    """Age 0 is a legitimate Common Sense value and must survive."""
    payload = load("mdblist_movie.json") | {"age_rating": 0}
    assert parse_content_rating(payload) == "0"


def test_string_age_rating_is_accepted():
    payload = load("mdblist_movie.json") | {"age_rating": "13"}
    assert parse_content_rating(payload) == "13"


def test_parse_ratings_applies_the_probes_per_field_rescaling():
    """Probe section 2.3.3's table, field by field. `letterboxd` is the *2
    outlier (a 5-point scale to Kometa's 10-point one); `imdb`,
    `metacriticuser` and `myanimelist` are untransformed because their native
    scale is already 0-10; everything else reads MDBList's own 0-100 `score`
    and divides by 10."""
    payload = load("mdblist_full_ratings.json")
    assert parse_ratings(payload) == {
        "mdb_average_rating": 5.8,
        "mdb_imdb_rating": 4.9,
        "mdb_letterboxd_rating": 6.2,
        "mdb_metacritic_rating": 5.8,
        "mdb_metacriticuser_rating": 6.8,
        "mdb_myanimelist_rating": None,
        "mdb_rating": 5.5,
        "mdb_tmdb_rating": 6.3,
        "mdb_tomatoes_rating": 6.7,
        "mdb_tomatoesaudience_rating": 7.1,
        "mdb_trakt_rating": 7.2,
    }


def test_parse_ratings_treats_a_falsy_source_value_as_none_not_zero():
    """Probe section 2.3.3: every branch in the pinned Kometa source is
    `X / 10 if X else None` -- a legitimate 0 is not a legitimate rating."""
    payload = load("mdblist_full_ratings.json")
    payload = payload | {
        "ratings": [
            e | {"value": 0, "score": 0} if e["source"] == "imdb" else e
            for e in payload["ratings"]
        ]
    }
    assert parse_ratings(payload)["mdb_imdb_rating"] is None


def test_parse_ratings_treats_a_string_zero_the_same_as_a_numeric_zero():
    """The falsy gate ran on the RAW value, before
    coercion -- a numeric `0` is caught by `if not raw`, but the string
    `"0"` is truthy on `raw` and survived coercion to a legitimate-looking
    `0.0`. The probe's own rule (`X / 10 if X else None`) has to apply to
    the coerced value, not the raw one, or a source that happens to emit its
    zero as a string escapes the same rule a numeric zero does not."""
    payload = load("mdblist_full_ratings.json")
    payload = payload | {
        "ratings": [
            e | {"value": "0", "score": "0"} if e["source"] == "imdb" else e
            for e in payload["ratings"]
        ]
    }
    assert parse_ratings(payload)["mdb_imdb_rating"] is None


def test_parse_ratings_is_total_over_a_payload_with_no_ratings_array():
    assert parse_ratings({}) == {
        "mdb_average_rating": None,
        "mdb_imdb_rating": None,
        "mdb_letterboxd_rating": None,
        "mdb_metacritic_rating": None,
        "mdb_metacriticuser_rating": None,
        "mdb_myanimelist_rating": None,
        "mdb_rating": None,
        "mdb_tmdb_rating": None,
        "mdb_tomatoes_rating": None,
        "mdb_tomatoesaudience_rating": None,
        "mdb_trakt_rating": None,
    }


def test_parse_ratings_degrades_a_malformed_entry_to_none_and_warns(caplog):
    """MEDIUM finding, preflight review: a present-but-non-numeric field (a
    stray string MDBList might emit for an errored source) must degrade that
    ONE key to `None`, never raise `ValueError` out of `parse_ratings` -- the
    same 'missing/unknown degrades, never errors' discipline the falsy-value
    branch above already has, extended to the malformed-value case. A raised
    `ValueError` here would fail the WHOLE badge stage for the item (only
    `MDBListLimitReached`/`httpx.HTTPError` are caught around the call site,
    Concern E) -- exactly the gap this pins shut."""
    payload = load("mdblist_full_ratings.json")
    payload = payload | {
        "ratings": [
            e | {"value": "not-a-number", "score": "also-not-a-number"}
            if e["source"] == "imdb" else e
            for e in payload["ratings"]
        ],
    }
    with caplog.at_level("WARNING"):
        result = parse_ratings(payload)
    assert result["mdb_imdb_rating"] is None, "the malformed source alone degrades"
    assert result["mdb_tmdb_rating"] == 6.3, "an unrelated source must not be taken down with it"
    assert len(caplog.records) == 1, "one warning for the one malformed source"


async def test_movies_are_looked_up_by_tmdb_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        rating = await MDBListClient("KEY", http).content_rating(tmdb_id=940143, is_movie=True)

    assert "/tmdb/movie/940143/" in seen["url"]
    assert rating == "17"


async def test_shows_are_looked_up_by_tvdb_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await MDBListClient("KEY", http).content_rating(tvdb_id=371980, is_movie=False)

    assert "/tvdb/show/371980/" in seen["url"]


async def test_the_api_key_is_sent_as_a_query_parameter():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await MDBListClient("SECRET", http).content_rating(tmdb_id=1, is_movie=True)

    assert "apikey=SECRET" in seen["url"]


async def test_missing_identifier_makes_no_request():
    def handler(request):
        raise AssertionError("should not have been called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await MDBListClient("KEY", http).content_rating(is_movie=True) is None


async def test_404_returns_none():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        assert await MDBListClient("KEY", http).content_rating(tmdb_id=1, is_movie=True) is None


async def test_quota_exhaustion_raises_a_distinct_error():
    """MDBList answers 200 with an error body when the daily budget is gone."""
    from autoposter.facts.mdblist import MDBListLimitReached

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        with pytest.raises(MDBListLimitReached):
            await MDBListClient("KEY", http).content_rating(tmdb_id=1, is_movie=True)


async def test_repeated_lookups_hit_the_cache_not_the_transport(session):
    """MDBList must go through the Phase 1 cache seam, like every
    other provider request — it is slow-moving data worth caching."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("mdblist_movie.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.content_rating(tmdb_id=940143, is_movie=True)
        second = await client.content_rating(tmdb_id=940143, is_movie=True)

    assert first == second == "17"
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_transport():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("mdblist_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http)
        await client.content_rating(tmdb_id=940143, is_movie=True)
        await client.content_rating(tmdb_id=940143, is_movie=True)

    assert len(calls) == 2


async def test_a_cached_404_still_returns_none(session):
    """fetch_json caches a negative (404) result too; that must still surface
    as None, not raise or return stale data."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404, json={})

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.content_rating(tmdb_id=1, is_movie=True)
        second = await client.content_rating(tmdb_id=1, is_movie=True)

    assert first is None
    assert second is None
    assert len(calls) == 1


async def test_a_limit_body_is_never_cached_for_a_list(session):
    """Roadmap row 147, tmdb_budget's law mirrored: MDBList answers a spent
    daily budget with 200 and {"error": "API Limit Reached!"}, which fetch_json
    stored like any other success -- so a later pass, including one after
    midnight when the allowance had already rolled over, was served the cached
    refusal until the entry aged out, up to a full TTL late. A refusal must
    never become a cached answer (tests/test_tmdb_budget.py::
    test_a_refusal_body_is_never_cached is the shipped precedent)."""
    from autoposter.facts.mdblist import MDBListLimitReached

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.list_items("user/some-list")

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_a_limit_body_is_never_cached_for_a_content_rating(session):
    """The same law on the other endpoint the client owns."""
    from autoposter.facts.mdblist import MDBListLimitReached

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.content_rating(tmdb_id=1, is_movie=True)

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_the_first_ask_after_rollover_reaches_the_transport_again(session):
    """Row 147's consequence, refused end to end: the allowance rolls over at
    midnight, and the very next ask must reach MDBList -- not a cached
    refusal. This is the recovery-latency half of the row: with no cached
    limit body, recovery is the first request after rollover, not TTL-late."""
    from autoposter.facts.mdblist import MDBListLimitReached

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json={"error": "API Limit Reached!"})
        return httpx.Response(200, json=[
            {"mediatype": "movie", "id": 1, "title": "Dune"},
        ])

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.list_items("user/some-list")
        items = await client.list_items("user/some-list")

    assert len(calls) == 2, "the post-rollover ask must reach the transport"
    assert [(kind, entry["title"]) for kind, entry in items] == [("movie", "Dune")]


async def test_a_non_limit_error_body_is_cached_on_purpose(session):
    """The inverse of the two tests above, and the reason they are narrow.
    ``_not_a_limit_body`` vetoes the cache write for the daily-budget refusal
    ONLY; a list that was deleted or made private also answers 200 with an
    error body, and THAT one is written to the cache deliberately -- a stable
    answer whose caching saves budget, the analogue of the 404 ``fetch_json``
    has always cached. Pinned here because the veto could otherwise be
    widened to every error body in a single edit (``if payload.get("error")``
    in place of the ``in _LIMIT_ERRORS`` membership) with the whole suite
    still green, turning one dead definition into a repeated request every
    pass for the entire TTL."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"error": "List not found"})

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListRefused):
            await client.list_items("user/gone-list")
        with pytest.raises(MDBListRefused):
            await client.list_items("user/gone-list")

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert len(rows) == 1, "a non-limit error body stays cacheable on purpose"
    assert len(calls) == 1, "the second ask must be served from the cache"


def test_a_mediatype_value_mdblist_has_never_served_raises():
    """``parse_list_items`` accepted any ``mediatype``
    string and left filtering to the builder, which compares it to the one
    value the library wants and silently drops everything else. If MDBList
    renamed 'show' to 'tv', every entry would compare unequal and the
    collection would empty out with nothing to say why -- the same failure
    mode the shape checks above already guard. An unrecognised value must
    raise here, naming the value, so the drift is loud instead of silent."""
    payload = [{"title": "Something", "tmdb_id": 1, "mediatype": "tv"}]
    with pytest.raises(MDBListRefused, match="tv"):
        parse_list_items(payload, "MDBList list 'x'")


async def test_null_client_always_returns_none_without_a_request():
    """Finding 1: the stand-in used when no API key is configured degrades
    only the content rating; it must never make a request or raise."""
    client = NullMDBListClient()
    assert await client.content_rating(tmdb_id=1, is_movie=True) is None
    assert await client.content_rating(tvdb_id=2, is_movie=False) is None
    assert await client.content_rating() is None


async def test_ratings_reuses_the_content_rating_endpoint_and_cache(session):
    """Roadmap row 100 C2a: 'the ratings array already arrives in a response
    the client already fetches' -- proved by counting transport calls, not
    asserted."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("mdblist_full_ratings.json"))

    factory = session_factory_for(session)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        cache = ProviderCache(factory)
        client = MDBListClient("KEY", http, cache=cache)
        await client.content_rating(tmdb_id=940143, is_movie=True)
        result = await client.ratings(tmdb_id=940143, is_movie=True)

    assert len(calls) == 1, "the second read must be served from the cache"
    assert result["mdb_imdb_rating"] == 4.9


async def test_ratings_with_no_identifier_makes_no_request_and_answers_all_none():
    def handler(request):
        raise AssertionError("should not have been called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await MDBListClient("KEY", http).ratings(is_movie=True)

    assert result == {k: None for k in result}
    assert len(result) == 11


async def test_ratings_quota_exhaustion_raises_the_same_error_content_rating_does():
    from autoposter.facts.mdblist import MDBListLimitReached

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        with pytest.raises(MDBListLimitReached):
            await MDBListClient("KEY", http).ratings(tmdb_id=1, is_movie=True)


async def test_null_client_ratings_answers_all_none_and_makes_no_request():
    result = await NullMDBListClient().ratings(tmdb_id=1, is_movie=True)
    assert result == {k: None for k in result}
    assert len(result) == 11
