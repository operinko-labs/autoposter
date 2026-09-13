"""Row 85 -- the IMDb parental-guide client (fetch + parse + cache).

Fixtures are the probe's own verbatim captures (`p-row85-probe.md`,
"Captures, verbatim" and "Null shape" sections) -- pinned, not recalled from
memory, per the module docstring's precedent in `collections/imdb_graphql.py`.

Parsing here is deliberately LENIENT, unlike `collections/imdb_graphql.py`'s
raise-on-drift posture: that module's wrong answer empties a whole live
collection, so it fails loudly. This module's wrong answer is "one item
keeps no parental labels this pass" across a 16k-item library where most
titles have no guide votes at all (`categories: null` is the *common* case)
-- raising on every shape surprise here would instead crash
metadata operations for the whole item.
"""
import json
from pathlib import Path

import httpx

from conftest import session_factory_for

from autoposter.providers.cache import ProviderCache
from autoposter.providers.imdb_parental_guide import IMDbParentalGuideClient

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def test_mixed_severity_profile_parses_every_category():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_shawshank.json")))
    ) as http:
        client = IMDbParentalGuideClient(http)
        result = await client.categories("tt0111161")

    assert result == [
        ("NUDITY", "Sex & Nudity", "Mild"),
        ("VIOLENCE", "Violence & Gore", "Moderate"),
        ("PROFANITY", "Profanity", "Severe"),
        ("ALCOHOL", "Alcohol, Drugs & Smoking", "Mild"),
        ("FRIGHTENING", "Frightening & Intense Scenes", "Moderate"),
    ]


async def test_severity_id_never_appears_in_the_result():
    """severity.text is the contract; severity.id (the mildVotes-shaped
    internal string) must never leak into what this client returns."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_shawshank.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0111161")

    for _category_id, _category_text, severity_text in result:
        assert severity_text in ("None", "Mild", "Moderate", "Severe")
        assert "Votes" not in severity_text


async def test_none_and_mild_categories_are_both_returned():
    """The client itself does not decide which severities become labels --
    that policy lives in the writer, not the fetch layer."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_paddington.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt1109624")

    severities = {text for _, _, text in result}
    assert severities == {"None", "Mild"}


async def test_categories_null_returns_none():
    """categories: null is the common case (no guide votes), not
    a fault -- skip silently, never raise."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_null.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0000002")

    assert result is None


async def test_a_completely_unrecognised_body_returns_none_not_a_raise():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": "shape"}))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0000002")

    assert result is None


async def test_a_malformed_single_entry_is_dropped_not_fatal():
    payload = load("imdb_parental_shawshank.json")
    # Corrupt one entry's severity; the other four must still come back.
    payload["data"]["title"]["parentsGuide"]["categories"][0]["severity"] = {"id": "mildVotes"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0111161")

    assert len(result) == 4
    assert all(category_id != "NUDITY" for category_id, _, _ in result)


async def test_the_query_is_posted_with_the_id_variable_and_the_imdb_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await IMDbParentalGuideClient(http).categories("tt0111161")

    assert seen["headers"]["x-imdb-client-name"] == "imdb-web-next"
    assert seen["body"]["variables"] == {"id": "tt0111161"}
    assert "parentsGuide" in seen["body"]["query"]


async def test_repeated_lookups_hit_the_cache_not_the_transport(session):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IMDbParentalGuideClient(http, cache=cache, cache_ttl_seconds=3600)
        first = await client.categories("tt0111161")
        second = await client.categories("tt0111161")

    assert first == second
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IMDbParentalGuideClient(http)
        await client.categories("tt0111161")
        await client.categories("tt0111161")

    assert len(calls) == 2
