"""Award-ceremony winners, from the community IMDb-Awards dataset.

One file holds every ceremony year of one event, so all seven Oscars
collections resolve from a single fetch. Shape is
``{year: {award_group: {category: {"nominee": [...], "winner": [...]}}}}``
with lower-cased keys; years with no data yet appear as ``{}``.

Nothing here is Oscars-specific any more: every function takes the event's
data, and the event id is a parameter. Which ceremonies this service actually
offers is the builder's registry (``builders/imdb_award.py``); what belongs
here is the dataset -- its two files, and the category vocabularies, which are
data about the ceremonies rather than about our collections.
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

# The Golden Globes' equivalents, transcribed from Kometa's own
# ``defaults/award/golden.yml`` (``category_filter``, fetched 2026-08-25) --
# not composed here. The ceremony splits its top film award by genre and has
# renamed every branch of it repeatedly, so "best picture" is fifteen category
# names; anything left out is a winner the collection would silently miss.
GOLDEN_GLOBES_BEST_PICTURE = (
    "best animated feature film",
    "best animated film",
    "best foreign film",
    "best foreign language film",
    "best foreign-language foreign film",
    "best motion picture - animated",
    "best motion picture - comedy",
    "best motion picture - comedy or musical",
    "best motion picture - drama",
    "best motion picture - foreign language",
    "best motion picture - musical",
    "best motion picture - musical or comedy",
    "best motion picture - non-english language",
    "best motion picture, musical or comedy",
    "best picture",
)
GOLDEN_GLOBES_BEST_DIRECTOR = (
    "best director",
    "best director - motion picture",
)


class UnknownAwardEvent(Exception):
    """An event id the community dataset does not cover.

    Its own class because the answer to it is never "try harder": Kometa, at
    this point, falls back to scraping IMDb one request per ceremony year, and
    this service deliberately does not (``docs/research/kometa-collections.md``
    §2.3). Raised rather than returned so it travels the builder's failure
    path -- the engine contains it and leaves the collection untouched.
    """


async def fetch_event_validation(http: httpx.AsyncClient) -> dict:
    """Every event id the community dataset covers, keyed by id.

    The same file Kometa reads first (``imdb.py:1034``) and for the same
    reason: it is the only way to know whether an event has data *before*
    asking for a file that may not exist. Ours is a smaller question than
    Kometa's -- it decides git-versus-scrape, we decide build-versus-refuse.
    """
    response = await http.get("%s/event_validation.yml" % BASE_URL)
    response.raise_for_status()
    validation = yaml.safe_load(response.text)
    if not isinstance(validation, dict) or not validation:
        raise ValueError("the award event validation list returned an unexpected body")
    return validation


def require_known_event(validation: dict, event_id: str) -> None:
    """Refuse an event the dataset does not carry, before fetching anything.

    A 404 on the event file would say the same thing eventually, in a form an
    operator has to decode. This says it once, in words, and it says it for
    the case the 404 does not cover either: an event id that exists on IMDb
    but was never added to the community dataset, which is precisely when
    Kometa would start scraping.
    """
    if event_id not in validation:
        raise UnknownAwardEvent(
            "award event %r is not in the community dataset at %s "
            "(event_validation.yml lists %d events). Kometa would scrape IMDb "
            "for it, one request per ceremony year; this service refuses "
            "instead." % (event_id, BASE_URL, len(validation))
        )


async def fetch_event(http: httpx.AsyncClient, event_id: str = EVENT_ID) -> dict:
    """The whole event dataset. Raises rather than returning nothing."""
    response = await http.get("%s/events/%s.yml" % (BASE_URL, event_id))
    response.raise_for_status()
    event = yaml.safe_load(response.text)
    if not isinstance(event, dict) or not event:
        raise ValueError("award event %r returned an unexpected body" % event_id)
    return event


def _by_year(event: dict) -> dict[str, dict]:
    """The event re-keyed by ``str`` year.

    Years are quoted in the dataset today, but nothing enforces that: a
    single unquoted key parses as an ``int``, and sorting a mix of ``int``
    and ``str`` raises ``TypeError`` -- outside the caller's try, so it takes
    the whole run down. Coercing here also keeps a ``str`` year handed back
    by ``recent_years`` usable as a lookup in ``winners_for_year``.
    """
    return {str(year): data for year, data in event.items()}


def recent_years(event: dict, count: int = 5) -> list[str]:
    """The most recent ceremony years that actually have data, newest first.

    Empty years must be skipped: the live dataset carries a placeholder for
    the next, unheld ceremony, and including it would create an empty
    collection for a year that has no winners.
    """
    years = _by_year(event)
    return sorted((y for y in years if years[y]), reverse=True)[:count]


def _dedupe(ids: list[str]) -> list[str]:
    """Drop repeats, keeping first occurrence -- a film can win twice."""
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def winners_for_categories(event: dict, categories: tuple[str, ...]) -> list[str]:
    """Winners of the given categories across every year, newest first."""
    wanted = {c.lower() for c in categories}
    years = _by_year(event)
    ids: list[str] = []
    for year in sorted((y for y in years if years[y]), reverse=True):
        for group in years[year].values():
            for category, entry in group.items():
                if category.lower() in wanted:
                    ids.extend(entry.get("winner") or [])
    return _dedupe(ids)


def winners_for_year(event: dict, year: str) -> list[str]:
    """Every category's winners for one ceremony year."""
    ids: list[str] = []
    for group in (_by_year(event).get(str(year)) or {}).values():
        for entry in group.values():
            ids.extend(entry.get("winner") or [])
    return _dedupe(ids)
