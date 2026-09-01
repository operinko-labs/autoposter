"""IMDb's parental-guide categories for one title (roadmap row 85).

Same transport as ``collections/charts.py`` and ``collections/
imdb_graphql.py`` -- the public, unauthenticated ``api.graphql.imdb.com``
endpoint, the same mandatory ``x-imdb-client-name`` header (its absence gets
a 403 on the chart endpoint). Unlike those two, this goes through
``providers.fetch.fetch_json`` rather than a raw POST: this is a single-shot
per-title lookup with no pagination, and it wants the same cache/decode seam
every other provider client already shares (roadmap row 151's ``decode``
parameter generalised ``fetch_json`` past plain-JSON-over-GET; ``method`` and
``request`` are already caller-supplied, so a POST fits without changes
there).

**The query, verified against the live endpoint on 2026-09-01** (see
``p-row85-probe.md``): ``title(id: "tt…") { parentsGuide { categories {
category { id text } severity { id text } totalSeverityVotes } } } }``.
``severity.text`` is the four-value contract (``None``/``Mild``/``Moderate``/
``Severe``); ``severity.id`` is an internal vote-bucket string
(``mildVotes``, …) and is parsed here only to discard it -- never returned,
never compared.

**Parsing here is deliberately lenient**, the opposite posture from
``collections/imdb_graphql.py``'s raise-on-drift. That module's wrong answer
empties a whole live collection, so failing loudly is the safer choice.
This module's wrong answer costs one item its labels for one pass -- and
``categories: null`` is the *ordinary* result for most of a 16k-item
library (the probe's own finding: an obscure title with no guide votes
answers exactly this shape, indistinguishable at this layer from a title
this endpoint has never heard of -- and that's fine, because our ids come
from facts and are already existence-checked upstream; see roadmap row 85's
C1.3). So every unrecognised shape -- a missing ``title``, a missing
``parentsGuide``, ``categories: null``, a malformed individual entry --
returns ``None`` (or drops just that entry) with a DEBUG line, never a raise.
"""
import logging

import httpx

from autoposter.collections.charts import GRAPHQL_URL, HEADERS
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

__all__ = ["IMDbParentalGuideClient"]

QUERY = (
    "query TitleParentalGuide($id: ID!) {"
    " title(id: $id) { parentsGuide { categories {"
    " category { id text } severity { id text } totalSeverityVotes"
    " } } } }"
)


def _parse_categories(payload: object, imdb_id: str) -> list[tuple[str, str, str]] | None:
    """``(category id, category text, severity text)`` tuples, or None.

    See the module docstring for why this never raises. Individual malformed
    entries are dropped rather than failing the whole title -- an IMDb
    response holding four good categories and one odd one should not cost
    the item all five.
    """
    if not isinstance(payload, dict):
        logger.debug("IMDb parental guide %r: no payload", imdb_id)
        return None
    title = (payload.get("data") or {}).get("title")
    if not isinstance(title, dict):
        logger.debug("IMDb parental guide %r: no 'title' in the response", imdb_id)
        return None
    guide = title.get("parentsGuide")
    categories = guide.get("categories") if isinstance(guide, dict) else None
    if not isinstance(categories, list):
        logger.debug(
            "IMDb parental guide %r: categories is %r, not a list (no guide "
            "votes, or this id does not exist)", imdb_id, categories,
        )
        return None
    entries: list[tuple[str, str, str]] = []
    for entry in categories:
        category = entry.get("category") if isinstance(entry, dict) else None
        severity = entry.get("severity") if isinstance(entry, dict) else None
        category_id = category.get("id") if isinstance(category, dict) else None
        category_text = category.get("text") if isinstance(category, dict) else None
        severity_text = severity.get("text") if isinstance(severity, dict) else None
        if not (
            isinstance(category_id, str)
            and isinstance(category_text, str)
            and isinstance(severity_text, str)
        ):
            logger.debug(
                "IMDb parental guide %r: dropped a malformed category entry: %r",
                imdb_id, entry,
            )
            continue
        entries.append((category_id, category_text, severity_text))
    return entries


class IMDbParentalGuideClient:
    """One cached ``title(id:)`` lookup per IMDb id."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def categories(self, imdb_id: str) -> list[tuple[str, str, str]] | None:
        """This title's parental-guide categories, or None -- see the module
        docstring for every reason it can be None."""
        payload = await fetch_json(
            method="POST",
            url=GRAPHQL_URL,
            params={"id": imdb_id},
            request=lambda: self._client.post(
                GRAPHQL_URL, headers=HEADERS,
                json={"query": QUERY, "variables": {"id": imdb_id}},
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        return _parse_categories(payload, imdb_id)
