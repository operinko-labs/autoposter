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
- The award event, the IMDb index and the section's collection listing are
  each obtained once and shared across every collection that needs them,
  rather than once per collection. The index costs a full ``section.all()``
  and the listing returns all 305 collections on the production Movies
  section, so the index is additionally deferred until something actually
  asks for it -- a library with no enabled source must not pay for it.
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

# Summaries are not ours to word: they come verbatim from Kometa's
# translations file, transcribed in docs/research/kometa-collections.md §5.
# The chart templates take the lower-cased library type ("movie"/"show").
CHART_SUMMARIES: dict[str, str] = {
    "IMDb Popular": "List of IMDb Popular %ss.",
    "IMDb Top 250": "List of IMDb Top 250 %ss.",
    "IMDb Lowest Rated": "List of IMDb Lowest Rated %ss.",
}

_OSCAR_SUMMARY = (
    "The Academy Award for Best %s is one of the Academy Awards presented "
    "annually by the Academy of Motion Picture Arts and Sciences since the "
    "awards debuted in 1929."
)

# The two static winner collections, movies only.
AWARD_COLLECTIONS: list[tuple[str, tuple[str, ...], str]] = [
    ("Oscars Best Picture Winners", BEST_PICTURE, _OSCAR_SUMMARY % "Picture"),
    ("Oscars Best Director Winners", BEST_DIRECTOR, _OSCAR_SUMMARY % "Director"),
]

# collection title -> the poster kind's key, for the two static award collections.
AWARD_POSTER_KEYS: dict[str, str] = {
    "Oscars Best Picture Winners": "best_picture_winner",
    "Oscars Best Director Winners": "best_director_winner",
}

YEAR_SUMMARY = "Academy Awards (Oscars) Winners for %s."

# §2.4: the five dynamic year collections override collection_order to
# release; only the two static winner collections keep the custom order.
YEAR_SORT = "release"


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

    Every summary written here is a verbatim Kometa translation string, not
    copy invented for this project -- see ``CHART_SUMMARIES``, the
    ``AWARD_COLLECTIONS`` entries and ``YEAR_SUMMARY``.
    """
    actions: list[str] = []
    dry_run = not config.collections.apply_to_plex
    charts = config.collections.charts
    awards = config.collections.awards and library_type == "Movie"
    if not charts and not awards:
        return actions

    adopt = config.collections.adopt
    adopt_from = config.collections.adopt_from
    adopt_removes_prior_label = config.collections.adopt_removes_prior_label
    protect_labels = config.collections.protect_labels

    index: dict[str, object] | None = None

    def imdb_index() -> dict[str, object]:
        nonlocal index
        if index is None:
            index = build_imdb_index(section)
        return index

    existing = {c.title: c for c in section.collections()}
    library_word = library_type.lower()

    if charts:
        for title, chart in CHART_COLLECTIONS.get(library_type, []):
            items = resolve_ids(imdb_index(), await _chart_ids(http, chart))
            actions += await reconcile_list_collection(
                session, section, library, title, items, label,
                summary=CHART_SUMMARIES[title] % library_word,
                dry_run=dry_run, existing=existing,
                adopt=adopt, adopt_from=adopt_from,
                adopt_removes_prior_label=adopt_removes_prior_label,
                protect_labels=protect_labels,
                kind="chart", key=title, http=http, config=config,
            )

    if awards:
        event = await _award_event(http)

        for title, categories, summary in AWARD_COLLECTIONS:
            ids = winners_for_categories(event, categories) if event else []
            items = resolve_ids(imdb_index(), ids)
            actions += await reconcile_list_collection(
                session, section, library, title, items, label,
                summary=summary, dry_run=dry_run, existing=existing,
                adopt=adopt, adopt_from=adopt_from,
                adopt_removes_prior_label=adopt_removes_prior_label,
                protect_labels=protect_labels,
                kind="award_static", key=AWARD_POSTER_KEYS[title], http=http, config=config,
            )

        for year in (recent_years(event) if event else []):
            items = resolve_ids(imdb_index(), winners_for_year(event, year))
            actions += await reconcile_list_collection(
                session, section, library, "Oscars Winners %s" % year, items, label,
                summary=YEAR_SUMMARY % year, sort=YEAR_SORT,
                dry_run=dry_run, existing=existing,
                adopt=adopt, adopt_from=adopt_from,
                adopt_removes_prior_label=adopt_removes_prior_label,
                protect_labels=protect_labels,
                kind="award_year", key=str(year), http=http, config=config,
            )

    return actions
