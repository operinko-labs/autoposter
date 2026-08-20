import json
from pathlib import Path

import httpx
import pytest

from autoposter.facts.mdblist import MDBListClient, parse_content_rating

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
