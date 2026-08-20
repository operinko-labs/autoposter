import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtCandidate, ArtRequest,
)
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/original"

# Which response array holds each artwork type.
_ARRAY_FOR_KIND = {
    POSTER: "posters",
    SEASON_POSTER: "posters",
    BACKGROUND: "backdrops",
    LOGO: "logos",
    TITLE_CARD: "stills",
}


def parse_tmdb_images(payload: dict, art_kind: str) -> list[ArtCandidate]:
    """Convert a TMDB /images response into candidates.

    ``iso_639_1`` is ``null`` for untagged images; that is TMDB's only proxy for
    textlessness — it has no explicit flag.
    """
    entries = payload.get(_ARRAY_FOR_KIND[art_kind]) or []
    candidates = []
    for entry in entries:
        file_path = entry.get("file_path")
        if not file_path:
            continue
        candidates.append(
            ArtCandidate(
                provider="TMDB",
                url=f"{IMAGE_BASE}{file_path}",
                language=entry.get("iso_639_1"),
                width=entry.get("width"),
                height=entry.get("height"),
                score=_tmdb_score(entry),
            )
        )
    return candidates


def _tmdb_score(entry: dict) -> float:
    """Shrink the average towards the mean so a 10.0 from one vote cannot win.

    Bayesian shrinkage with C=3 prior votes at m=5.0.
    """
    votes = entry.get("vote_count") or 0
    average = entry.get("vote_average") or 0.0
    prior_votes, prior_mean = 3, 5.0
    return (votes * average + prior_votes * prior_mean) / (votes + prior_votes)


class TMDBClient:
    """Reads artwork from TMDB. The configured token is a v4 read access token."""

    name = "TMDB"

    def __init__(
        self,
        token: str,
        language_order: list[str],
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._token = token
        self._language_order = language_order
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    def _params(self) -> dict:
        # "null" is the literal token TMDB uses for images with no language tag.
        codes = ["null" if code == "xx" else code for code in self._language_order]
        return {"include_image_language": ",".join(codes)}

    def _path(self, request: ArtRequest) -> str:
        if request.is_movie:
            return f"/movie/{request.tmdb_id}/images"
        if request.art_kind == TITLE_CARD:
            return (
                f"/tv/{request.tmdb_id}/season/{request.season_number}"
                f"/episode/{request.episode_number}/images"
            )
        if request.art_kind == SEASON_POSTER:
            return f"/tv/{request.tmdb_id}/season/{request.season_number}/images"
        return f"/tv/{request.tmdb_id}/images"

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.tmdb_id is None:
            return []
        if request.art_kind == TITLE_CARD and (
            request.season_number is None or request.episode_number is None
        ):
            return []
        if request.art_kind == SEASON_POSTER and request.season_number is None:
            return []
        path = self._path(request)
        url = f"{BASE_URL}{path}"
        params = self._params()
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self._token}", "accept": "application/json"},
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            return []
        return parse_tmdb_images(payload, request.art_kind)
