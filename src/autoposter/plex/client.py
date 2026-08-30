import asyncio
import re
from dataclasses import dataclass

from plexapi.exceptions import NotFound as PlexNotFound

from autoposter.intake.arr import RenderIntent
from autoposter.render.naming import derive_root_folder

_GUID_RE = re.compile(r"^(?:com\.plexapp\.agents\.)?(tmdb|imdb|tvdb)://([^?]+)")


class ItemNotFound(Exception):
    """Plex has no matching item — usually it has not scanned the file yet."""


class PlexPathMismatch(ItemNotFound):
    """The item resolved in Plex, but its file path maps into none of the
    library's roots.

    Unlike the class it subclasses, this is not a scan-in-progress wait -- it
    is a path-mapping mismatch between this container's view of the
    filesystem and Plex's, which no amount of retrying fixes. Raised only at
    the third ``resolve()`` raise site; see ``queue/worker.py``'s ordered
    except clause for how this is kept off the unbounded defer path.
    """


@dataclass(frozen=True)
class ResolvedItem:
    rating_key: str
    library: str
    kind: str
    title: str
    year: int | None
    season_number: int | None
    episode_number: int | None
    root_folder: str
    file_path: str | None
    art_url: str | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    parent_rating_key: str | None = None


@dataclass(frozen=True)
class SectionItem:
    """One movie or show in a library section, as plain data.

    What ``list_items`` returns: nothing here is a ``plexapi`` object, for the
    same reason ``_RawMatch`` is not one -- reading an attribute back on the
    event loop can trigger a synchronous HTTP reload.

    ``locations`` is the item's own, not the section's: a movie's is its file
    and a show's is its directory, which is exactly what ``arr.sync``'s
    ``source_path`` expects to be handed.
    """

    rating_key: str
    library: str
    title: str
    year: int | None
    locations: list[str]
    guids: dict[str, str]


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
# across chunk sizes -- .superpowers/sdd/archive/p9a-task-2-report.md) and
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
    ``language`` display title (probe decision D6 -- the production server
    carries ``language``, ``languageCode`` and ``languageTag`` on both audio
    and subtitle streams, so no adjustment is called for). ``Media``,
    ``MediaPart`` and streams are plain PlexObjects with no reload guard of
    their own (``filter_values._resolutions`` documents the same)."""
    found = []
    for media in _safe_attr(item, "media") or []:
        for part in getattr(media, "parts", None) or []:
            for stream in getattr(part, "streams", None) or []:
                if getattr(stream, "streamType", None) == stream_type:
                    found.append(getattr(stream, "language", None))
    return _uniq(found)


def _iter_metadata_batches(section, rating_keys, chunk_size):
    """Full metadata for the given keys, ``ceil(N/chunk)`` fetches exactly.

    The seam is plexapi's own list-of-ints translation: ``fetchItems`` turns a
    list of ints into ``/library/metadata/{k1,k2,...}`` (base.py:334-335). A
    non-numeric key raises ValueError: rating keys handed here come off
    plexapi items and are always numeric, so a failure to parse is a caller
    bug, not a library state. BLOCKING -- callers on the event loop go
    through ``asyncio.to_thread`` (``collections/enrichment.py`` does).
    """
    keys = [int(key) for key in rating_keys]
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


class PlexClient:
    """Resolves render intents to Plex items.

    ``plexapi`` is synchronous, so calls run in a thread to keep the event loop free.
    All ``plexapi`` attribute access happens inside that thread — attributes on
    partial objects can trigger a synchronous HTTP reload, so nothing touched back
    on the event loop may be a ``plexapi`` object.
    """

    def __init__(self, server, excluded_libraries: list[str]):
        self._server = server
        self._excluded = set(excluded_libraries)

    def _sections(self, wanted_type: str):
        """The non-excluded library sections of one Plex type ("movie"/"show").

        Constraining the walk by type is what keeps a movie library from
        answering a show-shaped intent — see the GUID-namespace note in
        `_search_sync`.
        """
        # plexapi exposes `library` as a property and `sections` as a method.
        return [
            s
            for s in self._server.library.sections()
            if s.title not in self._excluded and s.type == wanted_type
        ]

    def _fetch_by_rating_key_sync(self, intent: RenderIntent, sections) -> _RawMatch | None:
        """The item named by ``intent.rating_key``, or None to fall back.

        Adoption stored every item's exact Plex identity in
        ``media_items.rating_key``, so an intent built from such a row does not
        need an agent lookup at all. It also must not use one: adoption stored
        each episode's OWN external ids, while `_search_sync` reads an episode
        intent's ids as the *series'* ids -- true on the webhook path, where
        Sonarr supplies them, and false for every adopted row. Episode-level
        ids either match nothing (the job retries as "waiting for Plex" until
        it parks) or match an unrelated item that happens to carry the same
        number.

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
            item = self._server.fetchItem(int(intent.rating_key))  # type: ignore[arg-type]
        except (PlexNotFound, TypeError, ValueError):
            # NotFound: the key names nothing any more. TypeError/ValueError:
            # `rating_key` is a text column, so a row can hold a non-number.
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
        )

    def _search_sync(self, intent: RenderIntent) -> _RawMatch | None:
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

        if intent.rating_key:
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
                    )
        return None

    async def fetch_item(self, rating_key: str):
        """Fetch the live ``plexapi`` object for a rating key, for writing.

        Distinct from ``resolve()``'s plain-data ``ResolvedItem``: this is
        the object the writer calls ``.batchEdits()``/``.edit()`` on. It is a
        plain GET (``PlexObject.fetchItem``) — unlike a metadata refresh, no
        agent re-pull is triggered.
        """
        return await asyncio.to_thread(self._server.fetchItem, int(rating_key))

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
                        rating_key=str(item.ratingKey),
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

    async def resolve(self, intent: RenderIntent) -> ResolvedItem:
        match = await asyncio.to_thread(self._search_sync, intent)
        if match is None:
            raise ItemNotFound(
                f"no Plex item for {intent.kind} {intent.title!r} "
                f"(tmdb={intent.tmdb_id}, tvdb={intent.tvdb_id})"
            )

        guids = parse_guids(match.guids)
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
            rating_key=match.rating_key,
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
            parent_rating_key=match.parent_rating_key,
        )
