import asyncio
import re
from dataclasses import dataclass

from autoposter.intake.arr import RenderIntent
from autoposter.render.naming import derive_root_folder

_GUID_RE = re.compile(r"^(?:com\.plexapp\.agents\.)?(tmdb|imdb|tvdb)://([^?]+)")


class ItemNotFound(Exception):
    """Plex has no matching item — usually it has not scanned the file yet."""


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


def parse_guids(guids: list[str]) -> dict[str, str]:
    """Map Plex GUID strings to ``{agent: id}``, tolerating legacy agent prefixes."""
    parsed = {}
    for guid in guids:
        match = _GUID_RE.match(guid)
        if match:
            parsed[match.group(1)] = match.group(2)
    return parsed


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class PlexClient:
    """Resolves render intents to Plex items.

    ``plexapi`` is synchronous, so calls run in a thread to keep the event loop free.
    """

    def __init__(self, server, excluded_libraries: list[str]):
        self._server = server
        self._excluded = set(excluded_libraries)

    def _sections(self):
        return [s for s in self._server.library().sections() if s.title not in self._excluded]

    def _search_sync(self, intent: RenderIntent):
        wanted = []
        if intent.tmdb_id:
            wanted.append(f"tmdb://{intent.tmdb_id}")
        if intent.tvdb_id:
            wanted.append(f"tvdb://{intent.tvdb_id}")
        if intent.imdb_id:
            wanted.append(f"imdb://{intent.imdb_id}")

        for section in self._sections():
            for guid in wanted:
                results = section.search(guid=guid)
                if results:
                    return section, results[0]
        return None, None

    async def resolve(self, intent: RenderIntent) -> ResolvedItem:
        section, item = await asyncio.to_thread(self._search_sync, intent)
        if item is None:
            raise ItemNotFound(
                f"no Plex item for {intent.kind} {intent.title!r} "
                f"(tmdb={intent.tmdb_id}, tvdb={intent.tvdb_id})"
            )

        guids = parse_guids([g.id for g in getattr(item, "guids", [])])
        file_path = None
        if getattr(item, "media", None):
            parts = item.media[0].parts
            if parts:
                file_path = parts[0].file

        library_root = section.locations[0]
        if intent.kind == "movie":
            if not file_path:
                raise ItemNotFound(f"Plex item {item.ratingKey} has no media parts yet")
            root_folder = derive_root_folder(library_root, file_path, is_directory=False)
        else:
            show_path = file_path or getattr(item, "locations", [library_root])[0]
            root_folder = derive_root_folder(library_root, show_path, is_directory=True)

        return ResolvedItem(
            rating_key=str(item.ratingKey),
            library=section.title,
            kind=intent.kind,
            title=item.title,
            year=getattr(item, "year", None),
            season_number=intent.season_number,
            episode_number=intent.episode_number,
            root_folder=root_folder,
            file_path=file_path,
            art_url=getattr(item, "thumb", None),
            tmdb_id=_as_int(guids.get("tmdb")) or intent.tmdb_id,
            tvdb_id=_as_int(guids.get("tvdb")) or intent.tvdb_id,
            imdb_id=guids.get("imdb") or intent.imdb_id,
        )
