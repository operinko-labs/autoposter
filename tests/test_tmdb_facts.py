import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from conftest import session_factory_for
from autoposter.facts.tmdb_facts import (
    TMDBFactsClient,
    parse_movie_facts,
    parse_season_episode_ratings,
    parse_show_facts,
)

FIXTURES = Path(__file__).parent / "fixtures" / "facts"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_movie_facts():
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.audience_rating == pytest.approx(6.3)
    assert facts.genres == ["Horror", "Drama"]
    assert facts.originally_available == date(2023, 5, 12)
    assert facts.sources["audience_rating"] == "tmdb"


def test_movie_studio_is_the_first_production_company():
    """Kometa takes companies[0] with no sorting — match it exactly."""
    assert parse_movie_facts(load("tmdb_movie.json")).studio == "First Studio"


def test_show_studio_is_the_first_network_not_a_production_company():
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.studio == "Apple TV+"


def test_show_uses_first_air_date():
    assert parse_show_facts(load("tmdb_show.json")).originally_available == date(2022, 2, 18)


def test_missing_optional_fields_do_not_raise():
    facts = parse_movie_facts({"id": 1})
    assert facts.audience_rating is None
    assert facts.genres == []
    assert facts.studio is None
    assert facts.originally_available is None


def test_empty_company_list_gives_no_studio():
    assert parse_movie_facts({"production_companies": []}).studio is None


def test_genres_with_string_value_returns_empty_list():
    """Malformed input: genres is a string instead of a list."""
    facts = parse_movie_facts({"genres": "oops"})
    assert facts.genres == []


def test_genres_with_mixed_valid_and_invalid_entries():
    """Malformed input: genres list contains a string alongside valid dicts."""
    facts = parse_movie_facts({"genres": [{"name": "Horror"}, "junk", {"no_name": 1}]})
    assert facts.genres == ["Horror"]


def test_malformed_release_date_is_ignored():
    assert parse_movie_facts({"release_date": ""}).originally_available is None
    assert parse_movie_facts({"release_date": "not-a-date"}).originally_available is None


def test_season_episode_ratings_are_keyed_by_episode_number():
    ratings = parse_season_episode_ratings(load("tmdb_season.json"))
    assert ratings[1] == pytest.approx(7.8)
    assert ratings[2] == pytest.approx(8.1)


def test_zero_rating_is_treated_as_absent():
    """TMDB reports 0.0 for unrated episodes; writing that would show '0%'."""
    assert 3 not in parse_season_episode_ratings(load("tmdb_season.json"))


def test_season_episode_ratings_with_string_value_returns_empty_dict():
    """Malformed input: episodes is a string instead of a list."""
    ratings = parse_season_episode_ratings({"episodes": "notalist"})
    assert ratings == {}


def test_season_episode_ratings_with_mixed_valid_and_invalid_entries():
    """Malformed input: episodes list contains a string alongside a valid dict."""
    ratings = parse_season_episode_ratings({
        "episodes": [
            {"episode_number": 1, "vote_average": 7.8},
            "junk",
        ]
    })
    assert ratings == {1: pytest.approx(7.8)}


async def test_client_requests_the_season_endpoint():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("token", http)
        ratings = await client.season_episode_ratings(95396, 2)

    assert "/tv/95396/season/2" in seen["url"]
    assert ratings[1] == pytest.approx(7.8)


async def test_client_sends_a_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=load("tmdb_movie.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TMDBFactsClient("tok", http).movie(940143)

    assert seen["auth"] == "Bearer tok"


async def test_client_returns_empty_facts_on_404():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, json={}))
    ) as http:
        facts = await TMDBFactsClient("tok", http).movie(1)
    assert facts.is_empty()


async def test_repeated_season_lookups_hit_the_cache_not_the_api(session):
    """A season-pack import asks for one season repeatedly — pay once.

    This is the whole reason episode ratings are affordable per-item: without
    it, importing a 10-episode season means 10 identical TMDB requests.
    """
    from autoposter.providers.cache import ProviderCache

    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.season_episode_ratings(95396, 2)
        second = await client.season_episode_ratings(95396, 2)

    assert first == second
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_api():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=load("tmdb_season.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http)
        await client.season_episode_ratings(95396, 2)
        await client.season_episode_ratings(95396, 2)

    assert len(calls) == 2
