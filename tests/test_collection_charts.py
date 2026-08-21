"""IMDb chart fetching.

Never touches the real API -- MockTransport only.
"""
import json
from pathlib import Path

import httpx
import pytest

from autoposter.collections.charts import CHARTS, fetch_chart

FIXTURE = json.loads(
    (Path("tests/fixtures/collections/imdb_chart.json")).read_text(encoding="utf-8")
)


def _transport(recorder, response=None):
    def handler(request):
        recorder.append(request)
        return response or httpx.Response(200, json=FIXTURE)

    return httpx.MockTransport(handler)


def test_every_configured_chart_has_a_type_and_a_count():
    assert set(CHARTS) == {
        "popular_movies", "top_movies", "lowest_rated", "popular_shows", "top_shows",
    }
    assert CHARTS["top_movies"] == ("TOP_RATED_MOVIES", 250)
    assert CHARTS["popular_movies"] == ("MOST_POPULAR_MOVIES", 100)


async def test_ids_are_returned_in_chart_order():
    """The order is the rank; the collection preserves it."""
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        ids = await fetch_chart(http, "top_movies")
    assert ids == ["tt0111161", "tt0068646", "tt0468569"]


async def test_the_client_name_header_is_sent():
    """Without it the API returns 403."""
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        await fetch_chart(http, "top_movies")
    assert seen[0].headers["x-imdb-client-name"] == "imdb-web-next"


async def test_the_query_carries_the_charts_type_and_count():
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        await fetch_chart(http, "lowest_rated")
    body = json.loads(seen[0].content)["query"]
    assert "LOWEST_RATED_MOVIES" in body
    assert "first: 100" in body


async def test_an_http_error_raises_rather_than_returning_nothing():
    """An empty list reaching the reconciler would empty a live collection."""
    seen = []
    transport = _transport(seen, httpx.Response(403, text="Forbidden"))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(Exception):
            await fetch_chart(http, "top_movies")


async def test_a_malformed_body_raises():
    seen = []
    transport = _transport(seen, httpx.Response(200, json={"data": {}}))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(Exception):
            await fetch_chart(http, "top_movies")


async def test_an_unknown_chart_raises():
    seen = []
    async with httpx.AsyncClient(transport=_transport(seen)) as http:
        with pytest.raises(KeyError):
            await fetch_chart(http, "not_a_chart")
