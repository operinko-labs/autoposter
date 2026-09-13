import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from autoposter.facts.models import GatheredFacts
from autoposter.facts.tmdb_budget import (
    TmdbRateBudget,
    TmdbRateLimited,
    retry_after_seconds,
)
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"


def _as_date(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# Roadmap row 227. Kometa's own table (``modules/operations.py`` at v2.4.8) is
# six codes -- premiere 1, theatricallimited 2, theatrical 3, digital 4,
# physical 5, tv 6. Two are offered here, and the other four are NOT
# transcribed as dead entries: a source this service cannot serve is a config
# load error (``config/schema.py``'s ``Literal``), so a map entry no
# ``Literal`` can reach could only ever be a lie about what loads.
TMDB_RELEASE_TYPES: dict[str, int] = {
    "tmdb_premiere": 1,
    "tmdb_digital": 4,
}


def _release_date(value: object) -> date | None:
    """TMDb's ``release_date``, which is ISO-8601 WITH a time and a ``Z``.

    Measured rather than assumed: ``/movie/550/release_dates`` answers
    ``"1999-10-15T00:00:00.000Z"``. ``_as_date`` above is
    ``strptime(value, "%Y-%m-%d")`` and returns ``None`` for every one of
    these strings, so reusing it unchanged would ship a feature that silently
    never fires -- and a hand-typed ``"1999-10-15"`` fixture would pass
    anyway. The date half is the first ten characters; the time and the zone
    are dropped rather than parsed, because Kometa compares these at date
    granularity and so does this project's writer.
    """
    if not isinstance(value, str):
        return None
    return _as_date(value[:10])


def parse_release_date(payload: dict, type_code: int) -> date | None:
    """The earliest ``type_code`` release date in ANY region, or ``None``.

    Kometa's rule verbatim (``modules/operations.py``'s
    ``tmdb_release_date``): it consults ``config.TMDb.region`` only when that
    is set AND the movie carries it, and otherwise iterates every region and
    takes ``min()``. This project has no TMDb region setting at all, so the
    faithful port is Kometa's own default path -- all regions, ``min()`` --
    with no knob and no region parameter. A parameter no caller can ever pass
    is a config knob wearing a signature's clothes.

    ``min()`` also decides the WITHIN-region case, which is not theoretical:
    the captured response carries two type-1 entries and three type-5 entries
    for one movie in one country.

    Nothing matching anywhere is ``None`` -- Kometa's ``raise Failed`` at this
    seam. A source that yields nothing writes nothing, which is this
    project's standing rule that a missing value must never be written as an
    empty one (``facts/models.py:9-11``).
    """
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    dates: list[date] = []
    for country in results:
        if not isinstance(country, dict):
            continue
        entries = country.get("release_dates")
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != type_code:
                continue
            parsed = _release_date(entry.get("release_date"))
            if parsed is not None:
                dates.append(parsed)
    return min(dates) if dates else None


def _rating(payload: dict) -> float | None:
    """``vote_average``, treating 0 as absent.

    TMDB reports 0.0 for titles nobody has voted on. Writing that would render
    a "0%" badge, which is worse than no badge.
    """
    value = payload.get("vote_average")
    if value is None:
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    return rating if rating > 0 else None


def _genres(payload: dict) -> list[str]:
    """Genre names from the genres list, skipping non-dict entries."""
    genres = payload.get("genres")
    if isinstance(genres, list):
        return [g.get("name") for g in genres if isinstance(g, dict) and g.get("name")]
    return []


def _first_name(entries: object) -> str | None:
    """First element's name, unsorted — the rule Kometa uses."""
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        return entries[0].get("name")
    return None


def _countries(payload: dict) -> list[str]:
    """``origin_country``, which TMDb sends as a list of ISO-3166-1 codes.

    Deliberately NOT ``production_countries``: the two disagree on real titles
    (``/movie/940143`` reads ``["US"]`` here and ``[GB, US]`` there), and the
    production country is what Plex's own ``<Country>`` already carries.

    A bare string is refused rather than iterated: ``"US"`` would otherwise
    become ``["U", "S"]``, which is a plausible wrong value and therefore worse
    than an absent one -- the same reasoning ``_genres`` applies to a
    ``genres`` that is not a list.
    """
    countries = payload.get("origin_country")
    if isinstance(countries, list):
        return [one for one in countries if isinstance(one, str) and one]
    return []


def _language(payload: dict) -> str | None:
    """``original_language``, an ISO-639-1 code. Titled from the code itself
    downstream: neither this service nor Kometa's pack files carry a code->name
    table, which is the divergence roadmap row 190 already records for the
    audio/subtitle language families.

    Not ``languages``, which a show payload also carries: that is the list of
    languages the show is available in, not the one it was made in.
    """
    value = payload.get("original_language")
    return value if isinstance(value, str) and value else None


def _collection_id(payload: dict) -> int | None:
    """``belongs_to_collection.id``, or None.

    ``null`` is the normal case -- most films are in no franchise -- so it is
    the absent case and not a shape error, and it must read identically to the
    key being missing altogether, which is the shape ``/tv/{id}`` sends. The
    NAME is deliberately not read: it is a property of the collection rather
    than of the item, and the franchise family reads it once per franchise from
    ``/collection/{id}``, which is the same URL (and therefore the same
    provider-cache entry) its membership already comes from.
    """
    entry = payload.get("belongs_to_collection")
    if not isinstance(entry, dict):
        return None
    try:
        return int(entry["id"])
    except (KeyError, TypeError, ValueError):
        return None


# Kometa's own `discover_status`, transcribed entry for entry from
# `/modules/tmdb.py:108` in the pinned v2.4.8 image: TMDb's `status` strings
# on the left, the tokens an operator writes in a `tmdb_status:` filter on
# the right. Kometa validates a written value against exactly these six as a
# `commalist` (`/modules/builder.py:4369`, `options=[v for k, v in
# tmdb.discover_status.items()]`) and compares with
# `discover_status[item.status]` against that written set
# (`/modules/tmdb.py:685-693`).
#
# THE MAPPING HAPPENS HERE, not at filter time, and that is the whole reason
# this table is in the parser: the TOKEN is the value space the comparison
# lives in, and TMDb's own string is not. Storing `"Returning Series"` in a
# column named `tmdb_status` would be a column carrying Kometa's NAME with a
# different meaning -- exactly the trap roadmap row 156 exists to name (see
# `ItemFacts.content_rating`, which is MDBList's Common Sense AGE rating and
# not Plex's certification). One mapping site, at the edge, and every reader
# downstream gets the value the filter compares in.
TMDB_SHOW_STATUS = {
    "Returning Series": "returning",
    "Planned": "planned",
    "In Production": "production",
    "Ended": "ended",
    "Canceled": "canceled",
    "Pilot": "pilot",
}


def _show_status(payload: dict) -> str | None:
    """TMDb's ``status`` as Kometa's own token, or ``None``.

    A status outside the six is KEPT VERBATIM rather than dropped, and that
    is a declared divergence in this service's favour. Upstream's
    ``discover_status[item.status]`` is a bare subscript with no guard
    (``/modules/tmdb.py:685``), so a seventh TMDb status raises ``KeyError``
    there; TMDb's ``status`` is a closed enum in practice, which is why
    upstream gets away with it. Here the unmapped string is stored as TMDb
    spelled it: it matches none of the four shipped bands (which name
    ``returning``/``canceled``/``ended`` only), so the DRAWN outcome is
    upstream's minus the crash -- and, unlike ``None``, it stays
    distinguishable from "TMDb has not told us", which is what the NULL
    column means and what rows 189/192's coverage arithmetic depends on
    being able to tell apart.

    Blank and whitespace-only read as absent, the rule ``_original_title``
    already applies: a missing value must never be written as an empty one.
    """
    value = payload.get("status")
    if not isinstance(value, str) or not value.strip():
        return None
    return TMDB_SHOW_STATUS.get(value, value)


def _original_title(payload: dict, key: str) -> str | None:
    """TMDb's original-language title, or ``None``.

    ``original_title`` on a movie, ``original_name`` on a show -- TMDb's
    asymmetry, not ours. Blank and whitespace-only are read as absent: a
    missing value must never be written to Plex as an empty one.

    Provenance is NOT recorded here. Whether this value is used at all depends
    on ``operations.original_title_source`` naming tmdb, and this parser cannot
    see config -- ``gather_facts`` records the source when it keeps the value.
    """
    value = payload.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def parse_movie_facts(payload: dict) -> GatheredFacts:
    rating = _rating(payload)
    studio = _first_name(payload.get("production_companies"))
    genres = _genres(payload)
    released = _as_date(payload.get("release_date"))
    countries = _countries(payload)
    language = _language(payload)
    collection_id = _collection_id(payload)
    original_title = _original_title(payload, "original_title")
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", released),
                       ("tmdb_origin_country", countries),
                       ("tmdb_original_language", language)):
        if value:
            sources[key] = "tmdb"
    if collection_id is not None:
        sources["tmdb_collection_id"] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=released,
        original_title=original_title,
        tmdb_origin_country=countries,
        tmdb_original_language=language,
        tmdb_collection_id=collection_id,
        sources=sources,
    )


def parse_show_facts(payload: dict) -> GatheredFacts:
    """Shows take their studio from ``networks``, not ``production_companies``."""
    rating = _rating(payload)
    studio = _first_name(payload.get("networks"))
    genres = _genres(payload)
    aired = _as_date(payload.get("first_air_date"))
    countries = _countries(payload)
    language = _language(payload)
    original_title = _original_title(payload, "original_name")
    # Roadmap row 100, sub-phase C2c. Two more keys off the payload this
    # function is already handed -- Kometa reads the same two out of the same
    # response in one block (`/modules/tmdb.py:272-273`). ``last_air_date`` is
    # the show's LAST episode; ``first_air_date`` above is its first, and they
    # go to different fields on purpose -- reading one for the other is the
    # whole reason `last_episode_aired` is its own name rather than an alias
    # of `originally_available`.
    status = _show_status(payload)
    last_aired = _as_date(payload.get("last_air_date"))
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", aired),
                       ("tmdb_origin_country", countries),
                       ("tmdb_original_language", language),
                       ("tmdb_status", status),
                       ("last_episode_aired", last_aired)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=aired,
        original_title=original_title,
        tmdb_origin_country=countries,
        tmdb_original_language=language,
        tmdb_status=status,
        last_episode_aired=last_aired,
        sources=sources,
    )


@dataclass(frozen=True)
class CollectionOrder:
    """A franchise collection's name and its parts' TMDb ids, in TMDb's order.

    Roadmap row 268. ``parts`` holds ints, where ``providers/tmdb_lists.py``'s
    ``collection_parts`` hands the collections engine strings: that engine
    resolves ids as strings everywhere, and this value is compared against
    ``ResolvedItem.tmdb_id``, which is an int.
    """

    name: str
    parts: list[int]


def parse_collection_order(payload: dict) -> CollectionOrder | None:
    """``/collection/{id}``'s parts, exactly as TMDb lists them.

    The order is the SOURCE's and nothing is re-sorted here (operator rule,
    2026-09-12: "the sort order should be whatever comes from the list
    source"). ``providers/tmdb_lists.py::collection_parts`` hands the
    collections engine this same array in this same order, so a franchise
    collection and its members' sort titles agree by construction.

    A payload with no ``parts`` array is no order at all, never an empty one:
    an empty order would give every member "not in its own collection", which
    is indistinguishable from a real answer downstream.
    """
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return None
    ids: list[int] = []
    for entry in parts:
        if not isinstance(entry, dict):
            continue
        try:
            ids.append(int(entry["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    name = payload.get("name")
    return CollectionOrder(name=name if isinstance(name, str) else "", parts=ids)


def parse_season_episode_ratings(payload: dict) -> dict[int, float]:
    """``{episode_number: vote_average}`` for one season, skipping unrated."""
    ratings: dict[int, float] = {}
    episodes = payload.get("episodes")
    if isinstance(episodes, list):
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            number = episode.get("episode_number")
            rating = _rating(episode)
            if number is None or rating is None:
                continue
            ratings[int(number)] = rating
    return ratings


class TMDBFactsClient:
    """Reads ratings and descriptive metadata from TMDB.

    Separate from the artwork client because it asks different endpoints for
    different reasons; they share only the bearer token.

    Every request goes through the shared provider cache seam. That matters most for
    episodes: one season-pack import asks for the same season's ratings once
    per episode, and without the cache that is one API call each. With it, the
    first episode pays and the rest are free until the TTL expires.
    """

    name = "TMDB"

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
        budget: TmdbRateBudget | None = None,
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        # None means "no shared window": every 429 is then that one request's
        # own failure, out of ``raise_for_status`` exactly as it shipped. The
        # collections CLI (``collections/__main__.py``) constructs this client
        # without one deliberately -- its single use is a `tmdb_summary:`
        # definition's overview, where a refusal is one definition's problem
        # and there is no pipeline behind it to protect.
        self._budget = budget

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "accept": "application/json"}

    async def _get(self, path: str) -> dict | None:
        url = f"{BASE_URL}{path}"
        if self._budget is not None and await self._budget.blocked():
            raise TmdbRateLimited(
                "tmdb is inside a backoff window a 429 opened; this read was "
                "not attempted. A drift sweep comes round to it again, but not "
                "quickly: if the rest of the gather succeeded the refreshed "
                "fetched_at holds the item out for scheduler.drift_max_age_days "
                "and then sorts it behind everything older in a "
                "scheduler.drift_batch_size rotation, so a re-queued item is "
                "the only quick route back. The window itself cannot be "
                "shortened once open -- only waiting it out, or restarting "
                "with operations.tmdb_backoff_seconds = 0 to disable it, "
                "clears it early"
            )
        try:
            return await fetch_json(
                method="GET",
                url=url,
                params=None,
                request=lambda: self._client.get(url, headers=self._headers()),
                cache=self._cache,
                ttl_seconds=self._cache_ttl_seconds,
            )
        except httpx.HTTPStatusError as exc:
            # 429 ONLY. Everything else keeps raising exactly as it did -- a
            # 500 is not a budget and must not open a window that stops the
            # whole library being read.
            #
            # Nothing is cached on this path, and that is the row-147 promise:
            # ``fetch_json`` writes the cache on a 404 and on a 2xx, both
            # AFTER ``raise_for_status`` would have fired, so a refusal body
            # cannot become an answer with a TTL.
            if exc.response.status_code != 429 or self._budget is None:
                raise
            await self._budget.note_refusal(retry_after_seconds(exc.response))
            # Class name only: an httpx error's ``str()`` carries the full URL.
            logger.warning("tmdb refused a read: %s", type(exc).__name__)
            raise TmdbRateLimited(
                "tmdb answered 429. The window is now open and further reads "
                "are skipped until it closes; nothing shortens an open window "
                "-- wait it out, or restart with operations.tmdb_backoff_seconds "
                "= 0 to disable it"
            ) from exc

    async def movie(self, tmdb_id: int) -> GatheredFacts:
        payload = await self._get(f"/movie/{tmdb_id}")
        return parse_movie_facts(payload) if payload else GatheredFacts()

    async def release_date(self, tmdb_id: int, source: str) -> date | None:
        """The earliest ``source`` release date TMDb has for this movie (row 227).

        Its OWN endpoint, and never ``append_to_response`` on ``/movie/{id}``:
        ``providers/cache.build_cache_key`` hashes ``[method, url, params]``,
        so widening the movie request's query string would change that key and
        orphan every cached movie-facts entry in the table at once -- the same
        failure ``providers/tmdb.py``'s ``fetch`` docstring already records for
        the artwork client. A distinct URL is a distinct key and leaves the
        existing entries alone. It also costs one request only for movies and
        only when a source is named, where ``append_to_response`` would widen
        the payload for every movie and every show whether or not the feature
        is on.

        Through ``_get`` rather than a hand-rolled ``httpx`` call, so this read
        inherits the bearer header, the shared rate budget, the 429 backoff --
        and that handler's class-name-only logging, which matters more here
        than anywhere else: the URL now carries a TMDb id (row 213).
        """
        payload = await self._get(f"/movie/{tmdb_id}/release_dates")
        return parse_release_date(payload, TMDB_RELEASE_TYPES[source]) if payload else None

    async def show(self, tmdb_id: int) -> GatheredFacts:
        payload = await self._get(f"/tv/{tmdb_id}")
        return parse_show_facts(payload) if payload else GatheredFacts()

    async def season_episode_ratings(self, tmdb_id: int, season_number: int) -> dict[int, float]:
        """One request covers every episode of the season."""
        payload = await self._get(f"/tv/{tmdb_id}/season/{season_number}")
        return parse_season_episode_ratings(payload) if payload else {}

    async def collection_summary(self, tmdb_id: int) -> str | None:
        """A TMDB collection's overview, for a ``tmdb_summary:`` definition.

        The one thing the collections engine asks TMDB for (roadmap row 30).
        ``None`` for an id TMDB does not know (``fetch_json`` returns None on
        404) and for a collection whose overview is the empty string TMDB uses
        when nobody has written one -- both mean "no summary to borrow", and
        the caller's job is to leave the collection's own summary alone rather
        than blank it.
        """
        payload = await self._get(f"/collection/{tmdb_id}")
        if not payload:
            return None
        overview = payload.get("overview")
        return overview if isinstance(overview, str) and overview else None

    async def collection_order(self, collection_id: int) -> CollectionOrder | None:
        """A franchise collection's name and parts, in TMDb's order (row 268).

        The SAME request ``collection_summary`` makes, so the two share one
        ``provider_cache`` row per franchise per TTL. Through ``_get`` for the
        reasons ``release_date`` gives: the bearer header, the shared rate
        budget and the 429 backoff come with it. ``None`` for an id TMDb does
        not know -- a movie whose ``belongs_to_collection`` points at a
        collection that has since been deleted -- and for a response with no
        parts array; the caller writes nothing in either case.
        """
        payload = await self._get(f"/collection/{collection_id}")
        return parse_collection_order(payload) if payload else None
