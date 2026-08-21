"""Academy Awards winners, from the community IMDb-Awards dataset.

One file holds every ceremony year, so all seven Oscars collections resolve
from a single fetch. Shape is
``{year: {award_group: {category: {"nominee": [...], "winner": [...]}}}}``
with lower-cased keys; years with no data yet appear as ``{}``.
"""
import httpx
import yaml

EVENT_ID = "ev0000003"
BASE_URL = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"

# Category naming has changed across roughly 95 ceremonies, so each award
# needs every historical variant.
BEST_PICTURE = (
    "best motion picture of the year",
    "best picture",
    "best picture, production",
    "best picture, unique and artistic production",
)
BEST_DIRECTOR = (
    "best achievement in directing",
    "best director",
    "best director, comedy picture",
    "best director, dramatic picture",
)


async def fetch_event(http: httpx.AsyncClient, event_id: str = EVENT_ID) -> dict:
    """The whole event dataset. Raises rather than returning nothing."""
    response = await http.get("%s/events/%s.yml" % (BASE_URL, event_id))
    response.raise_for_status()
    event = yaml.safe_load(response.text)
    if not isinstance(event, dict) or not event:
        raise ValueError("award event %r returned an unexpected body" % event_id)
    return event


def recent_years(event: dict, count: int = 5) -> list[str]:
    """The most recent ceremony years that actually have data, newest first.

    Empty years must be skipped: the live dataset carries a placeholder for
    the next, unheld ceremony, and including it would create an empty
    collection for a year that has no winners.
    """
    return sorted((y for y in event if event[y]), reverse=True)[:count]


def _dedupe(ids: list[str]) -> list[str]:
    """Drop repeats, keeping first occurrence -- a film can win twice."""
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def winners_for_categories(event: dict, categories: tuple[str, ...]) -> list[str]:
    """Winners of the given categories across every year, newest first."""
    wanted = {c.lower() for c in categories}
    ids: list[str] = []
    for year in sorted((y for y in event if event[y]), reverse=True):
        for group in event[year].values():
            for category, entry in group.items():
                if category.lower() in wanted:
                    ids.extend(entry.get("winner") or [])
    return _dedupe(ids)


def winners_for_year(event: dict, year: str) -> list[str]:
    """Every category's winners for one ceremony year."""
    ids: list[str] = []
    for group in (event.get(year) or {}).values():
        for entry in group.values():
            ids.extend(entry.get("winner") or [])
    return _dedupe(ids)
