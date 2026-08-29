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


# --- the three prefetch fields (roadmap rows 189/192) -----------------------


def test_movie_facts_carry_the_three_prefetch_fields():
    """The widening, read off the same payload the pipeline already fetches.

    Zero new requests: these three fields ride the ``/movie/{id}`` read that
    already pays for the rating, the genres and the studio. Roadmap rows 189
    and 192 are what they are for.
    """
    facts = parse_movie_facts(load("tmdb_movie.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None
    assert facts.sources["tmdb_origin_country"] == "tmdb"
    assert facts.sources["tmdb_original_language"] == "tmdb"


def test_the_origin_country_read_is_not_production_countries():
    """The two fields disagree on this very title, so reading the wrong one is
    a red test rather than a silent pass.

    The captured ``/movie/940143`` carries ``origin_country: ["US"]`` and
    ``production_countries: [GB, US]`` -- which is the captured counter-example
    that refutes the substitution roadmap row 189 rules out. Plex's own
    ``<Country>`` already carries the production country; this column carries
    the origin country, and they are not the same statement.
    """
    payload = load("tmdb_movie.json")
    assert [entry["iso_3166_1"] for entry in payload["production_countries"]] == ["GB", "US"]
    assert parse_movie_facts(payload).tmdb_origin_country == ["US"]


def test_the_original_language_read_is_not_the_languages_list():
    """``languages`` is the list of languages the show is available in;
    ``original_language`` is the one it was made in. Reading the former would
    yield a list where an ISO-639-1 code belongs."""
    payload = load("tmdb_show.json")
    assert payload["languages"] == ["en"]
    assert parse_show_facts(payload).tmdb_original_language == "en"


def test_a_movie_in_a_franchise_carries_its_collection_id():
    """``belongs_to_collection`` is an object or ``null``; the id is what row
    192's enumeration keys on."""
    facts = parse_movie_facts(load("tmdb_movie_franchise.json"))
    assert facts.tmdb_collection_id == 8091
    assert facts.sources["tmdb_collection_id"] == "tmdb"


def test_show_facts_carry_the_two_fields_a_show_has():
    """A show has no ``belongs_to_collection`` -- TMDb collections are movie
    franchises (``builders/tmdb.py``'s ``TmdbCollectionBuilder``)."""
    facts = parse_show_facts(load("tmdb_show.json"))
    assert facts.tmdb_origin_country == ["US"]
    assert facts.tmdb_original_language == "en"
    assert facts.tmdb_collection_id is None


def test_the_three_fields_are_absent_rather_than_empty_when_tmdb_has_none():
    """Absent must never be written as a value -- ``persist_facts``' Finding 4.
    An empty ``origin_country`` list is the same statement as no key at all."""
    facts = parse_movie_facts({"id": 1})
    assert facts.tmdb_origin_country == []
    assert facts.tmdb_original_language is None
    assert facts.tmdb_collection_id is None
    assert "tmdb_origin_country" not in facts.sources


def test_a_null_collection_is_absent_not_a_shape_error():
    """``belongs_to_collection: null`` is the normal case -- most films are in
    no franchise -- and it must read exactly like the key being missing, which
    is the shape ``/tv/{id}`` sends."""
    assert parse_movie_facts({"belongs_to_collection": None}).tmdb_collection_id is None
    assert "tmdb_collection_id" not in parse_movie_facts({"belongs_to_collection": None}).sources


@pytest.mark.parametrize("payload", [
    {"origin_country": "US"},
    {"origin_country": [None, 5, "US"]},
    {"belongs_to_collection": []},
    {"belongs_to_collection": {"name": "no id here"}},
    {"belongs_to_collection": {"id": "not-a-number"}},
    {"original_language": 7},
])
def test_malformed_prefetch_fields_are_ignored_rather_than_raising(payload):
    """Every other parser here degrades on malformed input rather than taking
    the whole gather down (``_genres``, ``_first_name``, ``_as_date``); these
    three do the same. ``origin_country: "US"`` -- a bare string where TMDb
    documents a list -- is the one that would otherwise iterate to
    ``["U", "S"]``, which is a plausible wrong value, not an absent one."""
    facts = parse_movie_facts(payload)
    assert facts.tmdb_origin_country in ([], ["US"])
    if payload.get("origin_country") == [None, 5, "US"]:
        assert facts.tmdb_origin_country == ["US"]
    else:
        assert facts.tmdb_collection_id is None or isinstance(
            facts.tmdb_collection_id, int
        )


# --- the collection overview a ``tmdb_summary:`` definition borrows ---------


async def test_a_collection_summary_is_read_from_the_overview():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": 10, "overview": "The whole saga."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        summary = await TMDBFactsClient("tok", http).collection_summary(10)

    assert summary == "The whole saga."
    assert calls == ["https://api.themoviedb.org/3/collection/10"]


@pytest.mark.parametrize("payload", [{"id": 10}, {"id": 10, "overview": ""}])
async def test_a_collection_with_no_overview_has_no_summary_to_borrow(payload):
    """TMDB writes an empty string, not a missing key, for a collection nobody
    has described. Both mean "nothing to borrow" -- and the caller must leave
    the collection's own summary alone rather than blank it."""
    def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await TMDBFactsClient("tok", http).collection_summary(10) is None


async def test_a_collection_tmdb_does_not_know_has_no_summary():
    def handler(request):
        return httpx.Response(404, json={"status_message": "not found"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await TMDBFactsClient("tok", http).collection_summary(999) is None


async def test_a_collection_summary_is_served_from_the_cache(session):
    """One request per TTL. A pass over two libraries reconciles the same
    definition twice, and the summary is the same both times."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"id": 10, "overview": "The whole saga."})

    from autoposter.providers.cache import ProviderCache

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600)
        first = await client.collection_summary(10)
        second = await client.collection_summary(10)

    assert first == second == "The whole saga."
    assert len(calls) == 1
