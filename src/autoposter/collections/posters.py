"""Choosing which poster a managed collection should carry.

Two pure decisions: which hosted URL a collection's default poster lives at
(``hosted_poster_url``), and whether the operator has placed a local override
that should win instead (``local_poster_path``). Fetching and uploading is a
separate, later step -- this module never makes a network call.

The hosted defaults come from ``Kometa-Team/Default-Images``, a repository
with no LICENSE file and no licence statement, so the sibling
``Kometa-Team/Kometa`` MIT grant does not literally extend to it -- and in any
case an MIT grant only covers what the licensor owns, not the IMDb, AMPAS or
Common Sense marks these images embed. This is a private single-operator
deployment: the images are fetched at runtime rather than vendored into this
repository, and it replaces a tool (Kometa) that does exactly the same thing.
That is a lighter posture than ``assets/badges/``, which *are* committed
here. If this repository is ever published, revisit this alongside
``assets/badges/PROVENANCE.md``.
"""
from pathlib import Path
from urllib.parse import quote

from autoposter.config.schema import Config

DEFAULT_IMAGES_BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"

_LOCAL_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def hosted_poster_url(kind: str, key: str) -> str | None:
    """The default poster URL for a collection of the given kind, or ``None``.

    ``kind`` is one of ``award_static``, ``award_year``, ``chart``,
    ``content_rating``, ``content_rating_other``, ``separator``; ``key`` is
    the piece that varies. Only the chart key is URL-encoded -- the others
    have no spaces, and encoding the year path's slash would break
    ``award/oscars/winner/2026``. An unrecognised kind returns ``None``
    rather than guessing: a wrong URL 404s and the collection quietly keeps
    no poster, which is harder to spot than an error.
    """
    if kind == "award_static":
        return f"{DEFAULT_IMAGES_BASE}/award/oscars/{key}.jpg"
    if kind == "award_year":
        return f"{DEFAULT_IMAGES_BASE}/award/oscars/winner/{key}.jpg"
    if kind == "chart":
        return f"{DEFAULT_IMAGES_BASE}/chart/color/{quote(key, safe='')}.jpg"
    if kind == "content_rating":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/{key}.jpg"
    if kind == "content_rating_other":
        return f"{DEFAULT_IMAGES_BASE}/content_rating/cs/NR.jpg"
    if kind == "separator":
        return f"{DEFAULT_IMAGES_BASE}/separators/orig/{key}.jpg"
    return None


def local_poster_path(config: Config, library: str, title: str) -> Path | None:
    """The operator's own poster for this collection, if one exists.

    Checked in ``jpg``, ``jpeg``, ``png``, ``webp`` order at
    ``<assets_root>/<library>/<title>/poster.<ext>``, or -- when
    ``config.library_folders`` is false -- the flat layout's
    ``<assets_root>/<title>.<ext>``. This is how ``prioritize_assets: true``
    worked in the tool being replaced: a file here overrides the hosted
    default, so it must never be silently skipped in favour of a download.
    """
    root = Path(config.assets_root)
    if config.library_folders:
        folder = root / library / title
        candidates = (folder / f"poster.{ext}" for ext in _LOCAL_EXTENSIONS)
    else:
        candidates = (root / f"{title}.{ext}" for ext in _LOCAL_EXTENSIONS)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
