import httpx

from autoposter.providers.base import (
    BACKGROUND, LOGO, POSTER, SEASON_POSTER, TITLE_CARD, ArtCandidate, ArtRequest,
)

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


class TVDBClient:
    """Reads artwork from TVDB v4. Login tokens are valid for one month."""

    name = "TVDB"

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client
        self._token: str | None = None

    async def _authenticate(self) -> str:
        if self._token:
            return self._token
        response = await self._client.post(f"{BASE_URL}/login", json={"apikey": self._apikey})
        response.raise_for_status()
        self._token = response.json()["data"]["token"]
        return self._token

    async def fetch(self, request: ArtRequest) -> list[ArtCandidate]:
        if request.tvdb_id is None:
            return []
        if request.art_kind == TITLE_CARD:
            # An episode record carries a single `image` field, not an artworks
            # array, so TVDB cannot serve title cards through this path.
            return []
        token = await self._authenticate()
        headers = {"Authorization": f"Bearer {token}"}
        if request.is_movie:
            path = f"/movies/{request.tvdb_id}/extended"
        elif request.art_kind == SEASON_POSTER:
            if request.season_id is None:
                return []
            path = f"/seasons/{request.season_id}/extended"
        else:
            # Fetched without a lang filter on purpose: passing lang= would drop the
            # null-language entries, which are the best textless candidates.
            path = f"/series/{request.tvdb_id}/artworks"
        response = await self._client.get(f"{BASE_URL}{path}", headers=headers)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return parse_tvdb_artworks(response.json(), request.art_kind, request.is_movie)
