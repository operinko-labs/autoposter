import logging

import httpx

from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mdblist.com"

# MDBList answers 200 with an error body rather than a 429 when the daily
# budget is spent. The account in use has a 10,000/day allowance.
_LIMIT_ERRORS = {"API Limit Reached!", "API Rate Limit Reached!"}


class MDBListLimitReached(Exception):
    """The daily request budget is exhausted; stop asking until tomorrow."""


class MDBListRefused(Exception):
    """MDBList could not answer this list, or answered something unusable.

    One class for the three cases -- the list does not exist, MDBList replied
    with an error body that is not the budget, the response is in no shape this
    knows how to read -- because the engine logs the class name either way and
    the message says which. The message carries the list reference and
    MDBList's own error text; the API key travels in a query parameter and
    never enters one.

    Separate from ``MDBListLimitReached`` on purpose: only the budget is a
    reason to stop asking MDBList anything else this pass (see
    ``collections.builders.mdblist``). A list that 404s is one dead definition.
    """


# The two shapes MDBList has served list items in: an object keyed by media
# type, and one flat array with ``mediatype`` on each entry. The entries
# themselves are identical, so the key is only ever used as a fallback for an
# entry that does not carry its own.
_MEDIATYPE_KEYS = {"movies": "movie", "shows": "show"}


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


def parse_list_items(payload: object, subject: str) -> list[tuple[str, dict]]:
    """MDBList's list items as ``(mediatype, entry)`` pairs, in list order.

    Both shapes MDBList has served are read (``_MEDIATYPE_KEYS``), because they
    differ only in how the entries are *grouped* -- the entries are the same
    objects and each one carries its own ``mediatype``. So the container is
    used to iterate and the entry is used to classify, and the grouping key is
    only a fallback for an entry that has no ``mediatype`` of its own.

    Anything else raises. That is the whole point of parsing this strictly: a
    third shape read leniently would produce no entries, an empty membership
    means "remove every member" one layer down
    (``lists.reconcile_list_collection``), and a collection would empty itself
    with nothing anywhere to say why. An entry with no media type at all raises
    for the same reason -- if the field has gone away, *every* entry lacks it.
    A list that really is empty is data, not a failure, exactly as an empty
    page is in ``providers/tmdb_lists.py``.
    """
    if isinstance(payload, list):
        groups: list[tuple[str | None, list]] = [(None, payload)]
    elif isinstance(payload, dict):
        groups = []
        for key, mediatype in _MEDIATYPE_KEYS.items():
            entries = payload.get(key)
            if entries is None:
                continue
            if not isinstance(entries, list):
                raise MDBListRefused(f"{subject}: MDBList's {key!r} is not an array")
            groups.append((mediatype, entries))
        if not groups:
            raise MDBListRefused(
                f"{subject}: MDBList's response is an object with neither 'movies' "
                "nor 'shows' in it. Reading it as an empty list would remove every "
                "member the collection has."
            )
    else:
        raise MDBListRefused(
            f"{subject}: MDBList's response is neither an array of items nor an "
            "object keyed by media type"
        )

    items: list[tuple[str, dict]] = []
    for grouped_as, entries in groups:
        for entry in entries:
            if not isinstance(entry, dict):
                raise MDBListRefused(f"{subject}: an entry in MDBList's response is not an object")
            mediatype = entry.get("mediatype") or grouped_as
            if not mediatype:
                raise MDBListRefused(
                    f"{subject}: an entry in MDBList's response carries no "
                    "'mediatype', so there is no way to tell which id namespace "
                    "it belongs in"
                )
            items.append((str(mediatype), entry))
    return items


def _raise_for_error(payload: object, subject: str) -> None:
    """Turn MDBList's 200-with-an-error-body into the right exception.

    MDBList answers a spent daily budget with ``200`` and ``{"error": "API
    Limit Reached!"}`` rather than a 429, so nothing in the HTTP layer notices
    and every caller has to look. ``content_rating`` above looks for the same
    thing and degrades; a list cannot degrade, so both kinds of error raise
    here.
    """
    error = payload.get("error") if isinstance(payload, dict) else None
    if not error:
        return
    if error in _LIMIT_ERRORS:
        raise MDBListLimitReached(error)
    raise MDBListRefused(f"{subject}: MDBList answered {error!r}")


class NullMDBListClient:
    """Stand-in used when no MDBList API key is configured.

    Degrades only the ``content_rating`` field MDBList would have supplied;
    ``gather_facts`` already treats a ``None`` content rating as "no value",
    so every other metadata operation proceeds untouched (see finding 1).
    """

    name = "MDBList"

    async def content_rating(
        self,
        tmdb_id: int | None = None,
        tvdb_id: int | None = None,
        is_movie: bool = True,
    ) -> str | None:
        return None


class MDBListClient:
    """Reads Common Sense age ratings.

    Movies are addressed by TMDB id and shows by TVDB id — the asymmetry is
    MDBList's, not ours. MDBList is slow-moving data (see the Global
    Constraints), so every request goes through the Phase 1 cache seam, same
    as ``TMDBFactsClient``. ``apikey`` is sent as a query parameter, but
    ``build_cache_key`` already strips it from the cache key, so it is never
    persisted in ``provider_cache``.
    """

    name = "MDBList"

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
        url = f"{BASE_URL}/{provider}/{media_type}/{identifier}/"
        params = {"apikey": self._apikey}
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(
                url, params=params, headers={"User-Agent": "autoposter"}
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            return None
        error = payload.get("error") if isinstance(payload, dict) else None
        if error in _LIMIT_ERRORS:
            raise MDBListLimitReached(error)
        if error:
            logger.warning("mdblist error for %s %s: %s", provider, identifier, error)
            return None
        return parse_content_rating(payload)

    async def list_items(
        self, reference: str, *, sort: str | None = None, order: str | None = None
    ) -> list[tuple[str, dict]]:
        """One list's members as ``(mediatype, entry)`` pairs, in MDBList's order.

        ``reference`` is either ``"<user>/<slug>"`` or a numeric list id, and
        that is the *entire* difference between MDBList's two list endpoints:
        ``/lists/{user}/{slug}/items`` and ``/lists/{id}/items`` are one path
        with a reference of one segment or two. Telling the two apart is the
        builder's params model, which is where an operator's typo should be
        caught; here it is a path segment either way. Both forms are restricted
        to unreserved characters by that model, so neither needs escaping.

        ``sort``/``order`` are omitted rather than sent empty when unset, so a
        definition that does not ask for an order keeps the request -- and
        therefore the cache key -- it has always had.

        A 404 raises rather than returning nothing: an empty membership means
        "remove every member" one layer down, so a list that was deleted or
        made private would empty a live collection on the next sync.

        One consequence of going through ``fetch_json`` worth knowing: the
        200-with-an-error-body that means "budget spent" is a successful
        response as far as the cache is concerned, so it is stored like any
        other and re-raised from the cache until it expires. Within a pass that
        is redundant with the builder's memo; across passes it means MDBList is
        not asked again on this list until the entry ages out -- no further
        budget is spent, at the cost of recovering late once the allowance
        rolls over.
        """
        url = f"{BASE_URL}/lists/{reference}/items"
        params: dict[str, object] = {"apikey": self._apikey}
        if sort:
            params["sort"] = sort
        if order:
            params["order"] = order
        subject = f"MDBList list {reference!r}"
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(
                url, params=params, headers={"User-Agent": "autoposter"}
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            raise MDBListRefused(
                f"{subject} does not exist: MDBList answered 404. Building an empty "
                "collection instead would remove every member it has."
            )
        _raise_for_error(payload, subject)
        return parse_list_items(payload, subject)
