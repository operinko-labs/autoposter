import asyncio
import re
from dataclasses import dataclass

from plexapi.exceptions import NotFound as PlexNotFound

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
    parent_rating_key: str | None = None


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


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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

    def _sections(self):
        # plexapi exposes `library` as a property and `sections` as a method.
        return [
            s for s in self._server.library.sections() if s.title not in self._excluded
        ]

    def _search_sync(self, intent: RenderIntent) -> _RawMatch | None:
        wanted = []
        if intent.tmdb_id:
            wanted.append(f"tmdb://{intent.tmdb_id}")
        if intent.tvdb_id:
            wanted.append(f"tvdb://{intent.tvdb_id}")
        if intent.imdb_id:
            wanted.append(f"imdb://{intent.imdb_id}")

        for section in self._sections():
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

                    # The GUID search always matches the show (or movie). A season/
                    # episode intent must navigate down from there to the item it
                    # actually refers to, so the returned identity — rating key and
                    # title — belongs to that item, not the show.
                    target = item
                    parent_rating_key = None
                    if intent.kind == "season":
                        try:
                            target = item.season(season=intent.season_number)
                        except PlexNotFound:
                            return None
                        parent_rating_key = str(item.ratingKey)
                    elif intent.kind == "episode":
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
            raise ItemNotFound(
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
            tmdb_id=_as_int(guids.get("tmdb")) or intent.tmdb_id,
            tvdb_id=_as_int(guids.get("tvdb")) or intent.tvdb_id,
            imdb_id=guids.get("imdb") or intent.imdb_id,
            parent_rating_key=match.parent_rating_key,
        )
