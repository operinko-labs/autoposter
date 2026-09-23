import asyncio
import logging
import re
import time
from dataclasses import dataclass

import requests
from plexapi.exceptions import NotFound as PlexNotFound
from plexapi.server import PlexServer

from autoposter.intake.arr import RenderIntent
from autoposter.plex import artwork as plex_artwork
from autoposter.queue import job_memo
from autoposter.render.naming import derive_root_folder
from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_FIELD_LOCKS, CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD,
    CAP_LOGO_UPLOAD_KEY, CAP_RESET_TO_AGENT_DEFAULT, CAP_TITLE_CARD_URL,
    ItemNotFound, PathMismatch, ResolvedItem, SectionItem, ServerItemRef,
)

logger = logging.getLogger(__name__)


class _LazyPlexServer:
    """Defers connecting to Plex until the server is actually used.

    ``PlexServer(...)`` makes a blocking network call. Doing that eagerly at
    boot means a Plex outage crashloops the whole pod, taking webhook intake
    down with it. Connecting lazily lets the process start, serve /healthz,
    and queue webhooks while Plex is unreachable; jobs that need Plex get
    ``config.plex.resolve_max_attempts`` worth of backoff (see
    ``_handle_intent`` in app.py) instead of the generic retry cap, but an
    outage longer than that still parks them permanently -- see "Recovering
    parked jobs" in deploy/README.md to requeue them by hand. Every attribute
    access (already happening inside a worker thread via ``PlexClient``)
    triggers a (re)connect attempt if the previous one failed or never ran.

    Lives here rather than in ``main.py`` (where it started) so that
    ``servers/registry.py``'s ``build_servers`` can construct one without
    importing ``main`` -- the circular import that shape would create.
    """

    def __init__(self, url: str, token: str):
        self._url = url
        self._token = token
        self._server = None

    def _connect(self):
        if self._server is None:
            try:
                self._server = PlexServer(self._url, self._token)
            except Exception:
                logger.error("failed to connect to Plex at %s", self._url, exc_info=True)
                raise
        return self._server

    def __getattr__(self, name):
        return getattr(self._connect(), name)


_GUID_RE = re.compile(r"^(?:com\.plexapp\.agents\.)?(tmdb|imdb|tvdb)://([^?]+)")

#: ``fetch_item``'s retry policy. A fixed policy rather than a config key: the
#: operator asked for reliability, not a knob, and ``PlexConfig
#: .resolve_max_attempts`` is a DIFFERENT budget (the job-level parking when
#: Plex is unreachable at all) that this must not be confused with. Four
#: attempts in all -- the initial one plus ``FETCH_ITEM_RETRIES`` -- and the
#: wait BEFORE retry n is the n-th entry below.
FETCH_ITEM_RETRIES = 3
FETCH_ITEM_BACKOFF_SECONDS = (2.0, 4.0, 8.0)

#: Bound to a module-level name so a test can neutralise the 14 seconds of
#: real waiting by patching ``autoposter.plex.client._sleep``. Patching
#: ``autoposter.plex.client.asyncio.sleep`` would reach through to the
#: ``asyncio`` module object itself and silence sleeping process-wide, for
#: every other module in the same interpreter.
_sleep = asyncio.sleep

#: How long ``PlexClient`` trusts its section list (perf workstream B3).
#: ``library.sections()`` is one GET that every resolve, listing and existence
#: probe made afresh -- once per item per full pass -- for a list that changes
#: when an operator adds or edits a library. A resolve that misses re-reads it
#: at once (``_search_sync``), so a library added inside the window costs one
#: failed walk, never a deferred job.
SECTIONS_TTL_SECONDS = 60.0

#: Bound to a module-level name for ``_sleep``'s reason: a test moves the clock
#: by patching ``autoposter.plex.client._monotonic``.
_monotonic = time.monotonic


def _section_signature(sections) -> list[tuple]:
    """What makes two section listings different, as plain data. ``key`` is
    Plex's section id (absent on test doubles, hence ``getattr``)."""
    return [
        (
            getattr(s, "key", None), s.title, s.type,
            tuple(getattr(s, "locations", None) or ()),
        )
        for s in sections
    ]


class PlexPathMismatch(PathMismatch):
    """Plex's spelling of PathMismatch; queue/worker.py's except ladder names it."""


def parse_guids(guids: list[str]) -> dict[str, str]:
    """Map Plex GUID strings to ``{agent: id}``.

    Matches ``tmdb://``, ``imdb://`` and ``tvdb://`` GUIDs, optionally prefixed with
    ``com.plexapp.agents.`` (as produced by the legacy ``imdb``/``tmdb``/``tvdb``
    agents). Genuine legacy Plex GUIDs such as ``com.plexapp.agents.themoviedb://``
    and ``com.plexapp.agents.thetvdb://`` use different tokens and are not matched;
    those are silently dropped.
    """
    parsed = {}
    for guid in guids:
        match = _GUID_RE.match(guid)
        if match:
            parsed[match.group(1)] = match.group(2)
    return parsed


def as_int(value: str | None) -> int | None:
    """``int(value)`` or None -- for optional external ids that may be absent
    or malformed. Shared with ``arr.sync``."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# The batched metadata read (roadmap row 197). Chunk default from phase 9a's
# probe F (10 calls / 16.90s for 1955 movies at chunk=200, ~8ms/item flat
# across chunk sizes) and
# bounded by the URL-length cap the phase-B probe measured
# (docs/research/plex-batch-probe/README.md, D2).
TAG_BATCH_CHUNK = 200


@dataclass(frozen=True)
class ItemTags:
    """One item's tag families from the metadata endpoint, as plain data.

    The section listing truncates or strips these (9a's probe verdicts,
    quoted in the FILTER_ATTRIBUTES rows); the batch endpoint returns them
    full. Empty tuples are an ANSWER -- "this item has none" -- which is why
    the fetch stores them: absence from the index means "not fetched", and
    the two must never be conflated (the enrichment refusal law).
    """

    genres: tuple[str, ...]
    labels: tuple[str, ...]
    collections: tuple[str, ...]
    audio_languages: tuple[str, ...]
    subtitle_languages: tuple[str, ...]


def _safe_attr(item: object, name: str) -> object | None:
    """``object.__getattribute__``, so a falsy value can never trigger plexapi's
    partial-object reload -- the same load-bearing read
    ``collections/filter_values._listing_value`` uses, for the same reason."""
    try:
        return object.__getattribute__(item, name)
    except AttributeError:
        return None


def _uniq(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


def _tag_names(item: object, attr: str) -> tuple[str, ...]:
    children = _safe_attr(item, attr) or []
    return _uniq(getattr(child, "tag", None) for child in children)


def _stream_languages(item: object, stream_type: int) -> tuple[str, ...]:
    """streamType 2 is audio, 3 is subtitles. The value read is the stream's
    ``languageTag`` -- the ISO 639-1 CODE (``en``), not the ``language``
    display title (``English``). Probe decision D6 measured all three of
    ``language``, ``languageCode`` and ``languageTag`` populated on both audio
    and subtitle streams of the production server, so the choice is free; the
    code is the right one because the tree's only language normaliser,
    ``collections/filters.base_language_code``, consumes codes
    and passes an unparseable value through UNCHANGED by design -- a display
    title would never reduce to ``en`` and the mismatch would be silent.
    ``languageTag`` over ``languageCode`` (ISO 639-2, ``eng``) because it is
    what that normaliser already emits, so a comparison is an identity for the
    common case. ``Media``, ``MediaPart`` and streams are plain PlexObjects
    with no reload guard of their own (``filter_values._resolutions``
    documents the same)."""
    found = []
    for media in _safe_attr(item, "media") or []:
        for part in getattr(media, "parts", None) or []:
            for stream in getattr(part, "streams", None) or []:
                if getattr(stream, "streamType", None) == stream_type:
                    found.append(getattr(stream, "languageTag", None))
    return _uniq(found)


def _iter_metadata_batches(section, rating_keys, chunk_size):
    """Full metadata for the given keys, ``ceil(N/chunk)`` fetches exactly.

    The seam is plexapi's own list-of-ints translation: ``fetchItems`` turns a
    list of ints into ``/library/metadata/{k1,k2,...}`` (base.py:334-335). A
    non-numeric key raises ValueError ON FIRST ITERATION, not at call time --
    this is a generator: rating keys handed here come off plexapi items and
    are always numeric, so a failure to parse is a caller bug, not a library
    state. Duplicate keys are collapsed (order-preserving) so the fetch count
    stays ``ceil(N/chunk)`` over DISTINCT keys. BLOCKING -- callers on the
    event loop go through ``asyncio.to_thread``
    (``collections/enrichment.py`` does).
    """
    keys = list(dict.fromkeys(int(key) for key in rating_keys))
    for start in range(0, len(keys), chunk_size):
        yield from section.fetchItems(keys[start:start + chunk_size])


def fetch_tag_index(
    section, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, ItemTags]:
    """``{rating_key: ItemTags}`` for every key Plex still answers.

    A key in the request but not the result is an item Plex no longer holds
    (or held back): the caller decides what that means -- a ``filters:``
    enrichment REFUSES the definition rather than evaluating without it.
    Plain data out: nothing returned is a plexapi object, the same
    discipline as ``SectionItem``.
    """
    index: dict[str, ItemTags] = {}
    for item in _iter_metadata_batches(section, rating_keys, chunk_size):
        rating_key = _safe_attr(item, "ratingKey")
        if rating_key is None:
            continue
        index[str(rating_key)] = ItemTags(
            genres=_tag_names(item, "genres"),
            labels=_tag_names(item, "labels"),
            collections=_tag_names(item, "collections"),
            audio_languages=_stream_languages(item, 2),
            subtitle_languages=_stream_languages(item, 3),
        )
    return index


@dataclass(frozen=True)
class CreditTags:
    """One item's credit families from the metadata endpoint, as plain data.

    Same discipline as ``ItemTags``; separate class because its consumers are
    different (the credits SCAN, not the filter enrichment) and the two must
    stay independently cheap. Empty tuples are "credited nobody", an answer.

    ``actors`` is TRUNCATED, not complete: probe D1 measured a server-side cap
    of 200 ``Role`` children per item, which a single-key fetch does not
    escape (docs/research/plex-batch-probe/README.md). ``directors`` /
    ``writers`` / ``producers`` are empty on every SHOW -- series-level Plex
    metadata carries none, in the batch, in a single-key fetch and in
    ``listFilterChoices`` alike -- so nothing downstream may synthesise them.
    """

    actors: tuple[str, ...]
    directors: tuple[str, ...]
    writers: tuple[str, ...]
    producers: tuple[str, ...]


def fetch_credit_index(
    section, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, CreditTags]:
    """``{rating_key: CreditTags}`` for every key Plex still answers.

    Rides the same batched read as ``fetch_tag_index`` (``ceil(N/chunk)``
    calls); the phase-B probe (docs/research/plex-batch-probe/README.md, D1)
    is what established the batch response carries Role/Director/Writer/
    Producer at all, and that the batched credit lists are identical
    tag-for-tag to a single-key fetch's. BLOCKING, like its sibling.
    """
    index: dict[str, CreditTags] = {}
    for item in _iter_metadata_batches(section, rating_keys, chunk_size):
        rating_key = _safe_attr(item, "ratingKey")
        if rating_key is None:
            continue
        index[str(rating_key)] = CreditTags(
            actors=_tag_names(item, "roles"),
            directors=_tag_names(item, "directors"),
            writers=_tag_names(item, "writers"),
            producers=_tag_names(item, "producers"),
        )
    return index


@dataclass(frozen=True)
class _RawMatch:
    """Plain data extracted from a matched ``plexapi`` item, inside the search thread.

    Nothing here is a ``plexapi`` object, so reading these fields back on the event
    loop can never trigger a lazy HTTP reload.

    The search matches the *show* (or movie) by GUID. ``rating_key``/``title`` are
    the item the intent actually refers to — the show itself for a ``show`` intent,
    but the season's or episode's own identity for a ``season``/``episode`` intent,
    reached by navigating down from the matched show inside this same thread.
    ``file_path``/``item_locations`` stay the *show's*, because assets for every
    season and episode live under the show's folder.

    ``_fetch_by_rating_key_sync`` arrives at the same split from the other
    direction — it fetches the season or episode itself and climbs back up to
    the show — and must fill these fields identically.

    ``show_title`` is the SHOW's own title and is filled for a SEASON intent
    only (roadmap row 78); both producers below take it off the show object
    they already hold.

    ``parent_guids`` is the show's own ``guids`` (spec §4.2's parent identity),
    captured for a SEASON or EPISODE intent only -- empty for a movie or show,
    which have no parent to key. It is the same read already taken for
    ``guids`` on those two kinds; this just keeps it under its own name so
    ``resolve()`` can fill ``ResolvedItem.parent_tmdb_id``/``parent_tvdb_id``/
    ``parent_imdb_id`` without a caller mistaking the item's own ids for its
    parent's.
    """

    rating_key: str
    library: str
    title: str
    year: int | None
    file_path: str | None
    item_locations: list[str]
    section_locations: list[str]
    art_url: str | None
    guids: list[str]
    parent_rating_key: str | None
    parent_guids: list[str]
    original_title: str | None = None
    show_title: str | None = None


#: The Plex arm of ``MediaServer.capabilities`` (spec §3.4) -- every operation
#: ``plex/artwork.py`` and ``plex/writer.py`` back, wired up below.
PLEX_CAPABILITIES = frozenset({
    CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD, CAP_LOGO_UPLOAD_KEY, CAP_FIELD_LOCKS,
    CAP_ARTWORK_PROVENANCE, CAP_TITLE_CARD_URL, CAP_RESET_TO_AGENT_DEFAULT,
})


def _label_tags(item) -> list[str]:
    """``item.labels``' tag names. Read in a thread by ``PlexClient.item_labels``,
    because ``labels`` on a partial plexapi object can trigger a synchronous
    ``reload()`` (perf C2)."""
    return [t.tag for t in getattr(item, "labels", None) or []]


class PlexClient:
    """Resolves render intents to Plex items, and conforms to ``MediaServer``.

    ``plexapi`` is synchronous, so calls run in a thread to keep the event loop free.
    All ``plexapi`` attribute access happens inside that thread — attributes on
    partial objects can trigger a synchronous HTTP reload, so nothing touched back
    on the event loop may be a ``plexapi`` object.

    ``http``/``base_url``/``token`` back the artwork read-back methods
    (``fetch_artwork``/``artwork_provenance``/``check_liveness``), which need an
    HTTP client of their own rather than ``plexapi``'s -- see those functions in
    ``plex/artwork.py``. They default to unset because most callers (``resolve``,
    ``list_items``, the write paths) never touch them; ``app.py``'s construction
    site is the one that supplies real values.
    """

    name = "plex"
    capabilities = PLEX_CAPABILITIES

    def __init__(
        self, server, excluded_libraries: list[str],
        http=None, base_url: str = "", token: str = "",
    ):
        self._server = server
        self._excluded = set(excluded_libraries)
        self._http = http
        self.base_url = base_url
        self._headers = {"X-Plex-Token": token} if token else {}
        # (monotonic time read, section list), or None before the first read.
        # Written from worker threads; a tuple assignment is atomic, and two
        # threads refreshing at once each store a correct list.
        self._sections_cache: tuple[float, list] | None = None

    def _library_sections(self, *, refresh: bool = False) -> list:
        """Every section Plex has, cached for ``SECTIONS_TTL_SECONDS``.

        Called from worker threads only (every caller below runs inside
        ``asyncio.to_thread``), like every other plexapi touch here. The cached
        ``LibrarySection`` objects are shared by concurrent threads from then
        on; their lookups (``getGuid``, ``search``) are GETs that set nothing
        a concurrent caller depends on.
        """
        cached = self._sections_cache
        now = _monotonic()
        if not refresh and cached is not None and now - cached[0] < SECTIONS_TTL_SECONDS:
            return cached[1]
        # plexapi exposes `library` as a property and `sections` as a method.
        sections = list(self._server.library.sections())
        self._sections_cache = (now, sections)
        return sections

    def _sections(self, *wanted_types: str, refresh: bool = False):
        """The non-excluded library sections of the given Plex types
        ("movie"/"show").

        Constraining the walk by type is what keeps a movie library from
        answering a show-shaped intent — see the GUID-namespace note in
        `_search_sync`. Every caller but one asks for a single type;
        `_library_names_sync` below asks for both rather than keeping a second
        copy of "non-excluded and of the right type" that a change to this one
        would not reach.

        Served from ``_library_sections``' cache unless ``refresh`` (perf
        workstream B3).
        """
        return [
            s
            for s in self._library_sections(refresh=refresh)
            if s.title not in self._excluded and s.type in wanted_types
        ]

    def _sections_changed_on_reread(self) -> bool:
        """Re-read the section list; True when it differs from the cached one.

        A resolve's miss is the one moment a stale list can cost something --
        the item may sit in a library added since the cached read -- so a miss
        re-reads, and the walk runs again only when the list really moved. A
        genuine miss therefore costs the one listing GET every resolve paid
        before the cache existed, and nothing more.
        """
        cached = self._sections_cache
        before = cached[1] if cached is not None else None
        after = self._library_sections(refresh=True)
        return before is not None and _section_signature(before) != _section_signature(after)

    def _library_names_sync(self) -> set[str]:
        return {s.title for s in self._sections("movie", "show")}

    async def library_names(self) -> set[str]:
        # plexapi's sections() is a blocking HTTP call of up to several
        # seconds on a large server -- offloaded for `list_items`' own reason.
        return await asyncio.to_thread(self._library_names_sync)

    def _fetch_by_rating_key_sync(self, intent: RenderIntent, sections) -> _RawMatch | None:
        """The item named by ``intent.native_id_on("plex")``, or None to fall back.

        Adoption stored every item's exact Plex identity -- today a
        ``media_item_server_refs`` row, before the identity migration the
        ``media_items.rating_key`` column -- so an intent built from such a
        row does not need an agent lookup at all. It also must not use one:
        an episode intent's own ids are only ever the *series'* ids here
        (the adoption walk takes the show's guids, mirroring what
        ``_search_sync`` builds from the show container), and a row adopted
        before that alignment can still carry episode-level ids of its own.
        Those either match nothing (the job retries as "waiting for Plex"
        until it parks) or match an unrelated item that happens to carry the
        same number.

        Returning None rather than raising is the whole contract here: a
        rating key is a *hint*. Plex renumbers on a library rebuild, so a
        stored key can name nothing, or name something else entirely --
        including a real item of the same type, in the same library, that is
        simply not the one the intent means. That is why type+library is not
        treated as identity: the season/episode numbers or external ids are
        checked too. Every such case degrades to the GUID search rather than
        failing the job.

        The result deliberately mirrors what the GUID search would have built
        for the same intent, field for field, so that a key going stale
        changes how fast an item resolves and nothing else about it.
        ``_RawMatch``'s docstring has the split: identity comes from the item
        the intent refers to, everything path- and agent-shaped comes from the
        movie or show containing it.
        """
        try:
            item = self._server.fetchItem(int(intent.native_id_on("plex")))  # type: ignore[arg-type]
        except (PlexNotFound, TypeError, ValueError):
            # NotFound: the key names nothing any more. TypeError/ValueError:
            # `media_item_server_refs.native_id` is a text column (it has to
            # be -- a Jellyfin id is a hex string), so a row can hold a
            # non-number.
            return None
        if item is None or getattr(item, "type", None) != intent.kind:
            # Compared against the intent's kind, not the library type, so a
            # key that now names the season of the episode we wanted is
            # refused too -- accepting it would write the episode's title card
            # onto the season's identity.
            return None

        # `fetchItem` knows nothing about sections, so neither the library
        # exclusions nor the section-type filter apply to it. Requiring the
        # item's own library to be among the sections the GUID walk was
        # allowed to ask re-applies both -- and yields the `locations` the
        # root folder is derived from, which the item itself does not carry.
        section = next(
            (s for s in sections if s.title == getattr(item, "librarySectionTitle", None)),
            None,
        )
        if section is None:
            return None

        # Type and library alone are not identity: a stale/renumbered key can
        # land on a real, same-type item in the same library that is simply
        # not the one the intent means. Accepting it would stamp the intent's
        # season/episode numbers onto the wrong item, or write a wrong
        # movie/show's identity onto an unrelated row. Any mismatch here
        # falls back to the GUID search, same as every other refusal above.
        if intent.kind == "season":
            if getattr(item, "index", None) != intent.season_number:
                return None
        elif intent.kind == "episode":
            if (
                getattr(item, "parentIndex", None) != intent.season_number
                or getattr(item, "index", None) != intent.episode_number
            ):
                return None
        else:
            # movie/show: identity comes from the external ids, built the
            # same way `_search_sync`'s own `wanted` list is below.
            wanted_ids = [
                guid
                for guid in (
                    f"tmdb://{intent.tmdb_id}" if intent.tmdb_id else None,
                    f"tvdb://{intent.tvdb_id}" if intent.tvdb_id else None,
                    f"imdb://{intent.imdb_id}" if intent.imdb_id else None,
                )
                if guid is not None
            ]
            if wanted_ids:
                item_guids = {g.id for g in getattr(item, "guids", [])}
                if item_guids.isdisjoint(wanted_ids):
                    return None
            # else: a full-pass intent always carries whatever external ids
            # the adopted row had, so no ids at all means there is nothing to
            # check beyond the type+library match above, same as before this
            # check existed.

        container = item
        parent_rating_key = None
        if intent.kind in ("season", "episode"):
            # Seasons and episodes have no folder and no agent ids of their
            # own worth trusting; both live on the show, which the GUID search
            # reaches first and a direct fetch has to climb back up to.
            try:
                container = item.show()
            except PlexNotFound:
                return None
            parent_rating_key = (
                str(container.ratingKey)
                if intent.kind == "season"
                else str(item.parentRatingKey) if item.parentRatingKey is not None else None
            )

        file_path = None
        if getattr(container, "media", None):
            parts = container.media[0].parts
            if parts:
                file_path = parts[0].file
        return _RawMatch(
            rating_key=str(item.ratingKey),
            library=section.title,
            title=item.title,
            year=getattr(container, "year", None),
            file_path=file_path,
            item_locations=list(getattr(container, "locations", None) or section.locations),
            section_locations=list(section.locations),
            art_url=getattr(container, "thumb", None),
            guids=[g.id for g in getattr(container, "guids", [])],
            parent_rating_key=parent_rating_key,
            parent_guids=(
                [g.id for g in getattr(container, "guids", [])]
                if intent.kind in ("season", "episode") else []
            ),
            original_title=getattr(item, "originalTitle", None),
            show_title=(
                getattr(container, "title", None) if intent.kind == "season" else None
            ),
        )

    def _search_sync(self, intent: RenderIntent, *, _reread: bool = False) -> _RawMatch | None:
        wanted = []
        if intent.tmdb_id:
            wanted.append(f"tmdb://{intent.tmdb_id}")
        if intent.tvdb_id:
            wanted.append(f"tvdb://{intent.tvdb_id}")
        if intent.imdb_id:
            wanted.append(f"imdb://{intent.imdb_id}")

        # A movie intent can only be a movie; show/season/episode intents all
        # resolve by matching the *show*, so all three need a show library.
        # Mirrors resolve()'s own movie/else split below.
        wanted_type = "movie" if intent.kind == "movie" else "show"
        sections = self._sections(wanted_type)

        if intent.native_id_on("plex"):
            match = self._fetch_by_rating_key_sync(intent, sections)
            if match is not None:
                return match

        for section in sections:
            for guid in wanted:
                # `search(guid=...)` matches only an item's PRIMARY guid, which under
                # the Plex Movie/TV agents is a `plex://` URI — external ids live in
                # the item's `guids` list, so searching for `tvdb://...` there always
                # returns nothing. `getGuid` resolves the external id through the
                # agent and is what actually works against a real library.
                try:
                    item = section.getGuid(guid)
                except PlexNotFound:
                    continue
                if item is not None:
                    if item.type != wanted_type:
                        # A GUID number is only unique *within* one agent's
                        # namespace: TMDB numbers movies and TV separately, so
                        # the same id names a different title in each. In
                        # production an episode intent for tmdb://64677 (the
                        # show's id) matched the movie whose TMDB id is also
                        # 64677, and `item.episode(...)` on it raised
                        # "'Movie' object has no attribute 'episode'" — burning
                        # the job's retries. A wrong-type match is not a match:
                        # keep looking rather than navigating into it.
                        continue

                    # The GUID search always matches the show (or movie). A season/
                    # episode intent must navigate down from there to the item it
                    # actually refers to, so the returned identity — rating key and
                    # title — belongs to that item, not the show.
                    target = item
                    parent_rating_key = None
                    if intent.kind == "season":
                        if intent.season_number is None:
                            # A season intent with no season number cannot be
                            # addressed under the show, so the pipeline provably
                            # cannot resolve it -- absence concluded from the
                            # data, never from a provider error. The episode
                            # branch below carries the full reasoning.
                            return None
                        try:
                            target = item.season(season=intent.season_number)
                        except PlexNotFound:
                            return None
                        parent_rating_key = str(item.ratingKey)
                    elif intent.kind == "episode":
                        if intent.season_number is None or intent.episode_number is None:
                            # An episode intent with no coordinates cannot be
                            # addressed under the show, so the pipeline provably
                            # cannot resolve it -- absence concluded from the
                            # data, never from a provider error. Plex's TV agent
                            # really does hand back `index: None` (see
                            # `render/naming.py::missing_number`, which
                            # documents the year-grouped specials this comes
                            # from and guards the same shape at the naming
                            # call); `Show.episode(season=None, episode=None)`
                            # is a call plexapi refuses by contract, so making
                            # it at all only converts a knowable absence into a
                            # BadRequest that a prune scan reads as a failure.
                            return None
                        try:
                            target = item.episode(
                                season=intent.season_number, episode=intent.episode_number
                            )
                        except PlexNotFound:
                            return None
                        parent_rating_key = (
                            str(target.parentRatingKey)
                            if target.parentRatingKey is not None
                            else None
                        )

                    file_path = None
                    if getattr(item, "media", None):
                        parts = item.media[0].parts
                        if parts:
                            file_path = parts[0].file
                    return _RawMatch(
                        rating_key=str(target.ratingKey),
                        library=section.title,
                        title=target.title,
                        year=getattr(item, "year", None),
                        file_path=file_path,
                        item_locations=list(getattr(item, "locations", None) or section.locations),
                        section_locations=list(section.locations),
                        art_url=getattr(item, "thumb", None),
                        guids=[g.id for g in getattr(item, "guids", [])],
                        parent_rating_key=parent_rating_key,
                        parent_guids=(
                            [g.id for g in getattr(item, "guids", [])]
                            if intent.kind in ("season", "episode") else []
                        ),
                        original_title=getattr(target, "originalTitle", None),
                        show_title=(
                            getattr(item, "title", None)
                            if intent.kind == "season" else None
                        ),
                    )
        # Perf workstream B3: `sections` may be up to SECTIONS_TTL_SECONDS old.
        # An item in a library added since then misses above; re-read once and,
        # only if the list moved, walk again. Every caller -- resolve, the
        # pruner's exists_many -- therefore reaches "not found" only against a
        # list read just now, exactly as before the cache.
        if not _reread and self._sections_changed_on_reread():
            return self._search_sync(intent, _reread=True)
        return None

    async def fetch_item(self, rating_key: str):
        """Fetch the live ``plexapi`` object for a rating key, for writing.

        Distinct from ``resolve()``'s plain-data ``ResolvedItem``: this is
        the object the writer calls ``.batchEdits()``/``.edit()`` on. The
        retry policy lives in ``_fetch_item_with_retries``.

        Inside a worker job (perf workstream B3, ``queue/job_memo.py``) the
        object is memoised by rating key for the rest of the job, so the
        labels read, the metadata write, the badge stage's media read, the
        provenance probe and the upload share one fetch. Every write method
        below evicts it (``_forget``), so a read after a write goes back to
        Plex. Outside a job this is exactly the plain fetch it always was.
        """
        memo = job_memo.current()
        if memo is None:
            return await self._fetch_item_with_retries(rating_key)
        key = (id(self), str(rating_key))
        if key not in memo:
            memo[key] = await self._fetch_item_with_retries(rating_key)
        return memo[key]

    def _forget(self, rating_key) -> None:
        """Drop one item from the running job's memo, after a write to it."""
        memo = job_memo.current()
        if memo is not None:
            memo.pop((id(self), str(rating_key)), None)

    async def _fetch_item_with_retries(self, rating_key: str):
        """Fetch the live ``plexapi`` object for a rating key, for writing.

        Distinct from ``resolve()``'s plain-data ``ResolvedItem``: this is
        the object the writer calls ``.batchEdits()``/``.edit()`` on. It is a
        plain GET (``PlexObject.fetchItem``) — unlike a metadata refresh, no
        agent re-pull is triggered.

        A read timeout or a dropped connection is retried
        ``FETCH_ITEM_RETRIES`` times, waiting ``FETCH_ITEM_BACKOFF_SECONDS``
        between attempts: a Plex that is mid-scan on a fresh import drops
        single reads that succeed seconds later, and one dropped read used to
        cost the item its whole metadata pass. The retry lives at this seam
        rather than in any caller so that all of them inherit it -- the
        pipeline's metadata and badge stages, the artwork modes, the artwork
        and item-override endpoints, the metadata backup. Only
        ``requests.exceptions.Timeout`` and ``requests.exceptions
        .ConnectionError`` are retried: an item Plex does not have
        (``NotFound``) is a settled answer rather than a transient one, and
        everything else -- cancellation included -- propagates at once. After
        the last attempt the ORIGINAL exception re-raises unchanged, so
        ``app._handle_intent``'s attempt budget (which tags the object it
        catches) and the pipeline's "continuing to artwork" containment behave
        exactly as they do today, just four attempts later.
        """
        for attempt in range(FETCH_ITEM_RETRIES + 1):
            try:
                # A lambda, so ``self._server.fetchItem`` is dereferenced ON THE
                # THREAD (perf C2): passing the bound method evaluated it here,
                # and a ``_LazyPlexServer``'s first attribute access connects --
                # a blocking ``PlexServer()`` on the event loop.
                return await asyncio.to_thread(
                    lambda: self._server.fetchItem(int(rating_key))
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt == FETCH_ITEM_RETRIES:
                    raise
                wait = FETCH_ITEM_BACKOFF_SECONDS[attempt]
                # The exception's CLASS, never str(exc): a real requests
                # timeout stringifies to "HTTPSConnectionPool(host=...,
                # port=...): Read timed out." -- the operator's Plex host.
                logger.warning(
                    "plex: fetching item %s failed (%s); retrying in %ss (retry %d of %d)",
                    rating_key, type(exc).__name__, wait, attempt + 1, FETCH_ITEM_RETRIES,
                )
                await _sleep(wait)

    def _list_items_sync(self, wanted_type: str) -> list[SectionItem]:
        items = []
        for section in self._sections(wanted_type):
            # ``includeGuids`` is plexapi's default for `.search()`/`.all()`
            # already (pinned in tests/test_plexapi_list_items_contract.py),
            # so this asks for nothing the listing would not have carried
            # anyway -- it just says so at the call site rather than relying
            # on an upstream default holding. Guids and locations both come
            # back inline in this one request for any item that has them.
            #
            # The residual risk is the item that has *neither*: plexapi's
            # partial-object reload trips on any falsy attribute value, not
            # only an unset one, so a genuinely empty `.guids` (an unmatched
            # Plex item -- exactly the case this endpoint's zero-ids row
            # exists to catch) or `.locations` still costs one extra
            # synchronous HTTP round trip per such item. No listing parameter
            # closes that gap; a real Plex library with unmatched items should
            # get a live timing check post-deploy.
            for item in section.all(includeGuids=True):
                items.append(
                    SectionItem(
                        server="plex",
                        native_id=str(item.ratingKey),
                        library=section.title,
                        title=item.title,
                        year=getattr(item, "year", None),
                        locations=list(getattr(item, "locations", None) or []),
                        guids=parse_guids([g.id for g in getattr(item, "guids", None) or []]),
                    )
                )
        return items

    async def list_items(self, wanted_type: str) -> list[SectionItem]:
        """Every item of one Plex type ("movie"/"show"), across the sections
        this client is allowed to read.

        The library exclusions and the section-type filter come from
        ``_sections``, so a caller cannot walk a library the rest of the
        application does not touch. Listing a section is a blocking call of
        several seconds -- the scheduled arr sync offloads it for exactly this
        reason -- so the whole walk runs in one thread and returns plain data.
        """
        return await asyncio.to_thread(self._list_items_sync, wanted_type)

    def _exists_sync(self, intent: RenderIntent) -> bool:
        return self._search_sync(intent) is not None

    async def exists_many(self, intents: list[RenderIntent]) -> list[bool]:
        """Whether each intent still resolves, answered in the order given.

        The pruner's notion of "gone" (``scheduler/prune.py``), and
        deliberately NOT ``resolve()``: resolve raises ``ItemNotFound`` for two
        states that are not absence at all -- a movie Plex has but has not
        scanned media parts for yet, and an item whose file sits outside every
        library root. A pruner reading either as "gone" would delete rows for
        items Plex still holds. What IS shared with resolve is the half that
        decides existence, ``_search_sync``: the stored rating key first, then
        a GUID search, both constrained to the non-excluded sections of the
        right type. So an item moved into an excluded library reads as gone
        here for exactly the reason it is unreachable to the pipeline.

        One thread for the whole walk rather than one per intent: a
        library-sized sweep is ~16,000 probes, and that many hops through the
        event loop -- shared with the worker pool and the Plex liveness probe
        -- is the stall ``find_orphaned_assets`` offloads its own walk to
        avoid.

        Nothing is caught here. A probe that fails for any reason other than
        "not found" -- the server unreachable mid-walk, a token that stopped
        working -- must reach the caller, because a pruner that read an error
        as absence would delete the library.
        """

        def _walk() -> list[bool]:
            return [self._exists_sync(intent) for intent in intents]

        return await asyncio.to_thread(_walk)

    def _key_resolves_sync(self, intent: RenderIntent) -> bool:
        """Whether ``intent.native_id_on("plex")`` is still the item's own key.

        The wanted-type split is ``_search_sync``'s, verbatim: a movie intent
        can only be a movie; show, season and episode intents all resolve
        through a show library.
        """
        if not intent.native_id_on("plex"):
            return False
        wanted_type = "movie" if intent.kind == "movie" else "show"
        # A fresh list, never the cache: this answers the twin merge's
        # survivor election, and a stale list reading "no" would elect the
        # wrong row of the pair (keys_resolve's own docstring). One listing per
        # probe, as before perf workstream B3.
        sections = self._sections(wanted_type, refresh=True)
        return self._fetch_by_rating_key_sync(intent, sections) is not None

    async def keys_resolve(self, intents: list[RenderIntent]) -> list[bool]:
        """Whether each intent's STORED KEY is still accepted, in the order given.

        The twin merge's survivor election, and deliberately NOT
        ``exists_many``. That method shares ``_search_sync`` with ``resolve``,
        which falls through to the GUID walk on any rating-key refusal -- so a
        re-matched item reads as present under BOTH its stale key and its live
        one, and an election between two rows carrying one identity would be a
        coin toss. This asks ``_fetch_by_rating_key_sync`` and stops there: it
        answers "is this row's key the item's key", which is exactly the
        question that decides which of a twin pair survives.

        One thread for the whole walk, like ``exists_many`` and for the same
        reason: this loop shares the event loop with the worker pool and the
        Plex liveness probe.

        Nothing is caught. A probe that fails for any reason other than "not
        found" must reach the caller, because a merge that read an error as a
        refusal would delete the wrong row of the pair.
        """

        def _walk() -> list[bool]:
            return [self._key_resolves_sync(intent) for intent in intents]

        return await asyncio.to_thread(_walk)

    async def resolve(self, intent: RenderIntent) -> ResolvedItem:
        match = await asyncio.to_thread(self._search_sync, intent)
        if match is None:
            raise ItemNotFound(
                f"no Plex item for {intent.kind} {intent.title!r} "
                f"(tmdb={intent.tmdb_id}, tvdb={intent.tvdb_id})"
            )

        guids = parse_guids(match.guids)
        parent_guids = parse_guids(match.parent_guids)
        file_path = match.file_path

        if intent.kind == "movie":
            if not file_path:
                raise ItemNotFound(f"Plex item {match.rating_key} has no media parts yet")
            target_path = file_path
            is_directory = False
        else:
            target_path = file_path or match.item_locations[0]
            is_directory = True

        root_folder = None
        for library_root in match.section_locations:
            try:
                root_folder = derive_root_folder(library_root, target_path, is_directory=is_directory)
                break
            except ValueError:
                continue
        if root_folder is None:
            raise PlexPathMismatch(
                f"Plex item {match.rating_key} ({target_path!r}) is not inside any of the "
                f"library roots {match.section_locations!r} for library {match.library!r}"
            )

        return ResolvedItem(
            server="plex",
            native_id=match.rating_key,
            library=match.library,
            kind=intent.kind,
            title=match.title,
            year=match.year,
            season_number=intent.season_number,
            episode_number=intent.episode_number,
            root_folder=root_folder,
            file_path=file_path,
            art_url=match.art_url,
            tmdb_id=as_int(guids.get("tmdb")) or intent.tmdb_id,
            tvdb_id=as_int(guids.get("tvdb")) or intent.tvdb_id,
            imdb_id=guids.get("imdb") or intent.imdb_id,
            parent_native_id=match.parent_rating_key,
            parent_tmdb_id=as_int(parent_guids.get("tmdb")),
            parent_tvdb_id=as_int(parent_guids.get("tvdb")),
            parent_imdb_id=parent_guids.get("imdb"),
            original_title=match.original_title,
            show_title=match.show_title,
        )

    async def fetch_ref(self, native_id: str) -> ServerItemRef | None:
        """The ``ServerItemRef`` for a rating key, or ``None`` if Plex no longer has it.

        Built off ``fetch_item`` -- the same plain GET ``resolve()``'s writer
        callers use -- rather than a fresh search, since a native id is already
        as precise an address as Plex offers.
        """
        try:
            item = await self.fetch_item(native_id)
        except PlexNotFound:
            return None
        return ServerItemRef(
            "plex", str(item.ratingKey),
            getattr(item, "librarySectionTitle", ""), getattr(item, "type", ""),
        )

    async def upload_artwork(self, ref: ServerItemRef, data: bytes, art_kind: str, lock: bool) -> None:
        item = await self.fetch_item(ref.native_id)
        try:
            await asyncio.to_thread(plex_artwork.upload_artwork, item, data, art_kind, lock)
        finally:
            # A write, finished or not: the memoised object no longer describes
            # Plex (perf workstream B3).
            self._forget(ref.native_id)

    async def upload_logo(self, ref: ServerItemRef, data: bytes, suffix: str = ".png") -> str | None:
        item = await self.fetch_item(ref.native_id)
        try:
            return await asyncio.to_thread(plex_artwork.upload_logo, item, data, suffix)
        finally:
            self._forget(ref.native_id)

    async def clear_logo(self, ref: ServerItemRef) -> None:
        item = await self.fetch_item(ref.native_id)
        try:
            await asyncio.to_thread(plex_artwork.clear_logo, item)
        finally:
            self._forget(ref.native_id)

    async def has_clearlogo(self, ref: ServerItemRef) -> bool:
        item = await self.fetch_item(ref.native_id)
        return await plex_artwork.has_clearlogo(item)

    async def fetch_artwork(self, ref: ServerItemRef, art_kind: str) -> tuple[bytes, str] | None:
        item = await self.fetch_item(ref.native_id)
        return await plex_artwork.fetch_artwork(
            self._http, item, self.base_url, self._headers, art_kind,
        )

    async def artwork_provenance(self, ref: ServerItemRef, art_kind: str) -> str | None:
        item = await self.fetch_item(ref.native_id)
        return await plex_artwork.artwork_provenance(
            self._http, item, base_url=self.base_url, headers=self._headers, art_kind=art_kind,
        )

    async def reset_artwork_to_agent_default(self, ref: ServerItemRef, art_kind: str) -> bool:
        item = await self.fetch_item(ref.native_id)
        try:
            return await asyncio.to_thread(
                plex_artwork.reset_artwork_to_agent_default, item, art_kind
            )
        finally:
            self._forget(ref.native_id)

    async def item_labels(self, ref: ServerItemRef) -> list[str]:
        item = await self.fetch_item(ref.native_id)
        return await asyncio.to_thread(_label_tags, item)

    async def apply_facts(
        self, ref: ServerItemRef, facts, operations=None,
        parental_categories=None, overrides=None,
    ) -> dict:
        # Imported here, not at module scope: plex/writer.py pulls in
        # facts/gather.py, which imports ResolvedItem back off this module --
        # a module-level import here would deadlock that cycle on load.
        from autoposter.plex.writer import apply_facts as plex_apply_facts

        item = await self.fetch_item(ref.native_id)
        try:
            edits = await plex_apply_facts(item, facts, operations, parental_categories, overrides)
        except BaseException:
            self._forget(ref.native_id)
            raise
        # An empty plan wrote nothing (plex/writer.apply_facts returns before
        # batchEdits), so the memoised object still describes Plex and the
        # badge stage may reuse it; any write evicts (perf workstream B3).
        if edits:
            self._forget(ref.native_id)
        return edits

    async def check_liveness(self) -> bool:
        """Whether the configured Plex server answers at all.

        ``True`` with no ``http`` client configured: a caller in that shape
        (the conformance suite's Plex arm, today) has no way to probe over
        HTTP and no business asserting the server is down.
        """
        if self._http is None:
            return True
        try:
            response = await self._http.get(f"{self.base_url}/identity")
            return response.is_success
        except Exception:
            return False
