import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mdblist.com"

# MDBList answers 200 with an error body rather than a 429 when the daily
# budget is spent. The account in use has a 10,000/day allowance.
_LIMIT_ERRORS = {"API Limit Reached!", "API Rate Limit Reached!"}


class MDBListLimitReached(Exception):
    """The daily request budget is exhausted; stop asking until tomorrow."""


def parse_content_rating(payload: dict) -> str | None:
    """Common Sense age rating as a bare string, or ``None``.

    Gated on ``commonsense`` being truthy: ``age_rating`` alone can be
    populated from other sources, and using it ungated would write values the
    tool being replaced never would. The ``+`` suffix is a rendering concern.
    """
    if not payload.get("commonsense"):
        return None
    age = payload.get("age_rating")
    if age is None or age == "":
        return None
    return str(age)


class MDBListClient:
    """Reads Common Sense age ratings.

    Movies are addressed by TMDB id and shows by TVDB id — the asymmetry is
    MDBList's, not ours.
    """

    name = "MDBList"

    def __init__(self, apikey: str, client: httpx.AsyncClient):
        self._apikey = apikey
        self._client = client

    async def content_rating(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> str | None:
        identifier = tmdb_id if is_movie else tvdb_id
        if identifier is None:
            return None
        provider = "tmdb" if is_movie else "tvdb"
        media_type = "movie" if is_movie else "show"
        response = await self._client.get(
            f"{BASE_URL}/{provider}/{media_type}/{identifier}/",
            params={"apikey": self._apikey},
            headers={"User-Agent": "autoposter"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if error in _LIMIT_ERRORS:
            raise MDBListLimitReached(error)
        if error:
            logger.warning("mdblist error for %s %s: %s", provider, identifier, error)
            return None
        return parse_content_rating(payload)
