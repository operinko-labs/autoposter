from datetime import date, datetime

import httpx

from autoposter.facts.models import GatheredFacts
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

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


def parse_movie_facts(payload: dict) -> GatheredFacts:
    rating = _rating(payload)
    studio = _first_name(payload.get("production_companies"))
    genres = _genres(payload)
    released = _as_date(payload.get("release_date"))
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", released)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=released,
        sources=sources,
    )


def parse_show_facts(payload: dict) -> GatheredFacts:
    """Shows take their studio from ``networks``, not ``production_companies``."""
    rating = _rating(payload)
    studio = _first_name(payload.get("networks"))
    genres = _genres(payload)
    aired = _as_date(payload.get("first_air_date"))
    sources = {}
    if rating is not None:
        sources["audience_rating"] = "tmdb"
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", aired)):
        if value:
            sources[key] = "tmdb"
    return GatheredFacts(
        audience_rating=rating,
        genres=genres,
        studio=studio,
        originally_available=aired,
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
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "accept": "application/json"}

    async def _get(self, path: str) -> dict | None:
        url = f"{BASE_URL}{path}"
        return await fetch_json(
            method="GET",
            url=url,
            params=None,
            request=lambda: self._client.get(url, headers=self._headers()),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )

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
