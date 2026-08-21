"""IMDb chart ids over the public GraphQL endpoint.

The ``x-imdb-client-name`` header is not optional -- without it the endpoint
returns 403.

Kometa falls back to scraping the HTML chart page when GraphQL fails. That
fallback is deliberately not reproduced here. These collections use sync
semantics, so an empty result would remove every member; failing loudly is
both safer and cheaper than maintaining a second parser for the failure case.
"""
import httpx

GRAPHQL_URL = "https://api.graphql.imdb.com/"
HEADERS = {"content-type": "application/json", "x-imdb-client-name": "imdb-web-next"}

# chart key -> (GraphQL chartType, how many to request)
CHARTS: dict[str, tuple[str, int]] = {
    "popular_movies": ("MOST_POPULAR_MOVIES", 100),
    "top_movies": ("TOP_RATED_MOVIES", 250),
    "lowest_rated": ("LOWEST_RATED_MOVIES", 100),
    "popular_shows": ("MOST_POPULAR_TV_SHOWS", 100),
    "top_shows": ("TOP_RATED_TV_SHOWS", 250),
}

QUERY = "{ chartTitles(chart: { chartType: %s }, first: %d) { edges { node { id } } total } }"


async def fetch_chart(http: httpx.AsyncClient, chart: str) -> list[str]:
    """The chart's IMDb ids, in rank order.

    Raises rather than returning an empty list on any failure -- see the
    module docstring.
    """
    chart_type, first = CHARTS[chart]
    response = await http.post(
        GRAPHQL_URL, headers=HEADERS, json={"query": QUERY % (chart_type, first)}
    )
    response.raise_for_status()
    payload = response.json()
    try:
        edges = payload["data"]["chartTitles"]["edges"]
    except (KeyError, TypeError) as error:
        raise ValueError("IMDb chart %r returned an unexpected body" % chart) from error
    ids = [edge["node"]["id"] for edge in edges]
    if not ids:
        raise ValueError("IMDb chart %r returned no ids" % chart)
    return ids
