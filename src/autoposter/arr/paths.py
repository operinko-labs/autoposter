"""Translating Plex paths into the paths Radarr and Sonarr see.

Verified live: Plex mounts /mnt/Media (capital M), Radarr and Sonarr see the
same files at /mnt/media (lowercase). The mapping is case-sensitive on
purpose -- that is the entire point of this module. A path that does not
fall under ``plex_root`` returns None rather than a guess: registering a
path the service cannot read is worse than skipping the item, because the
service then believes it holds a file it cannot access.
"""


def map_path(path: str | None, plex_root: str, arr_root: str) -> str | None:
    """Replace a leading ``plex_root`` with ``arr_root``, on path segments.

    Matching is on path segments, not a raw string prefix, so a sibling root
    that merely shares a prefix (``/mnt/Media2`` against a root of
    ``/mnt/Media``) does not match.
    """
    if not path:
        return None

    norm_path = path.replace("\\", "/")
    norm_plex_root = plex_root.replace("\\", "/").rstrip("/")
    norm_arr_root = arr_root.replace("\\", "/").rstrip("/")

    if norm_path == norm_plex_root:
        return norm_arr_root

    prefix = norm_plex_root + "/"
    if norm_path.startswith(prefix):
        return norm_arr_root + "/" + norm_path[len(prefix):]

    return None
