"""The seam between the pipeline and any media server.

``ResolvedItem``, ``SectionItem``, ``ItemNotFound`` and ``PathMismatch`` used to
live in plex/client.py. They are server-neutral by content and were only Plex by
address, so they move here; plex/client.py re-exports them and keeps its own
``PlexPathMismatch`` subclass for the worker's except ladder.

Every artwork and metadata operation takes a ``ServerItemRef`` -- never a
client-native object. That is the one place the old boundary leaked (the
Plex client's fetch_item returned a plexapi item that plex/artwork.py's
functions consumed directly); closing it is what makes a second server
possible at all. Spec: docs/design/2026-09-12-jellyfin-media-server-design.md §3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Capability names (spec §3.4). Callers CHECK these; they never discover them
# by catching UnsupportedOnServer.
CAP_LOCK_ARTWORK = "lock_artwork"
CAP_LOGO_UPLOAD = "logo_upload"
CAP_LOGO_UPLOAD_KEY = "logo_upload_key"
CAP_FIELD_LOCKS = "field_locks"
CAP_ARTWORK_PROVENANCE = "artwork_provenance"
CAP_TITLE_CARD_URL = "title_card_url"
CAP_RESET_TO_AGENT_DEFAULT = "reset_to_agent_default"

LABELS = {"plex": "Plex", "jellyfin": "Jellyfin"}


class ItemNotFound(Exception):
    """The server has no matching item -- usually it has not scanned the file yet."""

    served_detail = True


class PathMismatch(ItemNotFound):
    """The item resolved, but its file path maps into none of the library's
    roots. Not a scan-in-progress wait: a path-mapping mismatch that no
    retry fixes (queue/worker.py catches it ahead of ItemNotFound)."""


class UnsupportedOnServer(Exception):
    def __init__(self, server: str, capability: str):
        self.server = server
        self.capability = capability
        super().__init__(f"{LABELS.get(server, server)} does not support {capability}")


@dataclass(frozen=True)
class ServerItemRef:
    """The only handle that crosses the boundary."""

    server: str
    native_id: str
    library: str
    kind: str


@dataclass(frozen=True)
class ResolvedItem:
    server: str
    native_id: str
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
    parent_native_id: str | None = None
    # The parent's provider ids, so an episode's parent can be resolved by
    # identity key rather than by a server key (spec §4.2).
    parent_tmdb_id: int | None = None
    parent_tvdb_id: int | None = None
    parent_imdb_id: str | None = None
    original_title: str | None = None
    show_title: str | None = None

    @property
    def ref(self) -> ServerItemRef:
        return ServerItemRef(self.server, self.native_id, self.library, self.kind)


@dataclass(frozen=True)
class SectionItem:
    server: str
    native_id: str
    library: str
    title: str
    year: int | None
    locations: list[str]
    guids: dict[str, str]


@runtime_checkable
class MediaServer(Protocol):
    name: str
    capabilities: frozenset[str]

    async def resolve(self, intent) -> ResolvedItem: ...
    async def fetch_ref(self, native_id: str) -> ServerItemRef | None: ...
    async def exists_many(self, intents: list) -> list[bool]: ...
    async def keys_resolve(self, intents: list) -> list[bool]: ...
    async def list_items(self, kind: str) -> list[SectionItem]: ...

    async def upload_artwork(self, ref: ServerItemRef, data: bytes, art_kind: str, lock: bool) -> None: ...
    async def upload_logo(self, ref: ServerItemRef, data: bytes, suffix: str = ".png") -> str | None: ...
    async def clear_logo(self, ref: ServerItemRef) -> None: ...
    async def has_clearlogo(self, ref: ServerItemRef) -> bool: ...
    async def fetch_artwork(self, ref: ServerItemRef, art_kind: str) -> bytes | None: ...
    async def artwork_provenance(self, ref: ServerItemRef, art_kind: str) -> str | None: ...
    async def reset_artwork_to_agent_default(self, ref: ServerItemRef, art_kind: str) -> bool: ...

    async def apply_facts(self, ref: ServerItemRef, facts, operations=None,
                          parental_categories=None, overrides=None) -> dict: ...

    async def check_liveness(self) -> bool: ...
