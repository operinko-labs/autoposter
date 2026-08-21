"""Read the provenance we stamp into badged artwork back out of Plex.

The badged images we upload are WebP, and Pillow cannot open a truncated
WebP -- so recovering EXIF from a partial read means locating the chunk in
the raw tail bytes ourselves rather than handing the bytes to ``Image.open``.
Plex honours HTTP Range on artwork, and the EXIF chunk in our badged WebPs
sits at the very end of the file, so a small suffix read is enough: see
``docs/research`` for the measured numbers this is built on.

The JPEG base files under ``/assets`` are a different format with metadata in
the *header*, not the tail -- nothing here applies to those.
"""
import logging

from PIL import Image

logger = logging.getLogger(__name__)

# The existing marker, already written by the badge compositor -- see
# ``badges.compose.EXIF_OVERLAY_TAG``. Re-declared here (same value) so a
# caller reading EXIF back from Plex has no reason to import the badges
# package at all.
OVERLAY_TAG = 0x04BC

# Standard EXIF ``ImageDescription`` tag, carrying a parseable value rather
# than a private binary blob, so it stays legible to any EXIF tool.
PROVENANCE_TAG = 0x010E
_PROVENANCE_PREFIX = "autoposter;v=1;fp="


def format_provenance(fingerprint: str) -> str:
    """The ``ImageDescription`` value that records ``fingerprint`` as ours."""
    return f"{_PROVENANCE_PREFIX}{fingerprint}"


def parse_provenance(value: str | None) -> str | None:
    """The fingerprint recorded in an ``ImageDescription`` value, or ``None``
    if it is absent or was not written by us (someone else's description
    text, or no tag at all)."""
    if value is None or not value.startswith(_PROVENANCE_PREFIX):
        return None
    fingerprint = value[len(_PROVENANCE_PREFIX):]
    return fingerprint or None


def read_exif_from_tail(tail: bytes) -> dict[int, object]:
    """Locate a WebP EXIF chunk within ``tail`` and return its parsed tags.

    ``tail`` is expected to be the last bytes of a WebP file -- the chunk
    layout in our badged images puts EXIF at the very end -- but this makes
    no assumption about where within ``tail`` the chunk starts. Returns
    ``{}`` rather than raising for anything that does not check out: no
    ``EXIF`` marker, a truncated chunk, a declared size that overruns what
    is available, or a payload Pillow refuses to parse. A malformed image
    must not take down a sweep.
    """
    index = tail.rfind(b"EXIF")
    if index == -1:
        return {}
    payload_start = index + 8  # 4-byte fourcc + 4-byte little-endian size
    if payload_start > len(tail):
        return {}
    size = int.from_bytes(tail[index + 4:payload_start], "little")
    if size <= 0 or payload_start + size > len(tail):
        return {}
    payload = tail[payload_start:payload_start + size]
    try:
        exif = Image.Exif()
        exif.load(payload)
        return dict(exif)
    except Exception:
        logger.debug("could not parse EXIF payload from tail read", exc_info=True)
        return {}


async def fetch_exif_tail(
    http, url: str, headers: dict, tail_bytes: int = 4096
) -> dict[int, object]:
    """Issue the suffix Range request for ``url`` and parse the result.

    A ``206`` is the expected response -- Plex honours Range on artwork.
    A ``200`` means the server ignored the Range and sent the whole file;
    that is still usable, just by reading the tail of what came back.
    Anything else (a ``404`` chief among them) yields ``{}``.
    """
    request_headers = dict(headers)
    request_headers["Range"] = f"bytes=-{tail_bytes}"
    response = await http.get(url, headers=request_headers)
    if response.status_code == 206:
        return read_exif_from_tail(response.content)
    if response.status_code == 200:
        return read_exif_from_tail(response.content[-tail_bytes:])
    return {}


__all__ = [
    "OVERLAY_TAG",
    "PROVENANCE_TAG",
    "format_provenance",
    "parse_provenance",
    "read_exif_from_tail",
    "fetch_exif_tail",
]
