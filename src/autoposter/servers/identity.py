"""The server-neutral item identity (spec §4.2). One rule, no I/O."""
from __future__ import annotations

import posixpath

FILE_BEARING = frozenset({"movie", "episode"})


def identity_key(kind: str, *, tmdb_id: int | None, tvdb_id: int | None, imdb_id: str | None,
                 season_number: int | None, episode_number: int | None, file_path: str | None,
                 root_folder: str | None = None, legacy: str | None = None) -> str:
    if kind in ("season", "episode") and season_number is not None:
        coords = f"s{season_number}"
        if kind == "episode" and episode_number is not None:
            coords += f"e{episode_number}"
    else:
        coords = ""
    file = ""
    if kind in FILE_BEARING and file_path:
        file = posixpath.basename(file_path.replace("\\", "/"))
    for ns, value in (("tmdb", tmdb_id), ("tvdb", tvdb_id), ("imdb", imdb_id)):
        if value is not None and value != "":
            # Provider branch is unchanged by root_folder: the provider id
            # already disambiguates the item, so the folder would just be
            # noise here (and would break across a provider's own
            # renames/moves that never touch the file itself).
            return f"{kind}:{ns}:{value}:{coords}:{file}"
    # No provider id: fall back to on-disk location, which is shared,
    # server-neutral storage. A movie/episode needs its own basename first --
    # the folder alone can't name a *file* -- and only then gains the root
    # folder, which disambiguates two provider-less items that happen to
    # share a literal basename (e.g. two "movie.mkv" in different folders --
    # a real collision, not a contrived one). A show/season has no file of
    # its own; its folder alone -- the show's own root_folder -- is the only
    # thing to key on.
    if kind in FILE_BEARING:
        pfile = (f"{root_folder}/{file}" if root_folder else file) if file else ""
    else:
        pfile = root_folder or ""
    if pfile:
        # Five-field shape per spec §4.2, provider="path" and an EMPTY id slot --
        # not a shortened four-field form. Do not "simplify" this back down.
        return f"{kind}:path::{coords}:{pfile}"
    if legacy:
        return f"{kind}:legacy:plex:{legacy}"
    raise ValueError(f"{kind}: no provider id, no file path and no legacy key -- nothing to key on")


def identity_key_for(item) -> str:
    return identity_key(item.kind, tmdb_id=item.tmdb_id, tvdb_id=item.tvdb_id, imdb_id=item.imdb_id,
                        season_number=item.season_number, episode_number=item.episode_number,
                        file_path=item.file_path, root_folder=item.root_folder)


def parent_identity_key_for(item) -> str | None:
    """The item's immediate parent, one hop up -- not always the show.

    A season's parent IS the show. An episode's parent is its own SEASON,
    not the show directly (config/impact.py documents this two-hop model:
    "the show's id for a season and the season's id for an episode", and
    api/routes.py's item-detail breadcrumb walks the two hops explicitly).
    The season key reuses the item's OWN season_number and its parent's
    provider ids -- a season carries the same provider ids as its show (Plex
    gives seasons no ids of their own; ``adopt/walk.py`` and the Plex
    resolver both key a season off the show's guids), so an episode's parent
    season and that season's own identity_key_for land on the same key.
    root_folder alone is enough when no provider id survived either hop: the
    show's folder IS the item's own root_folder for both a season and an
    episode (both live under it).
    """
    if item.kind not in ("season", "episode"):
        return None
    if not (item.parent_tmdb_id or item.parent_tvdb_id or item.parent_imdb_id or item.root_folder):
        return None
    if item.kind == "episode":
        return identity_key("season", tmdb_id=item.parent_tmdb_id, tvdb_id=item.parent_tvdb_id,
                            imdb_id=item.parent_imdb_id, season_number=item.season_number,
                            episode_number=None, file_path=None, root_folder=item.root_folder)
    return identity_key("show", tmdb_id=item.parent_tmdb_id, tvdb_id=item.parent_tvdb_id,
                        imdb_id=item.parent_imdb_id, season_number=None, episode_number=None,
                        file_path=None, root_folder=item.root_folder)
