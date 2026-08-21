"""The collection inventory, and the per-library orchestration that builds
every chart and award collection in one pass.

Two failure-containment rules matter here, both already enforced one level
down but easy to lose sight of when wiring several sources together:

- ``charts.fetch_chart`` and ``awards.fetch_event`` both raise rather than
  returning nothing on failure (see their own docstrings). This module is
  where that raise gets turned into "log it and hand the reconciler an empty
  list" -- which ``reconcile_list_collection`` already treats as "make no
  changes", never "remove everything". One dead chart or a GitHub outage
  must not take the rest of the run down with it.
- The award event and the IMDb index are each fetched once and shared across
  every collection that needs them, rather than once per collection.
"""
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.collections.awards import (
    BEST_DIRECTOR,
    BEST_PICTURE,
    fetch_event,
    recent_years,
    winners_for_categories,
    winners_for_year,
)
from autoposter.collections.charts import fetch_chart
from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.resolve import build_imdb_index, resolve_ids

logger = logging.getLogger(__name__)

# library type -> [(collection title, chart key), ...]. IMDb has no
# lowest-rated TV chart, so Show deliberately omits it.
CHART_COLLECTIONS: dict[str, list[tuple[str, str]]] = {
    "Movie": [
        ("IMDb Popular", "popular_movies"),
        ("IMDb Top 250", "top_movies"),
        ("IMDb Lowest Rated", "lowest_rated"),
    ],
    "Show": [
        ("IMDb Popular", "popular_shows"),
        ("IMDb Top 250", "top_shows"),
    ],
}

# The two static winner collections, movies only.
AWARD_COLLECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("Oscars Best Picture Winners", BEST_PICTURE),
    ("Oscars Best Director Winners", BEST_DIRECTOR),
]

YEAR_SUMMARY = "The winners of the %s Academy Awards."


async def _chart_ids(http: httpx.AsyncClient, chart: str) -> list[str]:
    try:
        return await fetch_chart(http, chart)
    except Exception:
        logger.exception("failed to fetch IMDb chart %r", chart)
        return []


async def _award_event(http: httpx.AsyncClient) -> dict | None:
    try:
        return await fetch_event(http)
    except Exception:
        logger.exception("failed to fetch the Oscars award dataset")
        return None


async def build_all(
    http: httpx.AsyncClient,
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    label: str,
    config,
) -> list[str]:
    """Reconcile every configured chart and award collection for one library.

    Chart collections have no known summary text -- passing one would
    invent copy the operator never wrote, so ``None`` is passed and
    whatever Plex already has is left alone. The year award collections are
    the one exception: their summary is templated, not invented.
    """
    actions: list[str] = []
    dry_run = not config.collections.apply_to_plex
    index = build_imdb_index(section)

    if config.collections.charts:
        for title, chart in CHART_COLLECTIONS.get(library_type, []):
            items = resolve_ids(index, await _chart_ids(http, chart))
            actions += await reconcile_list_collection(
                session, section, library, title, items, label, dry_run=dry_run,
            )

    if config.collections.awards and library_type == "Movie":
        event = await _award_event(http)

        for title, categories in AWARD_COLLECTIONS:
            ids = winners_for_categories(event, categories) if event else []
            items = resolve_ids(index, ids)
            actions += await reconcile_list_collection(
                session, section, library, title, items, label, dry_run=dry_run,
            )

        for year in (recent_years(event) if event else []):
            items = resolve_ids(index, winners_for_year(event, year))
            actions += await reconcile_list_collection(
                session, section, library, "Oscars Winners %s" % year, items, label,
                summary=YEAR_SUMMARY % year, dry_run=dry_run,
            )

    return actions
