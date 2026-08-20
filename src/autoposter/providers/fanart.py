import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, ArtCandidate, ArtRequest,
)
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

BASE_URL = "https://webservice.fanart.tv/v3.2"

# Response keys per artwork type. Fanart has no episode artwork at all.
# Artwork kinds Fanart can serve at all. Episode artwork is absent entirely.
_SUPPORTED_KINDS = {POSTER, BACKGROUND, SEASON_POSTER, LOGO}

_KEYS = {
    (POSTER, True): ["movieposter"],
    (POSTER, False): ["tvposter"],
    (BACKGROUND, True): ["moviebackground"],
    (BACKGROUND, False): ["showbackground"],
    (SEASON_POSTER, False): ["seasonposter"],
    (LOGO, True): ["hdmovielogo", "movielogo"],
    (LOGO, False): ["hdtvlogo", "clearlogo"],
}


def parse_fanart(
    payload: dict, art_kind: str, is_movie: bool, season_number: int | None
) -> list[ArtCandidate]:
    """Convert a Fanart.tv response into candidates.

    ``lang`` is ``"00"`` when a designer explicitly tagged the image textless and
    ``""`` when it was simply never tagged; both count as no-language. Every value
    in a Fanart response is a JSON string, including the numbers.
    """
    candidates = []
    for key in _KEYS.get((art_kind, is_movie), []):
        for entry in payload.get(key) or []:
            url = entry.get("url")
            if not url:
                continue
            if art_kind == SEASON_POSTER and season_number is not None:
                if str(entry.get("season")) != str(season_number):
                    continue
            candidates.append(
                ArtCandidate(
                    provider="Fanart",
                    url=url.replace("http://", "https://", 1),
                    language=entry.get("lang"),
                    width=_int_or_none(entry.get("width")),
                    height=_int_or_none(entry.get("height")),
                    score=_float_or_zero(entry.get("likes")),
                )
            )
    return candidates


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: object) -> float:
    try:
        return float(value or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class FanartClient:
    """Reads artwork from Fanart.tv. Authentication is a query parameter."""

    name = "Fanart"

    def __init__(
        self,
        apikey: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._apikey = apikey
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.art_kind not in _SUPPORTED_KINDS:
            return []
        external_id = request.tmdb_id if request.is_movie else request.tvdb_id
        if external_id is None:
            return []
        segment = "movies" if request.is_movie else "tv"
        url = f"{BASE_URL}/{segment}/{external_id}"
        params = {"api_key": self._apikey}
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(url, params=params),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            return []
        return parse_fanart(payload, request.art_kind, request.is_movie, request.season_number)
