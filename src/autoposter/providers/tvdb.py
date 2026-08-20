import asyncio

import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtCandidate, ArtRequest,
)
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

BASE_URL = "https://api4.thetvdb.com/v4"

# Artwork type ids from /artwork/types. Refresh that endpoint weekly rather than
# trusting these numbers indefinitely.
_TYPE_IDS = {
    (POSTER, False): {2},
    (POSTER, True): {14},
    (BACKGROUND, False): {3},
    (BACKGROUND, True): {15},
    (SEASON_POSTER, False): {7},
    (TITLE_CARD, False): {11, 12},
    (LOGO, False): {23},
    (LOGO, True): {25},
}


def parse_tvdb_artworks(payload: dict, art_kind: str, is_movie: bool) -> list[ArtCandidate]:
    """Convert a TVDB extended/artworks response into candidates.

    TVDB is the only provider with an explicit ``includesText`` boolean, so it is
    carried through rather than inferred from the language.
    """
    wanted = _TYPE_IDS.get((art_kind, is_movie), set())
    data = payload.get("data") or payload
    artworks = data.get("artworks") or data.get("artwork") or []
    candidates = []
    for entry in artworks:
        if entry.get("type") not in wanted:
            continue
        url = entry.get("image")
        if not url:
            continue
        candidates.append(
            ArtCandidate(
                provider="TVDB",
                url=url,
                language=entry.get("language"),
                width=entry.get("width"),
                height=entry.get("height"),
                score=float(entry.get("score") or 0),
                includes_text=entry.get("includesText"),
            )
        )
    return candidates


def _find_season_id(payload: dict, season_number: int) -> int | None:
    """Pick the id of the season matching ``season_number`` from a series' extended record.

    TVDB lists a season number once per "type" (official, alternate, dvd, ...);
    prefer the official entry when the type is present, and otherwise accept the
    first match so fixtures that omit type info still resolve.

    Confirmed against the live API: a season entry's ``type`` is a nested
    object, e.g.
    ``{"id": 1, "name": "Aired Order", "type": "official", "alternateName": null}``,
    matching the ``SeasonType`` schema in TVDB's own swagger. The inner
    ``type`` string is the discriminator read here.
    """
    data = payload.get("data") or payload
    for season in data.get("seasons") or []:
        if season.get("number") != season_number:
            continue
        season_type = (season.get("type") or {}).get("type")
        if season_type is not None and season_type != "official":
            continue
        return season.get("id")
    return None


class TVDBClient:
    """Reads artwork from TVDB v4. Login tokens are valid for one month."""

    name = "TVDB"

    def __init__(
        self,
        apikey: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._apikey = apikey
        self._client = client
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def _login(self) -> str:
        """Log in and cache the token. Concurrent cold-start callers share one call.

        The token is not re-checked once acquired here: this is only called by
        ``_authenticate`` while holding ``_login_lock``, after the double-checked
        ``self._token is None`` test, and by ``_reauthenticate`` which always
        wants a fresh token.
        """
        response = await self._client.post(f"{BASE_URL}/login", json={"apikey": self._apikey})
        response.raise_for_status()
        self._token = response.json()["data"]["token"]
        return self._token

    async def _authenticate(self) -> str:
        if self._token:
            return self._token
        async with self._login_lock:
            if self._token is None:
                await self._login()
            return self._token

    async def _reauthenticate(self) -> str:
        """Force a fresh token after a 401. TVDB tokens last a month, so this is rare."""
        async with self._login_lock:
            return await self._login()

    async def _authenticated_get(self, path: str) -> httpx.Response:
        """GET with the cached token, retrying once with a fresh token on a 401."""
        token = await self._authenticate()
        response = await self._client.get(
            f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 401:
            token = await self._reauthenticate()
            response = await self._client.get(
                f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}
            )
        return response

    async def _fetch_json(self, path: str) -> dict | None:
        """Authenticated GET for everything except ``/login``, optionally cached.

        ``/login`` never goes through here — it is a credential with its own
        lifetime and its own refresh path (the 401 retry above, under
        ``_login_lock``), and caching it would defeat that.
        """
        return await fetch_json(
            method="GET",
            url=f"{BASE_URL}{path}",
            params=None,
            request=lambda: self._authenticated_get(path),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )

    async def _resolve_season_id(self, tvdb_id: int, season_number: int) -> int | None:
        """Look up the TVDB season id for a season number.

        ``ArtRequest.season_id`` is never populated by callers, so a season
        poster request only ever carries ``tvdb_id`` and ``season_number``; the
        client must resolve the season id itself via the series' extended record.
        """
        payload = await self._fetch_json(f"/series/{tvdb_id}/extended")
        if payload is None:
            return None
        return _find_season_id(payload, season_number)

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.tvdb_id is None:
            return []
        if request.art_kind == TITLE_CARD:
            # An episode record carries a single `image` field, not an artworks
            # array, so TVDB cannot serve title cards through this path.
            return []
        if request.is_movie:
            path = f"/movies/{request.tvdb_id}/extended"
        elif request.art_kind == SEASON_POSTER:
            season_id = request.season_id
            if season_id is None:
                if request.season_number is None:
                    return []
                season_id = await self._resolve_season_id(request.tvdb_id, request.season_number)
                if season_id is None:
                    return []
            path = f"/seasons/{season_id}/extended"
        else:
            # Fetched without a lang filter on purpose: passing lang= would drop the
            # null-language entries, which are the best textless candidates.
            path = f"/series/{request.tvdb_id}/artworks"
        payload = await self._fetch_json(path)
        if payload is None:
            return []
        return parse_tvdb_artworks(payload, request.art_kind, request.is_movie)
