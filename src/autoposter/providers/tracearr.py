"""Tracearr's public v2 read API: the transport under the activity builders.

Tracearr publishes **no** most-watched, popular or top-N endpoint in either API
version (``docs/research/tracearr-api-harvest.md``, "The headline finding": the
only such endpoint is on the internal session-JWT API and an API key cannot
reach it). So the ranking row 79 asks for is computed on this side, from raw
watch history. This module is only the transport -- the ranking is
``autoposter.collections.activity`` and the builder is
``autoposter.collections.builders.tracearr``.

Four decisions, each of them a way a wrong answer or a leaked credential could
otherwise reach a live deployment.

**The single-page-app 200.** Tracearr serves its SPA on every unmatched path,
so a mistyped base URL answers ``200 text/html`` rather than a 404 and a naive
client would decode nothing and build an empty collection -- which one layer
down means "remove every member". Every matched v2 route carries
``x-ratelimit-*`` headers; both fallbacks (the bare-envelope 404 and the SPA
200) sit outside the rate-limit plugin and carry none. Header presence is
therefore the route-matched tell, and it is cheaper and more robust than
sniffing the body.

**Cursor paging.** ``meta.nextCursor`` is passed back as ``cursor`` until it is
null, bounded by ``MAX_PAGES`` so a runaway upstream costs a bounded number of
requests. A cursor is opaque *and* is base64 of ``{"t": ..., "id": <session
uuid>}``, so it is handed straight back, never inspected and never logged.

**The base URL never leaves this module.** It is a cluster-internal hostname an
operator put in their YAML. ``httpx.HTTPStatusError`` puts the full URL in its
own message, and the engine logs a failed build with ``logger.exception`` --
traceback included (``collections/engine.py``). So every exception raised while
fetching becomes a ``TracearrRefused`` naming the path and the original
exception's CLASS, raised ``from None`` so the chained message cannot reach the
log either. That catch is deliberately *total* rather than a list of classes:
the libraries under here interpolate what they were handed into their own
messages -- the full URL, an offending port, the hostname, a punycode-decoded
transform of it -- and which class carries which is a moving target across
dependency versions. ``_get`` records why at length. The API key lives in a
header and enters no message, no cache key and no ``repr``.

**404 raises -- as its own class.** ``fetch_json`` turns a 404 into ``None``,
which is the right answer for artwork and the wrong one here, for the reason
``providers/tmdb_lists.py`` already records: an empty membership means "remove
every member" (``lists.reconcile_list_collection``). So every method raises.
What is different here is that a matched 404 raises ``TracearrNotFound``, a
subclass, because this client has two kinds of caller: one asking "give me the
window" (where a 404 is the source being gone) and one asking "who is this one
title" (where a 404 is one title being gone, in a list of twenty). The
transport does not get to decide which of those is fatal -- it only makes the
two distinguishable. Everything else, the SPA fallback included, stays a plain
``TracearrRefused``, which is a dead source however it is caught.

No response cache, and that is a decision rather than an omission. ``/history``
is not cached server-side, the ranking is recomputed every pass by design (the
harvest's "recompute rather than cache ranks", because ``/history``'s
``since``/``until`` are instants while ``/media/{ref}/stats``' windows are UTC
calendar days and the two demonstrably disagree), and every page after the
first carries a unique cursor -- so a response cache would store one entry per
pass per page and never serve one. The per-pass memo that *does* pay for itself
is the builder's, on ``ctx.run_cache``.

**The budget.** 240 requests/minute, shared across the whole v2 tree and spent
even by requests that fail authentication. One collection costs one page per
100 plays in its window plus, on a Show library, one ``/media/{ref}`` per
ranked title -- which is why the builder's ``limit`` is applied *before* those
calls rather than after.
"""
import logging
import re

import httpx

from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

__all__ = [
    "API_PREFIX",
    "MAX_PAGES",
    "PAGE_SIZE",
    "TracearrClient",
    "TracearrNotFound",
    "TracearrRefused",
]

# Every route this client uses is under the v2 public tree. v1 is not a
# camelCase mirror of it: v1 ``/history`` returns raw sessions rather than
# plays and carries no media identity at all, so it cannot be joined to Plex.
API_PREFIX = "/api/v2/public"

# Tracearr's own maximum. A 30-day window on a busy server is a handful of
# pages at this size and a few dozen at the default of 25.
PAGE_SIZE = 100

# How many pages one call will ever fetch. The same bound, and the same
# reasoning, as ``providers/tmdb_lists.MAX_PAGES``: a definition's ``limit``
# trims after the fact and cannot stop a fetch that has already happened.
MAX_PAGES = 10

# The header whose presence means a v2 route matched. See the module docstring.
RATE_LIMIT_HEADER = "x-ratelimit-limit"

# A canonical media id is a uuid. Checked because the value is interpolated
# into a path: it always comes from Tracearr itself, and a value with a slash
# in it would address a different endpoint entirely.
_MEDIA_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                       r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


class TracearrRefused(Exception):
    """Tracearr could not answer this, or answered something unusable.

    One class for the cases a caller cannot usefully tell apart -- the SPA
    fallback, a transport failure, a response with no ``data`` array -- because
    the engine logs the class name either way and the message says which. It
    carries the path and, for a transport failure, the original exception's
    class name. It never carries the base URL (a cluster-internal hostname) and
    never the API key.
    """


class TracearrNotFound(TracearrRefused):
    """A v2 route matched and answered 404.

    A subclass rather than a flag, and it exists for exactly one caller: the
    builder resolves one ``/media/{uuid}`` per ranked title, and a title
    deleted from Tracearr between the history read and that lookup is a
    transient, per-item fact -- not a reason to fail a whole collection.
    Distinguishing it here means the *caller* decides, while everything the
    caller cannot recover from (no route, no connection, a mangled envelope)
    stays a plain ``TracearrRefused``.

    Note what it is NOT a licence for: the history endpoint answering 404 is
    still fatal, because the builder never catches this class around that call.
    """


class TracearrClient:
    """Reads watch history and media identity from one Tracearr instance."""

    name = "TRACEARR"

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        max_pages: int = MAX_PAGES,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_pages = max_pages

    def __repr__(self) -> str:
        # Neither the key nor the base URL: a bundle's ``repr`` reaches logs
        # and test output, and ``SourceClients`` is a dataclass whose default
        # repr would otherwise print whatever this returns.
        return "TracearrClient(...)"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "accept": "application/json"}

    async def _get(self, path: str, params: dict, subject: str) -> dict:
        """One request. Raises rather than answering ``None`` or HTML.

        ``fetch_json`` is used for the decode-and-check-404 half only; the
        cache is deliberately off (module docstring), which also means the
        bearer token cannot reach a cache key because no key is ever built.
        """
        url = f"{self._base_url}{API_PREFIX}{path}"

        async def request() -> httpx.Response:
            response = await self._client.get(url, params=params, headers=self._headers())
            if RATE_LIMIT_HEADER not in response.headers:
                # The SPA 200 and the bare-envelope 404 both land here. Neither
                # is data, and both mean the request never reached the API.
                raise TracearrRefused(
                    f"{subject}: {path} answered {response.status_code} with no "
                    f"{RATE_LIMIT_HEADER!r} header, so it was served by Tracearr's "
                    "single-page app or its not-found fallback rather than by the "
                    "API. Check the configured base URL."
                )
            return response

        try:
            payload = await fetch_json(
                method="GET",
                url=url,
                params=params,
                request=request,
                cache=None,
                ttl_seconds=0,
            )
        except TracearrRefused:
            # The guard's own refusal, already hygienic. It is raised inside
            # ``request()``, which runs inside this ``try``, so it needs an
            # explicit pass-through or the clause below would re-wrap it and
            # lose the message that names the missing rate-limit header.
            raise
        except Exception as error:
            # Deliberately total, and the breadth is the point rather than a
            # shortcut.
            #
            # The invariant is that NO third-party exception text may cross
            # this boundary: ``base_url`` is a cluster-internal hostname, the
            # engine logs a failed build with ``logger.exception``, and the
            # libraries under here interpolate whatever they were handed into
            # their own messages -- the full URL, the offending port, the
            # hostname, a punycode-decoded transform of it, a fragment of an
            # undecodable response body.
            #
            # That surface cannot be enumerated. Two rounds of listing the
            # classes that can escape were each defeated by the first probe
            # outside the tested inputs: ``httpx.InvalidURL`` is a *sibling* of
            # ``HTTPError`` rather than a descendant, and then ``idna``'s errors
            # (``UnicodeError`` subclasses, so in neither) escape the *second*,
            # unguarded ``idna`` call site -- ``_urls.py``'s ``URL.host``
            # property, which ``idna.decode``s any ``xn--`` host on the
            # request-build path -- while ``response.json()`` raises
            # ``UnicodeDecodeError`` rather than ``JSONDecodeError`` on a body
            # that is not decodable text. ``pyproject.toml`` pins ``httpx>=0.27``
            # with no upper bound, so any such list is validated against one
            # patch version of a dependency free to move those call sites again.
            #
            # So the guard is closed by construction instead: nothing leaves
            # ``_get`` except ``TracearrRefused`` and its subclass, whatever
            # httpx, idna or the stdlib decide to raise next. The class name is
            # the diagnostic that survives, and ``from None`` suppresses the
            # chain -- without it ``logger.exception`` prints "During handling
            # of the above exception" followed by the original message, which is
            # the leak this whole arrangement exists to prevent. Losing the
            # traceback is the accepted price; it is not recovered by logging
            # ``exc_info`` here, because that would put the URL in a log line
            # just as surely.
            detail = type(error).__name__
            if isinstance(error, httpx.HTTPStatusError):
                # An integer, carrying neither URL nor credential. Without it a
                # rejected API key (401), a spent rate-limit budget (429) and a
                # dead instance (500) are one indistinguishable log line -- and
                # a 401 carries rate-limit headers, so it passes the guard and
                # lands exactly here.
                detail = f"{detail} {error.response.status_code}"
            raise TracearrRefused(
                f"{subject}: Tracearr refused {path} ({detail})"
            ) from None
        if payload is None:
            raise TracearrNotFound(
                f"{subject}: Tracearr answered 404 for {path}. Building an empty "
                "collection instead would remove every member it has."
            )
        if not isinstance(payload, dict):
            # A matched route answering a top-level array or scalar. Both
            # callers annotate ``dict`` and both go on to ``.get`` it, so
            # without this the failure surfaces as an ``AttributeError`` from
            # somewhere downstream -- outside the ``TracearrRefused`` contract
            # the builder's per-bucket handling is built on, and for ``media()``
            # only after the wrong type has been handed to the ranking code.
            # The type's name only: a payload this client cannot parse is
            # exactly the payload it must not quote into a log.
            raise TracearrRefused(
                f"{subject}: {path} answered a {type(payload).__name__}, not a JSON "
                "object"
            )
        return payload

    async def history(
        self, *, since: str, media_type: str, page_size: int = PAGE_SIZE
    ) -> list[dict]:
        """Every play in the window, newest first, as Tracearr's own records.

        ``since`` is an ISO-8601 instant (never a calendar day: ``/history``
        and ``/media/{ref}/stats`` use different window semantics and their
        numbers do not agree). ``media_type`` is Tracearr's own word --
        ``"movie"`` or ``"episode"``.

        A record **is** a play: the API groups sessions into resume chains and
        drops chains under two minutes, so counting records is counting plays
        and nothing here re-filters.

        Records are handed back unparsed. The API carries undocumented keys and
        omits documented ones, so a strict model would reject live payloads;
        the only fields anything downstream reads are the dozen named in
        ``collections/activity.py``.
        """
        subject = "the Tracearr watch history"
        base: dict[str, object] = {
            "since": since,
            "media_type": media_type,
            "pageSize": page_size,
        }
        records: list[dict] = []
        cursor = None
        for _ in range(self._max_pages):
            params = dict(base)
            if cursor is not None:
                params["cursor"] = cursor
            payload = await self._get("/history", params, subject)
            data = payload.get("data")
            if not isinstance(data, list):
                raise TracearrRefused(
                    f"{subject}: the response carries no 'data' array. Reading that "
                    "as an empty window would empty the collection."
                )
            if not all(isinstance(record, dict) for record in data):
                raise TracearrRefused(
                    f"{subject}: the response's 'data' array holds something that is "
                    "not a record"
                )
            records += data
            meta = payload.get("meta")
            cursor = meta.get("nextCursor") if isinstance(meta, dict) else None
            if not cursor:
                break
        else:
            # Every page spent with a cursor still outstanding: the one exit
            # that hands back a *truncated* window. Silent truncation looks
            # exactly like a complete answer -- the judgement
            # ``providers/tmdb_lists`` and ``collections/imdb_graphql`` both made.
            logger.warning(
                "%s: stopped at the %d-page cap with %d record(s); the window may "
                "hold more plays than this pass will see",
                subject, self._max_pages, len(records),
            )
        return records

    async def media(self, media_id: str) -> dict:
        """One canonical media document: SHOW-level ids and availability.

        The only correct route from an episode play to the show it belongs to.
        An episode record's own ``imdb_id``/``tmdb_id``/``tvdb_id`` are the
        EPISODE's, and ``GET /media/show:tvdb:<episode id>`` is a live-verified
        404 (harvest finding 3, banked as
        ``docs/research/tracearr/payloads/v2-media-show-by-tvdb-ref.json``) --
        so a show's ids come from here or from nowhere.
        """
        if not isinstance(media_id, str) or not _MEDIA_ID.match(media_id):
            raise TracearrRefused(
                f"{media_id!r} is not a canonical Tracearr media id (a uuid), so it "
                "would address some other endpoint rather than a media document"
            )
        return await self._get(f"/media/{media_id}", {}, f"Tracearr media {media_id}")
