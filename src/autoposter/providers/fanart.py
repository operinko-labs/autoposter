import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, ArtCandidate, ArtRequest,
)

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

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.art_kind not in _SUPPORTED_KINDS:
            return []
        external_id = request.tmdb_id if request.is_movie else request.tvdb_id
        if external_id is None:
            return []
        segment = "movies" if request.is_movie else "tv"
        response = await self._client.get(
            f"{BASE_URL}/{segment}/{external_id}", params={"api_key": self._apikey}
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return parse_fanart(
            response.json(), request.art_kind, request.is_movie, request.season_number
        )
