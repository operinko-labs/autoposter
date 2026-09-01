import logging
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
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", aired),
                       ("tmdb_origin_country", countries),
                       ("tmdb_original_language", language)):
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
        sources=sources,
    )


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

    Every request goes through the Phase 1 cache seam. That matters most for
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
