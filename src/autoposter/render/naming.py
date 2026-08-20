import re
from pathlib import Path

from autoposter.config.schema import Config

_SEPARATORS = re.compile(r"[\\/]+")

# Suffixes used by the flat layout (library_folders: false). The poster has no
# suffix — it is the bare item name.
_FLAT_SUFFIX = {"poster": "", "background": "_background"}


def _split(path: str) -> list[str]:
    return [segment for segment in _SEPARATORS.split(path) if segment]


def derive_root_folder(library_root: str, media_path: str, is_directory: bool) -> str:
    """Return the on-disk folder name an item's assets are filed under.

    For movies ``media_path`` is the media *file*, so the folder is its parent.
    For shows it is the series *directory*, which is the folder itself. The name
    is used verbatim: Posterizarr applies no sanitisation, so bracket characters
    such as ``[tvdb-389597]`` are part of the real asset path on disk.
    """
    root_segments = _split(library_root)
    path_segments = _split(media_path)
    if path_segments[: len(root_segments)] != root_segments:
        raise ValueError(f"{media_path!r} is not inside library root {library_root!r}")
    relative = path_segments[len(root_segments) :]
    if not relative:
        raise ValueError(f"{media_path!r} resolves to the library root itself")
    if is_directory:
        return relative[-1]
    if len(relative) < 2:
        raise ValueError(f"{media_path!r} has no containing folder below {library_root!r}")
    return relative[-2]


def season_asset_name(season_number: int) -> str:
    """``Season01.jpg``. Specials are season 0. Padding is a minimum, not a limit."""
    return f"Season{season_number:02d}.jpg"


def episode_asset_name(season_number: int, episode_number: int) -> str:
    """``S01E01.jpg``. Capital S and E, no separator."""
    return f"S{season_number:02d}E{episode_number:02d}.jpg"


def _file_name(art_kind: str, season_number: int | None, episode_number: int | None) -> str:
    if art_kind == "poster":
        return "poster.jpg"
    if art_kind == "background":
        return "background.jpg"
    if art_kind == "season_poster":
        if season_number is None:
            raise ValueError("season_poster requires season_number")
        return season_asset_name(season_number)
    if art_kind == "title_card":
        if season_number is None:
            raise ValueError("title_card requires season_number")
        if episode_number is None:
            raise ValueError("title_card requires episode_number")
        return episode_asset_name(season_number, episode_number)
    raise ValueError(f"unknown art_kind {art_kind!r}")


def asset_path(
    config: Config,
    library: str,
    root_folder: str,
    art_kind: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> Path:
    """Absolute path of one artifact inside the asset tree."""
    name = _file_name(art_kind, season_number, episode_number)
    if config.library_folders:
        return Path(config.assets_root) / library / root_folder / name
    if art_kind in _FLAT_SUFFIX:
        suffix = _FLAT_SUFFIX[art_kind]
        return Path(config.assets_root) / f"{root_folder}{suffix}.jpg"
    return Path(config.assets_root) / f"{root_folder}_{name}"
