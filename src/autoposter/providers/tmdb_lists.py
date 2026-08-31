"""TMDb's *list* surface: the endpoints a collection's membership comes from.

Separate from ``providers/tmdb.py`` (artwork) and ``facts/tmdb_facts.py``
(ratings and descriptive metadata) for the reason those two are separate from
each other: they ask different endpoints for different reasons and share only
the bearer token. What is genuinely new here is paging -- nothing else in this
repository reads more than one page of a TMDb response -- and the rule that a
404 is an error rather than an empty answer.

**Why 404 raises here and returns ``None`` everywhere else.** ``fetch_json``
turns a 404 into ``None``, which every artwork and facts client reads as "TMDb
has nothing for this title" and degrades gracefully. A *collection* cannot:
an empty membership means "remove every member" one layer down
(``lists.reconcile_list_collection``), so a list id that has been deleted or
made private would empty a live collection on the next sync. Every method here
therefore raises ``TmdbListRefused`` naming what was asked for, and the engine
contains that as one dead source.

**Paging.** Two response shapes, and the client honours whichever one the
endpoint uses to say how much there is:

- the paged shape (``/movie/popular``, ``/discover/*``) counts pages in
  ``total_pages`` and holds its entries in ``results``;
- the v3 list-details shape (``/list/{id}``) holds its entries in ``items``
  and counts *members* in ``item_count``.

Reading both stop conditions costs four lines and buys the client an answer
that stays correct if TMDb ever ignores ``page`` on the list endpoint: the
loop would otherwise collect the same first page ``max_pages`` times over.
Both are bounded by ``max_pages`` regardless, because a runaway upstream must
cost a bounded number of requests -- a definition's ``limit`` trims after
resolution and cannot stop a fetch that has already happened.

**A mixed list is filtered to one media type.** ``/list/{id}`` is the only
endpoint here that answers with movies and shows at once, and TMDb's movie
and show ids are different id spaces sharing one *namespace* -- so a show's
id handed to a Movie library can resolve to an unrelated film rather than to
nothing. ``_of_media_type`` drops the other kind before ids are taken; see
its docstring. The ``item_count`` stop condition counts entries *seen* rather
than ids kept precisely so that filtering does not disarm it (roadmap row 144).

**Companies, networks and keywords go through ``/discover``**, not through
``/company/{id}/movies`` or ``/keyword/{id}/movies``: TMDb marks those
deprecated in favour of the discover filters, and there is no listing endpoint
for a network at *all*, so discover is the only form that covers all three.
One shape, one paging rule, and the filter name is the only thing that varies.
"""
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

BASE_URL = "https://api.themoviedb.org/3"

# How many pages one call will ever fetch. See the module docstring.
MAX_PAGES = 10

# Chart key -> the endpoint that answers it for each library type.
#
# A chart key is the operator's word ("popular") and the media type is the
# library the pass is running against, so one definition can serve a Movie and
# a Show library. A key with no entry for a library type is a chart that
# library type cannot build -- theatrical release windows have no TV form and
# airing schedules have no movie form -- and the builders' mismatch guard
# reads exactly this table to say so.
CHART_ENDPOINTS: dict[str, dict[str, str]] = {
    "popular": {"Movie": "/movie/popular", "Show": "/tv/popular"},
    "top_rated": {"Movie": "/movie/top_rated", "Show": "/tv/top_rated"},
    "now_playing": {"Movie": "/movie/now_playing"},
    "upcoming": {"Movie": "/movie/upcoming"},
    "airing_today": {"Show": "/tv/airing_today"},
    "on_the_air": {"Show": "/tv/on_the_air"},
    "trending_day": {"Movie": "/trending/movie/day", "Show": "/trending/tv/day"},
    "trending_week": {"Movie": "/trending/movie/week", "Show": "/trending/tv/week"},
}

# The endpoints in ``CHART_ENDPOINTS`` where TMDb actually applies `region` to
# filter membership. Every other endpoint -- the four `/tv/*` charts and all
# `/trending/*` variants -- accepts the parameter over HTTP without complaint
# and silently drops it, which is exactly the "operator asked for a regional
# chart and got a global one with nothing to notice" hazard the builder's
# region validator exists to guard against. See
# ``collections.builders.tmdb.TmdbChartBuilder`` for the build-time refusal
# that reads this set.
CHART_ENDPOINTS_ACCEPTING_REGION: frozenset[str] = frozenset(
    {"/movie/popular", "/movie/top_rated", "/movie/now_playing", "/movie/upcoming"}
)

__all__ = [
    "CHART_ENDPOINTS",
    "CHART_ENDPOINTS_ACCEPTING_REGION",
    "MAX_PAGES",
    "PersonCredit",
    "PersonDetail",
    "TmdbListClient",
    "TmdbListRefused",
]


@dataclass(frozen=True)
class PersonCredit:
    """One entry of ``/person/{id}/{movie,tv}_credits``, reduced to four fields.

    The one method here that does not hand back bare ids, and the reason is
    that its endpoint answers a question this client cannot finish: it returns
    *every* credit a person holds, and which of them a collection means is the
    builder's role table, not the transport's. So the shape is
    ``collection_parts``' -- one request, no pager, the array validated here --
    with a richer element.

    Four fields and no more, deliberately. ``kind`` is ``"cast"`` or
    ``"crew"``, which is the array the credit came out of; ``job`` and
    ``department`` are TMDb's own strings (``"Director"``, ``"Writing"``) and
    are ``None`` on cast credits, which carry a ``character`` instead. Nothing
    else crosses: the payload also holds profile paths, characters, episode
    counts and popularity, and none of them belongs here. Summaries and
    posters ship through ``person_detail`` instead -- the credit record stays
    lean *because* the person's own record already carries them. Appearance
    thresholds are the remaining reason: still unbuilt, filed on roadmap row
    194. Returning the raw payload would put profile paths one attribute
    access away from a builder that must not grow them.
    """

    tmdb_id: str
    kind: str
    job: str | None
    department: str | None


@dataclass(frozen=True)
class PersonDetail:
    """``/person/{id}``, reduced to the two fields a collection can use.

    ``PersonCredit``'s discipline, one endpoint along: the payload also carries
    ``birthday``, ``deathday``, ``place_of_birth``, ``also_known_as``,
    ``popularity`` and ``known_for_department``, and none of them crosses.
    Birthday/deathday gating is filed (roadmap row 160), and a field nobody
    reads is a field a builder can start reading without the decision being
    made.

    ``name`` is deliberately NOT here even though the client checks it: it is
    the response-shape probe (see ``person_detail``), not something any caller
    needs, and carrying it would make an unused field look like a considered
    one.

    Both fields are ``None`` for absence, and ``person_detail`` normalises the
    two shapes TMDb uses -- ``""`` for a biography it has none of in the
    requested language, ``null`` for a missing photo -- onto that one.
    """

    biography: str | None
    profile_path: str | None


class TmdbListRefused(Exception):
    """TMDb could not answer this, or answered something unusable.

    One class for the three cases -- the id does not exist, the response has
    no entry array, an entry has no id -- because the engine logs the class
    name either way and the message says which. It carries the path and the
    id asked for and nothing else; the token lives in a header and never
    enters a message.
    """


class TmdbListClient:
    """Reads collection membership from TMDb. The token is a v4 read access
    token, exactly as ``providers/tmdb.py``'s is."""

    name = "TMDB"

    def __init__(
        self,
        token: str,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
        max_pages: int = MAX_PAGES,
    ):
        self._token = token
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_pages = max_pages

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}", "accept": "application/json"}

    async def _get(self, path: str, params: dict, subject: str) -> dict:
        """One request, through the cache, with 404 turned back into an error.

        The cache key is built from method, URL and params only
        (``build_cache_key``), so the bearer token in the header is never
        persisted and rotating it does not orphan a single cached page.
        """
        url = f"{BASE_URL}{path}"
        payload = await fetch_json(
            method="GET",
            url=url,
            params=params,
            request=lambda: self._client.get(url, params=params, headers=self._headers()),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        if payload is None:
            raise TmdbListRefused(
                f"{subject} does not exist: TMDb answered 404 for {path}. Building "
                "an empty collection instead would remove every member it has."
            )
        return payload

    async def _paged(
        self,
        path: str,
        params: dict,
        *,
        subject: str,
        items_key: str,
        media_type: str | None = None,
    ) -> list[str]:
        """Every page of ``path``, in order, bounded by ``max_pages``.

        ``media_type`` keeps only the entries of one kind -- see
        ``_of_media_type``. ``None``, the default, keeps everything, which is
        the right answer for every endpoint that already answers for exactly
        one media type.
        """
        ids: list[str] = []
        seen = 0
        for page in range(1, self._max_pages + 1):
            payload = await self._get(path, {**params, "page": page}, subject)
            entries = payload.get(items_key)
            if not isinstance(entries, list):
                raise TmdbListRefused(
                    f"{subject}: TMDb's response carries no {items_key!r} array"
                )
            if not entries:
                # The page past the end. A source that really is empty is data,
                # not a failure -- unlike a 404, which is the id being wrong.
                break
            seen += len(entries)
            ids += _ids(_of_media_type(entries, media_type, subject), subject)
            total_pages = payload.get("total_pages")
            if isinstance(total_pages, int) and page >= total_pages:
                break
            item_count = payload.get("item_count")
            # ``item_count`` counts a list's *members*, both media types
            # together, so it is compared against entries SEEN rather than ids
            # kept: kept-vs-listed could never be satisfied once ``media_type``
            # filtered anything out (roadmap row 144), which killed this break
            # for every filtered read -- and ``tmdb_list`` always filters --
            # degrading the pathological "TMDb ignored ``page``" case it
            # guards into repeated identical requests with the survivors
            # duplicated per page. A short upstream (deleted members) still
            # ends on the empty page; this break only ever fires early.
            if isinstance(item_count, int) and seen >= item_count:
                break
        else:
            # Every page spent and no stop condition met, which is the one exit
            # from this loop that hands back a *truncated* answer. It used to be
            # silent, and a silently short collection looks exactly like a
            # correct one -- `imdb_graphql._fetch` says so out loud at its own cap
            # and this is the same read. Generic discover is what forced it:
            # `vote_average.gte: 5` matches tens of thousands of titles, so the
            # cap is reachable by an ordinary definition rather than only by a
            # runaway upstream. It stays a warning rather than a raise because
            # the ids collected are real members in TMDb's order -- the same
            # judgement `imdb_graphql` made.
            logger.warning(
                "%s: stopped at the %d-page cap with %d id(s); there may be more that "
                "this pass will not see",
                subject, self._max_pages, len(ids),
            )
        return ids

    async def chart(
        self, path: str, *, region: str | None = None, language: str | None = None
    ) -> list[str]:
        """One chart, in rank order. ``path`` comes from ``CHART_ENDPOINTS``.

        ``region`` is the parameter that changes *membership*: TMDb resolves
        ``now_playing`` and ``upcoming`` against one country's release dates.
        Both are omitted entirely when unset rather than sent empty, so an
        unconfigured chart keeps the request -- and therefore the cache key --
        it has always had.
        """
        params: dict[str, object] = {}
        if region:
            params["region"] = region
        if language:
            params["language"] = language
        return await self._paged(
            path, params, subject=f"the TMDb chart at {path}", items_key="results"
        )

    async def list_items(self, list_id: int, *, media_type: str | None = None) -> list[str]:
        """A public v3 list's members, in list order.

        ``media_type`` is TMDb's own word (``"movie"``/``"tv"``) and keeps
        only the members of that kind. A v3 list is the one endpoint here
        that can hold both, and passing the other kind through would not
        merely fail to resolve -- see ``_of_media_type``.

        No ``language``: the only thing read out of the response is ids, and
        every extra parameter is a second cache key for an identical answer.
        ``media_type`` is applied to the response rather than sent, so it
        costs no extra cache key either: both library types read one cached
        copy of the list.
        """
        return await self._paged(
            f"/list/{list_id}",
            {},
            subject=f"TMDb list {list_id}",
            items_key="items",
            media_type=media_type,
        )

    async def collection_parts(self, collection_id: int) -> list[str]:
        """A franchise collection's parts, in TMDb's order.

        Not paged -- ``/collection/{id}`` returns every part in one response --
        so this is the one method that sends no ``page`` at all.
        """
        subject = f"TMDb collection {collection_id}"
        payload = await self._get(f"/collection/{collection_id}", {}, subject)
        parts = payload.get("parts")
        if not isinstance(parts, list):
            raise TmdbListRefused(f"{subject}: TMDb's response carries no 'parts' array")
        return _ids(parts, subject)

    async def collection_name(self, collection_id: int) -> str | None:
        """A franchise collection's own name, or None if TMDb does not know it.

        The SAME request ``collection_parts`` makes -- same path, same (empty)
        params, therefore the same ``build_cache_key`` and the same
        ``provider_cache`` row. So a franchise family that titles a collection
        here and builds its membership one layer down pays for ONE
        ``/collection/{id}`` read per franchise per TTL, not two.

        That is also why the name is not stored on ``item_facts``: it is a
        property of the collection rather than of the item, storing it per item
        would invite drift between rows, and the read it would save is a read
        the membership already pays for.

        ``None`` rather than a raise for an unknown id, unlike
        ``collection_parts``: a family drops a key it cannot name and reports
        it, where a single ``tmdb_collection`` definition naming a dead id is
        one collection that genuinely cannot be built. Only the 404 degrades --
        a transport error or a 500 still raises, because "TMDb was unreachable
        this pass" is not "this franchise has no name".
        """
        try:
            payload = await self._get(
                f"/collection/{collection_id}", {}, f"TMDb collection {collection_id}"
            )
        except TmdbListRefused:
            return None
        name = payload.get("name")
        return name if isinstance(name, str) and name else None

    async def person_credits(self, person_id: int, media_type: str) -> list[PersonCredit]:
        """Every credit one person holds on ``media_type``, in TMDb's order.

        ``media_type`` is TMDb's own word (``"movie"``/``"tv"``) and picks the
        endpoint -- ``/person/{id}/movie_credits`` or ``/tv_credits`` -- rather
        than filtering a response, because TMDb splits a person's filmography
        across two of them. That is what lets one definition serve a Movie and
        a Show library, and it is also why the credit filters on
        ``/discover/movie`` (``with_cast``, ``with_crew``, ``with_people``) are
        not the route taken: they are paged, and they have no ``/discover/tv``
        form at all.

        Not paged -- one response carries the whole filmography, as
        ``/collection/{id}`` carries a whole franchise -- so this sends no
        ``page`` and costs one cache entry per person per media type.

        **The order.** TMDb documents none for these arrays, and what it
        actually returns is its own internal credit order: broadly
        relevance/popularity-weighted, neither chronological nor alphabetical,
        and not promised to be stable between reads. It is preserved exactly
        as sent, cast entries then crew entries, because order becomes the
        collection's custom order one layer down and a sort invented here
        would be a decision TMDb did not make and the operator did not ask
        for. A definition that wants a different order says so with its own
        knobs.
        """
        subject = f"TMDb person {person_id}"
        payload = await self._get(f"/person/{person_id}/{media_type}_credits", {}, subject)
        credits: list[PersonCredit] = []
        for kind in ("cast", "crew"):
            entries = payload.get(kind)
            if not isinstance(entries, list):
                raise TmdbListRefused(
                    f"{subject}: TMDb's response carries no {kind!r} array. Reading "
                    "that as 'no credits of that kind' would build an empty "
                    "collection out of a broken response."
                )
            # ``_ids`` is what refuses an entry with no id, and it is also what
            # guarantees every entry is a dict, so the two ``.get``s below are
            # safe by the time they run.
            credits += [
                PersonCredit(
                    tmdb_id=value,
                    kind=kind,
                    job=entry.get("job"),
                    department=entry.get("department"),
                )
                for entry, value in zip(entries, _ids(entries, subject), strict=True)
            ]
        return credits

    async def person_detail(self, person_id: int) -> PersonDetail:
        """One person's own record: their biography and their profile photo.

        A second endpoint rather than a richer ``person_credits``, because the
        credits endpoints do not carry a biography at all -- ``/person/{id}``
        is where TMDb keeps it -- and because the two answer questions that
        change at different rates: a filmography changes when someone works, a
        biography changes about never, and two endpoints are two cache entries
        rather than one that has to expire for the faster of them.

        Not paged, like ``/collection/{id}``, so this sends no ``page``. No
        ``append_to_response`` either, which is what upstream's own person read
        sends on this path (Kometa's ``get_person`` passes ``partial=None``, so
        the library's default append set is never applied). NOT KOMETA: no
        ``language``: upstream sends the operator's configured one and this
        service has no such setting on any collection path, so sending nothing
        is the honest shape rather than a value invented here -- see
        ``PersonDetail`` for what an absent biography then means.

        The name is read and thrown away on purpose. It is the only field TMDb
        always sends for a person, so it is the one thing that distinguishes
        "this person has no biography and no photo" -- a legitimate answer that
        must leave the collection alone -- from "this response is not a person
        record", which must be refused. Without the probe the two are the same
        empty ``PersonDetail`` and the second one silently leaves a collection
        without artwork on every pass.
        """
        subject = f"TMDb person {person_id}"
        payload = await self._get(f"/person/{person_id}", {}, subject)
        if not isinstance(payload.get("name"), str) or not payload["name"]:
            raise TmdbListRefused(
                f"{subject}: TMDb's response carries no 'name', so it is not a "
                "person record. Reading that as 'this person has no biography "
                "and no photo' would leave the collection without artwork on "
                "every pass, with nothing to notice. Check the person id."
            )
        biography = payload.get("biography")
        profile_path = payload.get("profile_path")
        return PersonDetail(
            biography=(
                biography.strip()
                if isinstance(biography, str) and biography.strip()
                else None
            ),
            profile_path=(
                profile_path
                if isinstance(profile_path, str) and profile_path.startswith("/")
                else None
            ),
        )

    async def discover(self, media_type: str, filters: Mapping[str, object]) -> list[str]:
        """``/discover/{movie,tv}`` under an arbitrary filter map.

        Two callers with one shape. The narrow by-id builders send a single
        entry -- a company, a network, a keyword -- because there is no listing
        endpoint for those any more (see the module docstring); ``tmdb_discover``
        sends whatever the operator wrote, already validated and already named
        in TMDb's own vocabulary by
        ``collections.builders.tmdb_discover.discover_filters``. Nothing here
        inspects a key, so the general case needed no plumbing that the narrow
        one had not already paid for: every parameter TMDb takes, including
        ``sort_by``, ``region``, ``language`` and ``watch_region``, is just an
        entry in ``filters``. Deciding which of them mean anything for this
        media type is the builder's job and happens before this call.
        """
        path = f"/discover/{media_type}"
        described = ", ".join(f"{name}={value}" for name, value in sorted(filters.items()))
        return await self._paged(
            path, dict(filters), subject=f"TMDb discover ({described})", items_key="results"
        )


def _of_media_type(entries: list, media_type: str | None, subject: str) -> list:
    """The entries of one TMDb media type, when the caller wants only one.

    ``None`` means every entry, which is what the chart and discover
    endpoints need: each already answers for a single media type and tags
    nothing. ``/list/{id}`` is the exception -- a v3 list holds movies and
    shows at once and carries a ``media_type`` on each entry.

    Filtering matters rather than being tidiness, and for the same reason
    ``builders/mdblist.py`` and ``builders/tvdb.py`` drop the other kind:
    TMDb's movie and show ids are different id spaces sharing one namespace,
    so a show's id offered to a Movie library does not fail to resolve, it
    can resolve to an *unrelated film*. A missing member is visible as a
    smaller collection; a wrong one looks exactly like a right one.

    An entry that does not say which it is raises, on ``_ids``' argument one
    field over: guessing adds a title nobody listed and skipping removes one
    somebody did, and neither leaves anything to notice.
    """
    if media_type is None:
        return entries
    kept = []
    for entry in entries:
        value = entry.get("media_type") if isinstance(entry, dict) else None
        if value is None:
            raise TmdbListRefused(
                f"{subject}: an entry in TMDb's response has no 'media_type', so "
                "there is no way to tell which library it belongs in. TMDb's "
                "movie and show ids share one namespace, so guessing would add "
                "an unrelated title rather than nothing."
            )
        if value == media_type:
            kept.append(entry)
    return kept


def _ids(entries: list, subject: str) -> list[str]:
    """The ``id`` of every entry, as strings, in the order TMDb listed them.

    An entry without an id raises rather than being skipped: a silently
    dropped member is a slightly smaller collection every pass and no way at
    all to notice, which is the same argument ``text_file`` refuses an
    unparseable line on.
    """
    ids = []
    for entry in entries:
        value = entry.get("id") if isinstance(entry, dict) else None
        if value is None:
            raise TmdbListRefused(
                f"{subject}: an entry in TMDb's response has no 'id'. Skipping it "
                "would build a smaller collection with nothing to show for it."
            )
        ids.append(str(value))
    return ids
