"""One MediaServer double for every suite (spec §10.1).

Not collected: no ``test_`` prefix. Records every write so a test asserts
``server.uploads == [...]`` rather than mocking a method.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_FIELD_LOCKS, CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD,
    CAP_LOGO_UPLOAD_KEY, CAP_RESET_TO_AGENT_DEFAULT, CAP_TITLE_CARD_URL,
    ItemNotFound, PathMismatch, ResolvedItem, SectionItem, ServerItemRef, UnsupportedOnServer,
)

PLEX_CAPS = frozenset({
    CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD, CAP_LOGO_UPLOAD_KEY, CAP_FIELD_LOCKS,
    CAP_ARTWORK_PROVENANCE, CAP_TITLE_CARD_URL, CAP_RESET_TO_AGENT_DEFAULT,
})
JELLYFIN_CAPS = frozenset({CAP_LOGO_UPLOAD, CAP_FIELD_LOCKS, CAP_ARTWORK_PROVENANCE})


def resolved(server: str, native_id: str, *, kind: str = "movie", library: str = "Movies",
             title: str = "Title", year: int | None = 2020, tmdb_id: int | None = 1,
             tvdb_id: int | None = None, imdb_id: str | None = None,
             season_number: int | None = None, episode_number: int | None = None,
             file_path: str | None = "/media/Movies/Title (2020)/Title (2020).mkv",
             root_folder: str = "Title (2020)", parent_native_id: str | None = None) -> ResolvedItem:
    return ResolvedItem(
        server=server, native_id=native_id, library=library, kind=kind, title=title,
        year=year, season_number=season_number, episode_number=episode_number,
        root_folder=root_folder, file_path=file_path, art_url=None,
        tmdb_id=tmdb_id, tvdb_id=tvdb_id, imdb_id=imdb_id, parent_native_id=parent_native_id,
    )


@dataclass
class FakeMediaServer:
    name: str = "plex"
    capabilities: frozenset[str] = PLEX_CAPS
    #: dedupe_key -> the item resolve() answers with
    items: dict[str, ResolvedItem] = field(default_factory=dict)
    not_found: set[str] = field(default_factory=set)
    path_mismatch: set[str] = field(default_factory=set)
    raise_on_upload: Exception | None = None
    healthy: bool = True
    uploads: list[tuple[ServerItemRef, bytes, str, bool]] = field(default_factory=list)
    logo_uploads: list[tuple[ServerItemRef, bytes]] = field(default_factory=list)
    cleared_logos: list[ServerItemRef] = field(default_factory=list)
    facts_written: list[tuple[ServerItemRef, object]] = field(default_factory=list)
    resolve_calls: int = 0

    def _require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedOnServer(self.name, capability)

    async def resolve(self, intent) -> ResolvedItem:
        self.resolve_calls += 1
        key = intent.dedupe_key
        if key in self.path_mismatch:
            raise PathMismatch(f"{self.name}: {intent.title!r} is outside every library root")
        if key in self.not_found or key not in self.items:
            raise ItemNotFound(f"no {self.name} item for {intent.kind} {intent.title!r}")
        return self.items[key]

    async def fetch_ref(self, native_id: str) -> ServerItemRef | None:
        for item in self.items.values():
            if item.native_id == native_id:
                return item.ref
        return None

    async def exists_many(self, intents) -> list[bool]:
        return [i.dedupe_key in self.items and i.dedupe_key not in self.not_found for i in intents]

    async def keys_resolve(self, intents) -> list[bool]:
        return [
            any(it.native_id == (i.refs or {}).get(self.name) for it in self.items.values())
            for i in intents
        ]

    async def list_items(self, kind: str) -> list[SectionItem]:
        return [
            SectionItem(server=self.name, native_id=i.native_id, library=i.library, title=i.title,
                        year=i.year, locations=[i.file_path] if i.file_path else [],
                        guids={k: str(v) for k, v in (("tmdb", i.tmdb_id), ("tvdb", i.tvdb_id), ("imdb", i.imdb_id)) if v})
            for i in self.items.values() if i.kind == ("movie" if kind == "movie" else "show")
        ]

    async def upload_artwork(self, ref, data, art_kind, lock) -> None:
        if lock:
            self._require(CAP_LOCK_ARTWORK)
        if self.raise_on_upload is not None:
            raise self.raise_on_upload
        self.uploads.append((ref, data, art_kind, lock))

    async def upload_logo(self, ref, data, suffix=".png") -> str | None:
        self._require(CAP_LOGO_UPLOAD)
        self.logo_uploads.append((ref, data))
        return f"upload://{ref.native_id}" if CAP_LOGO_UPLOAD_KEY in self.capabilities else None

    async def clear_logo(self, ref) -> None:
        self._require(CAP_LOGO_UPLOAD)
        self.cleared_logos.append(ref)

    async def has_clearlogo(self, ref) -> bool:
        return any(r == ref for r, _ in self.logo_uploads)

    async def fetch_artwork(self, ref, art_kind) -> tuple[bytes, str] | None:
        for r, data, kind, _ in reversed(self.uploads):
            if r == ref and kind == art_kind:
                # Content type is fixed rather than tracked per upload: nothing
                # this double's callers assert on varies it.
                return data, "image/png"
        return None

    async def artwork_provenance(self, ref, art_kind) -> str | None:
        self._require(CAP_ARTWORK_PROVENANCE)
        return None

    async def reset_artwork_to_agent_default(self, ref, art_kind) -> bool:
        self._require(CAP_RESET_TO_AGENT_DEFAULT)
        return True

    async def apply_facts(self, ref, facts, operations=None, parental_categories=None, overrides=None) -> dict:
        self.facts_written.append((ref, facts))
        return {}

    async def check_liveness(self) -> bool:
        return self.healthy
