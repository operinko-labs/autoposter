import asyncio
from datetime import date

import httpx

from autoposter.facts.models import GatheredFacts
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


class TVDBListRefused(Exception):
    """TVDb could not answer this list, or answered something unusable.

    Its own class, and one class for all three cases -- the list does not
    exist, the slug does not exist, the response carries no ``entities`` array
    -- because the engine's log line carries the exception class name and
    nothing else, and the message says which. It carries the list reference
    asked for; the API key lives in the login body and the token in a header,
    and neither enters a message.

    Deliberately *not* the empty list every artwork path returns for a 404: an
    empty membership means "remove every member" one layer down
    (``lists.reconcile_list_collection``), so a list that was deleted would
    empty a live collection on the next sync.
    """


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


def _first_company(companies, categories: tuple[str, ...]) -> str | None:
    """First named company off a movie's ``companies`` object, tried in
    ``categories`` order.

    A movie's ``companies`` is a dict of company-type buckets (``studio``,
    ``production``, ``distributor``, ``special_effects``, ``network``) --
    confirmed against the mass-ops-2 capture, which differs from a series'
    ``companies`` (a flat list; see ``parse_tvdb_facts``). TVDb's own
    ``studio`` bucket is what this field means, but the captured movie
    fixture carries its actual studios tagged ``production`` instead, so
    that bucket is tried second rather than left unread.
    """
    if not isinstance(companies, dict):
        return None
    for category in categories:
        for entry in companies.get(category) or []:
            if isinstance(entry, dict) and entry.get("name"):
                return entry["name"]
    return None


def _tvdb_date(value) -> date | None:
    """Parse a TVDb ``YYYY-MM-DD`` date string, or ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_tvdb_facts(payload: dict, is_movie: bool) -> GatheredFacts:
    """Genres, studio/network and release date off a TVDb extended record.

    The second parser over a payload ``fetch`` already retrieves for artwork
    (``/movies/{id}/extended``, ``/series/{id}/extended``) -- so this costs no
    request, no new endpoint and no change to the login/token/cache machinery.

    Every key read here is pinned by the fixtures captured in the mass-ops-2
    phase and by nothing else. An absent key reads as "TVDb has nothing for
    this field", never as an empty value: a missing value must not be written
    to Plex as a blank one.

    A movie's release date lives at ``first_release.date`` and its companies
    under the bucketed ``companies`` object (see ``_first_company``); a
    series' date is the flat top-level ``firstAired`` string and its network
    is ``latestNetwork`` (falling back to ``originalNetwork``), each a single
    company object rather than a list -- the two payload shapes genuinely
    differ and neither is guessed.
    """
    data = payload.get("data") or payload
    genres = [
        name for name in (
            entry.get("name") if isinstance(entry, dict) else entry
            for entry in (data.get("genres") or [])
        ) if isinstance(name, str) and name
    ]
    if is_movie:
        studio = _first_company(data.get("companies"), ("studio", "production"))
        released = _tvdb_date((data.get("first_release") or {}).get("date"))
    else:
        network = data.get("latestNetwork") or data.get("originalNetwork")
        studio = network.get("name") if isinstance(network, dict) else None
        released = _tvdb_date(data.get("firstAired"))
    sources: dict[str, str] = {}
    for key, value in (("genres", genres), ("studio", studio),
                       ("originally_available", released)):
        if value:
            sources[key] = "tvdb"
    return GatheredFacts(
        genres=genres, studio=studio, originally_available=released, sources=sources,
    )


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

    async def _list_id_for_slug(self, slug: str) -> int:
        """The list id behind a slug.

        ``/lists/slug/{slug}`` answers the list's *base* record, which has no
        ``entities`` in it, so a slug costs one extra request before the
        entities can be asked for. That is worth paying rather than refusing
        slugs: a TVDb list's URL carries the slug and not the id, so the slug
        is what an operator copying a list actually has.

        Coerced with ``int()`` rather than returned as whatever the JSON
        happened to carry -- the annotation says ``int`` and this is where
        that becomes true, instead of a caller finding out three requests
        later.
        """
        payload = await self._fetch_json(f"/lists/slug/{slug}")
        data = (payload or {}).get("data") or {}
        list_id = data.get("id")
        if list_id is None:
            raise TVDBListRefused(
                f"TVDb has no list with the slug {slug!r}. Building an empty "
                "collection instead would remove every member it has."
            )
        return int(list_id)

    async def list_entities(
        self, *, list_id: int | None = None, slug: str | None = None
    ) -> list[dict]:
        """A TVDb list's entries, in the order the list gives them.

        Each entry is ``{"order": n, "seriesId": …, "movieId": …}`` -- one or
        the other -- so a list is inherently mixed and this returns the entries
        as they are; deciding which kind this library meant belongs to the
        builder, which is the only layer that knows.

        The array is taken in the order it arrives rather than re-sorted on the
        ``order`` field: two sources of truth for one ordering is one more than
        there should be, and order is the source's throughout the collections
        engine.

        Goes through ``_fetch_json`` like every other read here, so it inherits
        the cached token, the one-shot re-login on a 401, and the cache -- the
        login itself still never goes through any of it.

        Both ``list_id`` and ``slug`` being ``None`` is unreachable through the
        builder (``TvdbListParams`` enforces exactly one), but this is a public
        client method and building ``/lists/slug/None`` for a caller that
        passed neither would read as a confusing 404 rather than saying what
        actually went wrong.
        """
        if list_id is None and slug is None:
            raise TVDBListRefused(
                "list_entities() needs a list id or slug, and got neither"
            )
        if list_id is None:
            list_id = await self._list_id_for_slug(slug)
        payload = await self._fetch_json(f"/lists/{list_id}/extended")
        if payload is None:
            raise TVDBListRefused(
                f"TVDb has no list {list_id}. Building an empty collection instead "
                "would remove every member it has."
            )
        entities = (payload.get("data") or payload).get("entities")
        if not isinstance(entities, list):
            raise TVDBListRefused(
                f"TVDb list {list_id}: the response carries no 'entities' array. "
                "Reading it as an empty list would remove every member the "
                "collection has."
            )
        return entities

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

    async def extended_facts(self, tvdb_id: int, is_movie: bool) -> GatheredFacts | None:
        """This title's metadata off the extended record, or ``None``.

        Goes through ``_fetch_json`` like every other read here, so it shares
        the cached token, the one-shot re-login on a 401, and the provider
        cache -- an item whose artwork was fetched this pass pays nothing for
        its facts.
        """
        path = f"/movies/{tvdb_id}/extended" if is_movie else f"/series/{tvdb_id}/extended"
        payload = await self._fetch_json(path)
        if payload is None:
            return None
        return parse_tvdb_facts(payload, is_movie)

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
