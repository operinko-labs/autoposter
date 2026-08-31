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

from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

EVENT_ID = "ev0000003"
BASE_URL = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"

# The same class-default every provider client already carries --
# ``providers/fanart.py:81``, ``providers/tmdb.py:69``, ``providers/tvdb.py:108``,
# ``facts/mdblist.py:204`` -- all default ``cache_ttl_seconds: int = 24 * 3600``.
# Award category data changes roughly once a year per ceremony, so this is not
# a tight bound; it is the existing convention, reused rather than invented
# (roadmap row 151's TTL decision). ``BuilderContext`` carries no application
# config for a builder to read a live value from (only ``cache`` itself), so
# the literal is the correct level for this to live at, not a threaded-through
# setting.
_CACHE_TTL_SECONDS = 24 * 3600

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

# ---------------------------------------------------------------------------
# The other fourteen ceremonies, all transcribed 2026-08-26 from the
# ``imdb_award:`` block of Kometa's own ``defaults/award/<file>.yml`` -- its
# ``category_filter`` below, its ``award_filter`` further down. Kometa parses
# both with ``util.parse(..., datatype="lowerlist")`` (``modules/builder.py``
# :2321,:2327), so a scalar is a one-item list and every item is lower-cased;
# these are recorded already-lowered, in Kometa's own order, which is why
# ``César`` appears here as ``césar``.
#
# Four ceremonies set no ``category_filter`` at all -- Berlinale, Cannes, the
# National Film Registry and Sundance -- and for them Kometa reads every
# category of the groups it kept (``if data["category_filter"] and ...``).
# That is ``categories=None`` on the row, not a constant here.
# ---------------------------------------------------------------------------

BAFTA_BEST_FILM = (
    "best film",
    "best film from any source",
)
CESAR_BEST_FILM = ("best film (meilleur film)",)
CRITICS_CHOICE_BEST_PICTURE = ("best picture",)
# The Emmys have renamed their top series awards in every decade since 1949,
# and Kometa's list carries all of it -- including five spellings of the
# animated-programme award that differ only in their parenthetical.
EMMY_BEST_IN_CATEGORY = (
    "best comedy series",
    "best comedy show",
    "best dramatic anthology series",
    "best dramatic program",
    "best dramatic series",
    "best dramatic series - less than one hour",
    "best dramatic series - one hour or longer",
    "best series - half hour or less",
    "best series - one hour or more",
    "outstanding animated program",
    "outstanding animated program (for programming less than one hour)",
    "outstanding animated program (for programming more than one hour)",
    "outstanding animated program (for programming one hour or less)",
    "outstanding animated program (for programming one hour or more)",
    "outstanding animated programming",
    "outstanding comedy series",
    "outstanding drama series",
    "outstanding drama series - continuing",
    "outstanding drama/comedy - limited episodes",
    "outstanding dramatic program",
    "outstanding dramatic series",
    "outstanding miniseries",
    "outstanding series - comedy",
    "outstanding series - drama",
)
# Two of these name a single year ("the movie of 2022", "the show of 2021"):
# the ceremony renamed its top award for one edition each time, and Kometa's
# list follows. They are transcribed rather than tidied away.
PEOPLES_CHOICE_FAVOURITE = (
    "all-time favorite tv program",
    "favorite all-time motion picture",
    "favorite motion picture",
    "favorite movie",
    "favorite non-musical motion picture",
    "favorite overall motion picture",
    "favorite tv show",
    "the movie of 2022",
    "the show of 2021",
)
RAZZIE_WORST_PICTURE = ("worst picture",)
SAG_BEST_ENSEMBLE = (
    "outstanding performance by a cast",
    "outstanding performance by a cast in a motion picture",
    "outstanding performance by a cast in a theatrical motion picture",
    "outstanding performance by an ensemble in a comedy series",
    "outstanding performance by an ensemble in a drama series",
    "outstanding performance by the cast of a theatrical motion picture",
)
SPIRIT_BEST_FEATURE = ("best feature",)
TIFF_PEOPLES_CHOICE = (
    "best film",
    "people's choice award",
    "grolsch people's choice award",
    "gala or special presentations",
)
VENICE_GOLDEN_LION = (
    "golden lion",
    "best feature film",
    "best film",
    "grand international award",
)

# The ``award_filter`` halves -- award *groups* rather than categories. Six
# ceremonies use one; the other ten read every group, which is ``None``.
BERLINALE_GOLDEN_BEAR_AWARDS = ("golden berlin bear",)
CANNES_PALME_DOR_AWARDS = ("palme d'or",)
CESAR_AWARDS = ("césar",)
SUNDANCE_GRAND_JURY_AWARDS = ("grand jury prize",)
TIFF_PEOPLES_CHOICE_AWARDS = (
    "people's choice award",
    "grolsch people's choice award",
)
VENICE_GOLDEN_LION_AWARDS = (
    "golden lion",
    "international critics award",
    "grand international award",
)


class UnknownAwardEvent(Exception):
    """An event id the community dataset does not cover.

    Its own class because the answer to it is never "try harder": Kometa, at
    this point, falls back to scraping IMDb one request per ceremony year, and
    this service deliberately does not (``docs/research/kometa-collections.md``
    §2.3). Raised rather than returned so it travels the builder's failure
    path -- the engine contains it and leaves the collection untouched.
    """


async def fetch_event_validation(
    http: httpx.AsyncClient,
    *,
    cache: ProviderCache | None = None,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> dict:
    """Every event id the community dataset covers, keyed by id.

    The same file Kometa reads first (``imdb.py:1034``) and for the same
    reason: it is the only way to know whether an event has data *before*
    asking for a file that may not exist. Ours is a smaller question than
    Kometa's -- it decides git-versus-scrape, we decide build-versus-refuse.

    Routed through ``providers.fetch.fetch_json`` since roadmap row 151:
    ``ProviderCache`` needed no extension to hold a YAML-decoded dict, only a
    decoder at the fetch site (``fetch_json``'s ``decode`` parameter). ``cache``
    is None for a direct caller -- unchanged behaviour, one request per call.
    """
    url = "%s/event_validation.yml" % BASE_URL
    validation = await fetch_json(
        method="GET", url=url, params=None,
        request=lambda: http.get(url),
        cache=cache, ttl_seconds=ttl_seconds,
        decode=lambda response: yaml.safe_load(response.text),
    )
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


async def fetch_event(
    http: httpx.AsyncClient,
    event_id: str = EVENT_ID,
    *,
    cache: ProviderCache | None = None,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> dict:
    """The whole event dataset. Raises rather than returning nothing.

    A 404 here is now a ``fetch_json`` "confirmed nothing there" (cached as a
    negative, same as every other provider's 404 convention) rather than an
    ``httpx.HTTPStatusError`` raised directly -- this function still raises
    either way, now via the same ``ValueError`` an unexpected-but-200 body
    already got, so the builder contract (``build`` raises, the engine
    contains) is unchanged. What is new: a genuine 404 on this URL is now
    memoised as "not found" for the TTL rather than re-requested every pass --
    correct for the same reason every other provider client already caches its
    404s, since this file's URL does not 404 in ordinary operation.
    """
    url = "%s/events/%s.yml" % (BASE_URL, event_id)
    event = await fetch_json(
        method="GET", url=url, params=None,
        request=lambda: http.get(url),
        cache=cache, ttl_seconds=ttl_seconds,
        decode=lambda response: yaml.safe_load(response.text),
    )
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


def _wanted_groups(award_filter: tuple[str, ...] | None) -> set[str] | None:
    """The award groups to read, or ``None`` for all of them.

    Kometa filters a ceremony's data at two levels -- the award group first,
    the category within it second (``modules/imdb.py`` ``_award``: ``if
    award_filter and award not in award_filter: continue``) -- and until this
    existed we read every group. It matters for the ceremonies that award more
    than one medium under one event id: BAFTA's ``ev0000123`` holds film,
    television and games, and its film collections must not pick up a
    television category that happens to share a name.

    Lower-cased on both sides, as the category filter already is: the dataset's
    keys are lower-cased today but nothing upstream promises that, and a
    capitalised group key would otherwise turn a filtered collection empty
    rather than wrong -- the silent failure this whole module is careful about.
    """
    return {a.lower() for a in award_filter} if award_filter else None


def winners_for_categories(
    event: dict,
    categories: tuple[str, ...] | None,
    award_filter: tuple[str, ...] | None = None,
) -> list[str]:
    """Winners of the given categories across every year, newest first.

    ``award_filter`` narrows to one or more of the ceremony's award groups;
    the default reads all of them, which is what every single-medium ceremony
    means and what this function did before the argument existed.

    ``categories`` is ``None`` for every category of whatever groups survived
    the award filter. That is not a convenience: it is what four of Kometa's
    own ceremonies configure -- Berlinale, Cannes, Sundance and the National
    Film Registry set no ``category_filter``, and Kometa reads a missing
    filter as "no filter" (``if data["category_filter"] and cat not in ...``,
    ``modules/imdb.py`` ``_award``). Cannes' whole collection is one *group*,
    the Palme d'Or, whose categories nobody enumerates.
    """
    wanted = {c.lower() for c in categories} if categories else None
    groups = _wanted_groups(award_filter)
    years = _by_year(event)
    ids: list[str] = []
    for year in sorted((y for y in years if years[y]), reverse=True):
        for award, group in years[year].items():
            if groups is not None and award.lower() not in groups:
                continue
            for category, entry in group.items():
                if wanted is None or category.lower() in wanted:
                    ids.extend(entry.get("winner") or [])
    return _dedupe(ids)


def uncovered_categories(
    event: dict,
    categories: tuple[str, ...] | None,
    award_filter: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Which of ``categories`` never appears anywhere in ``event``, across
    every year and (if narrowed) every wanted award group.

    Empty when ``categories`` is ``None`` -- there is nothing named to have
    drifted (the four ceremonies with no ``category_filter``). A category
    present with zero WINNERS some year is not "uncovered": winning nothing in
    a given year is ordinary Oscars history. The category NAME never
    appearing at all -- upstream renamed it after this codebase's tuple was
    transcribed -- is the drift roadmap row 153 was filed for, and it is the
    only thing this function reports.

    Rides data the caller already fetched this pass (``_event``'s live
    result); it makes no fetch of its own and belongs beside
    ``winners_for_categories``, not inside it, because the caller decides what
    to do with drift (roadmap row 153: log it) and this function only detects
    it.
    """
    if not categories:
        return ()
    wanted = {c.lower() for c in categories}
    groups = _wanted_groups(award_filter)
    seen: set[str] = set()
    for year_data in _by_year(event).values():
        for award, group in year_data.items():
            if groups is not None and award.lower() not in groups:
                continue
            seen.update(category.lower() for category in group)
    return tuple(c for c in categories if c.lower() not in seen)


def winners_for_year(event: dict, year: str) -> list[str]:
    """Every award group's every category's winners for one ceremony year.

    Deliberately *not* group-filterable, though it briefly was. Not one of the
    sixteen ceremonies Kometa ships a default for narrows its year collections
    by award group: ``award_filter`` appears only under ``collections:`` in
    ``defaults/award/*.yml``, never under ``dynamic_collections:`` -- so the
    parameter had no caller and no ceremony that wanted one. A multi-medium
    ceremony's year collection therefore carries that year's television
    winners alongside its film ones, which is Kometa's own behaviour and the
    reason ``AwardEvent.library_types`` is the gate rather than this.
    """
    ids: list[str] = []
    for group in (_by_year(event).get(str(year)) or {}).values():
        for entry in group.values():
            ids.extend(entry.get("winner") or [])
    return _dedupe(ids)
