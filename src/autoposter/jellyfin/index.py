"""Index-first resolution (spec §4.4): 12.0 has no provider-id query.

``LibraryIndex`` builds an in-memory index of every movie and series (by
``JellyfinApi.items`` -- docs/reference/2026-09-jellyfin-openapi-12.md,
"/Items" -> get) up front, then resolves an intent from that index. A miss
costs exactly one narrow search (same doc, "/Items" -> get with
``searchTerm``) before giving up, rather than searching on every resolve.

Identity ruling (Phase 2 identity corrections, 2026-09-12): a season or
episode's own ``tmdb_id``/``tvdb_id``/``imdb_id`` -- and its ``parent_*``
ids -- are always the SERIES' ``ProviderIds`` (never a season/episode's own),
because Jellyfin's TV agent only ever writes real provider ids onto the
series. ``parent_native_id`` is the series' id for a season and the SEASON's
id for an episode, matching the app's two-hop parent model
(``autoposter.servers.identity.parent_identity_key_for``). ``root_folder``
for a season/episode is always the SERIES folder's basename, and
``file_path`` is None for both -- mirroring the Plex resolver, which rebinds
its match container to the show for a season/episode intent and reads
file_path off that (always None there too). Together this makes a
Jellyfin-resolved item compute the same ``identity_key`` as its Plex twin at
the same coordinates (tests/test_jellyfin_index.py). A movie whose two
servers pick a different underlying file would still key differently under
this rule -- that case is ruled into Task 19 (one intent maps to one row,
and the second server contributes only its ref, never a second identity).

Basename lookup deferred: spec §4.4 step 2 describes falling back to a
by-basename index entry on a provider-id miss, but ``RenderIntent`` carries
no path for this index to match against, so nothing can ever call it
(ledgered).

``kind_of`` returns ``""`` for a ``Type`` this module does not recognise
(e.g. ``BoxSet``), and such a dto is never indexed under any kind -- a
collection sharing a movie's provider id must never be filed as that movie.
"""
from __future__ import annotations

import asyncio

from autoposter.render.naming import derive_root_folder  # the same helper plex/client.py uses
from autoposter.servers.base import ItemNotFound, PathMismatch, ResolvedItem, SectionItem

KIND_OF = {"Movie": "movie", "Series": "show", "Season": "season", "Episode": "episode"}
TYPE_OF = {"movie": "Movie", "show": "Series"}


def _pid(dto: dict, ns: str):
    # ProviderIds keys are compared case-insensitively: live servers send
    # Tmdb/Imdb/Tvdb/TmdbCollection, not necessarily this module's own casing.
    ids = dto.get("ProviderIds") or {}
    for k, v in ids.items():
        if k.lower() == ns:
            return v
    return None


def _as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class LibraryIndex:
    def __init__(self, api, excluded: set[str], library_map: dict[str, str] | None = None):
        self._api = api
        # Matched on the raw Jellyfin folder name -- a different name space
        # from `_map` below, and on purpose: exclusion happens before any
        # translation to the Plex-facing name.
        self._excluded = set(excluded)
        # config/schema.py defines library_map as Plex name -> Jellyfin name.
        # Every folder this index sees carries its JELLYFIN name, so the
        # lookup direction is inverted here, once, rather than on every
        # resolve: this is jellyfin name -> Plex name.
        self._map = {jellyfin_name: plex_name for plex_name, jellyfin_name in dict(library_map or {}).items()}
        self._folders: list[dict] = []          # VirtualFolders, filtered
        self._by_key: dict[tuple, dict] = {}    # (kind, ns, value) -> dto
        self._by_id: dict[str, dict] = {}
        self._children: dict[str, tuple[list[dict], list[dict]]] = {}  # series id -> (seasons, episodes)
        self.built = False
        self._lock = asyncio.Lock()

    # --- shape helpers the client also uses ---
    def kind_of(self, dto: dict) -> str:
        return KIND_OF.get(dto.get("Type", ""), "")

    def _folder_of(self, dto: dict) -> dict | None:
        path = dto.get("Path") or ""
        for f in self._folders:
            if any(path.startswith(loc.rstrip("/") + "/") or path == loc for loc in f.get("Locations") or []):
                return f
        return None

    def library_of(self, dto: dict) -> str:
        folder = self._folder_of(dto)
        if folder is None:
            return ""
        name = folder["Name"]
        return self._map.get(name, name)

    # --- build ---
    async def rebuild(self) -> None:
        async with self._lock:
            if self.built:
                # Another caller already won the race to build (or this is a
                # redundant explicit call); `invalidate()` is how a caller
                # asks for a real rebuild.
                return
            # capture -> "/Library/VirtualFolders" -> get
            folders = [f for f in await self._api.virtual_folders()
                       if f.get("Name") not in self._excluded and f.get("CollectionType") in ("movies", "tvshows")]
            by_key, by_id = {}, {}
            for f in folders:
                types = "Movie" if f["CollectionType"] == "movies" else "Series"
                # capture -> "/Items" -> get
                for dto in await self._api.items(parentId=f["ItemId"], recursive="true",
                                                 includeItemTypes=types, fields="ProviderIds,Path"):
                    self._index_one(dto, by_key, by_id)
            # All-or-nothing: nothing below this line runs if any await above
            # raised, so a failed build leaves the previous index (or the
            # empty one) exactly as it was, and `built` untouched.
            self._folders, self._by_key, self._by_id, self._children = folders, by_key, by_id, {}
            self.built = True

    def _index_one(self, dto: dict, by_key: dict, by_id: dict) -> None:
        kind = self.kind_of(dto)
        if not kind:
            # An unrecognised Type (e.g. BoxSet) is never indexed under any
            # kind, even when it happens to share a provider id with a real
            # movie or series.
            return
        by_id[dto["Id"]] = dto
        for ns in ("tmdb", "tvdb", "imdb"):
            v = _pid(dto, ns)
            if v:
                by_key.setdefault((kind, ns, str(v)), dto)

    def invalidate(self) -> None:
        self.built = False

    # --- lookup ---
    async def _top_level(self, intent) -> dict | None:
        if not self.built:
            await self.rebuild()
        kind = "movie" if intent.kind == "movie" else "show"
        for ns, v in (("tmdb", intent.tmdb_id), ("tvdb", intent.tvdb_id), ("imdb", intent.imdb_id)):
            if v and (dto := self._by_key.get((kind, ns, str(v)))):
                return dto
        return None

    async def _search_once(self, intent) -> dict | None:
        kind = "movie" if intent.kind == "movie" else "show"
        # capture -> "/Items" -> get, with searchTerm: the one narrow search
        # a miss is allowed before giving up.
        query = dict(searchTerm=intent.title, includeItemTypes=TYPE_OF[kind], recursive="true", fields="ProviderIds,Path")
        if intent.year:
            query["years"] = str(intent.year)
        for dto in await self._api.items(**query):
            if self.kind_of(dto) != kind:
                # includeItemTypes should already have filtered this out;
                # never trust a hit's kind without checking (a BoxSet must
                # never be taken for the movie it collects).
                continue
            for ns, v in (("tmdb", intent.tmdb_id), ("tvdb", intent.tvdb_id), ("imdb", intent.imdb_id)):
                if v and str(_pid(dto, ns)) == str(v):
                    self._index_one(dto, self._by_key, self._by_id)
                    return dto
        return None

    async def _children_of(self, series_id: str):
        if series_id in self._children:
            return self._children[series_id]
        async with self._lock:
            if series_id in self._children:
                return self._children[series_id]
            # capture -> "/Shows/{seriesId}/Seasons" and "/Shows/{seriesId}/Episodes" -> get
            self._children[series_id] = (await self._api.seasons(series_id), await self._api.episodes(series_id))
        return self._children[series_id]

    async def _find(self, intent) -> tuple[dict, dict | None, dict | None]:
        """(the item's dto, its series dto or None, the item's season dto or None).

        The season dto is only ever populated for an episode intent -- it is
        what supplies ``parent_native_id`` there (the two-hop model: an
        episode's immediate parent is its season, not the series).
        """
        top = await self._top_level(intent) or await self._search_once(intent)
        if top is None:
            raise ItemNotFound(f"no Jellyfin item for {intent.kind} {intent.title!r} (tmdb={intent.tmdb_id}, tvdb={intent.tvdb_id})")
        if intent.kind in ("movie", "show"):
            return top, None, None
        seasons, episodes = await self._children_of(top["Id"])
        if intent.kind == "season":
            for s in seasons:
                if s.get("IndexNumber") == intent.season_number:
                    return s, top, s
        else:
            season = next((s for s in seasons if s.get("IndexNumber") == intent.season_number), None)
            for e in episodes:
                if e.get("ParentIndexNumber") == intent.season_number and e.get("IndexNumber") == intent.episode_number:
                    return e, top, season
        raise ItemNotFound(f"Jellyfin has {top.get('Name')!r} but not S{intent.season_number}E{intent.episode_number}")

    async def resolve(self, intent) -> ResolvedItem:
        dto, top, season = await self._find(intent)
        container = top or dto
        folder = self._folder_of(container)
        library = self._map.get(folder["Name"], folder["Name"]) if folder else ""

        if intent.kind == "movie":
            target = dto.get("Path")
            if not target:
                raise ItemNotFound(f"Jellyfin item {dto['Id']} has no path yet")
            is_directory = False
            file_path = target
            ids_source = dto
            parent_native_id = parent_tmdb_id = parent_tvdb_id = parent_imdb_id = None
        elif intent.kind == "show":
            target = dto.get("Path") or ""
            is_directory = True
            file_path = None
            ids_source = dto
            parent_native_id = parent_tmdb_id = parent_tvdb_id = parent_imdb_id = None
        else:
            # season or episode: everything but the coordinates comes off the
            # SERIES (top), never off the season/episode's own dto.
            target = top.get("Path") or ""
            is_directory = True
            file_path = None
            ids_source = top
            parent_native_id = top["Id"] if intent.kind == "season" else (season["Id"] if season else None)
            parent_tmdb_id = _as_int(_pid(top, "tmdb"))
            parent_tvdb_id = _as_int(_pid(top, "tvdb"))
            parent_imdb_id = _pid(top, "imdb")

        root_folder = None
        for loc in (folder or {}).get("Locations") or []:
            try:
                root_folder = derive_root_folder(loc, target, is_directory=is_directory)
                break
            except ValueError:
                continue
        if root_folder is None:
            raise PathMismatch(f"Jellyfin item {dto['Id']} ({target!r}) is not inside any root of library {library!r}")

        return ResolvedItem(
            server="jellyfin", native_id=dto["Id"], library=library, kind=intent.kind,
            title=dto.get("Name") or intent.title, year=ids_source.get("ProductionYear") or intent.year,
            season_number=intent.season_number, episode_number=intent.episode_number,
            root_folder=root_folder, file_path=file_path, art_url=None,
            tmdb_id=_as_int(_pid(ids_source, "tmdb")) or intent.tmdb_id,
            tvdb_id=_as_int(_pid(ids_source, "tvdb")) or intent.tvdb_id,
            imdb_id=_pid(ids_source, "imdb") or intent.imdb_id,
            parent_native_id=parent_native_id,
            parent_tmdb_id=parent_tmdb_id,
            parent_tvdb_id=parent_tvdb_id,
            parent_imdb_id=parent_imdb_id,
            show_title=top.get("Name") if (top and intent.kind == "season") else None,
        )

    async def exists(self, intent) -> bool:
        try:
            await self._find(intent)
            return True
        except ItemNotFound:
            return False

    async def list_items(self, kind: str) -> list[SectionItem]:
        if not self.built:
            await self.rebuild()
        want = "movie" if kind == "movie" else "show"
        out = []
        for dto in self._by_id.values():
            if self.kind_of(dto) != want:
                continue
            guids = {ns: str(v) for ns in ("tmdb", "tvdb", "imdb") if (v := _pid(dto, ns))}
            out.append(SectionItem(server="jellyfin", native_id=dto["Id"], library=self.library_of(dto),
                                   title=dto.get("Name", ""), year=dto.get("ProductionYear"),
                                   locations=[dto["Path"]] if dto.get("Path") else [], guids=guids))
        return out
