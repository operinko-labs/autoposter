"""Read the provenance we stamp into badged artwork back out of Plex.

Two formats turn up on a Plex artwork URL and they hide their metadata in
opposite ends of the file:

* **WebP** -- what *this* service uploads. The EXIF chunk sits at the very end
  (measured on a production poster: ``VP8X`` at 12, ``VP8`` at 30, ``EXIF`` at
  199,080 of 199,122), so only a *suffix* read reaches it. Pillow cannot open a
  truncated WebP either, so the chunk has to be located in the raw bytes.
* **JPEG** -- what the tool being replaced uploaded, and what the base files
  under ``/assets`` are. Metadata lives in the *header* (``APP0`` at 2, ``COM``
  at 20, ``SOS`` at 406 in our base files), so a prefix read reaches it and a
  suffix read never would.

``probe_exif`` is the entry point that gets this right for both without
spending a request it does not need: see its docstring.

Plex honours HTTP Range on artwork. See ``docs/research`` for the measured
numbers all of this is built on.
"""
import io
import logging

from PIL import Image

logger = logging.getLogger(__name__)

# Enough of the front of the file for both jobs at once.
#
# JPEG: our base files carry every metadata segment before ``SOS`` at offset
# 406, and Pillow parses the marker chain up to ``SOS`` at ``Image.open`` time
# without touching entropy-coded data -- so 4096 clears the measured worst case
# by roughly ten times. (A JPEG whose EXIF segment genuinely overran this reads
# as "no provenance" and falls through to a normal upload, which is the safe
# direction.)
# WebP: the decision needs exactly 21 bytes -- the RIFF header, the ``VP8X``
# fourcc at 12 and the flags byte at 20 -- so anything past that is slack.
# 4096 also matches ``fetch_exif_tail``'s suffix size, so both halves of a
# probe cost one small range request each.
PREFIX_BYTES = 4096

_JPEG_MAGIC = b"\xff\xd8"
# Offset 12 holds the first chunk's fourcc. ``VP8X`` is the extended header,
# the only form that can declare metadata; a plain ``VP8 ``/``VP8L`` file has
# no flags byte and therefore cannot carry EXIF at all.
_VP8X_OFFSET = 12
_VP8X_FOURCC = b"VP8X"
# The VP8X flags byte, and the bit that says "this file has an EXIF chunk".
# Verified against a production poster: flags = 0x08.
_VP8X_FLAGS_OFFSET = 20
_EXIF_FLAG = 0x08

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


def read_exif_from_header(prefix: bytes) -> dict[int, object]:
    """Parse the EXIF of a JPEG from its leading bytes.

    ``Image.open`` reads the marker chain and stops at ``SOS`` without touching
    the entropy-coded data, so a prefix that reaches ``SOS`` is enough -- and
    every metadata segment is before it by definition. ``{}`` for anything that
    does not check out: a prefix that stops short, a format Pillow does not
    recognise from these bytes, or a payload it refuses to parse.
    """
    try:
        with Image.open(io.BytesIO(prefix)) as image:
            return dict(image.getexif())
    except Exception:
        logger.debug("could not parse EXIF from header read", exc_info=True)
        return {}


def webp_declares_exif(prefix: bytes) -> bool:
    """Whether a WebP's own header says it carries an EXIF chunk.

    This is what makes the suffix request avoidable. A WebP can only hold
    metadata behind a ``VP8X`` extended header, whose flags byte advertises
    which optional chunks are present -- so a file with no ``VP8X`` at offset
    12, or with the EXIF bit clear, provably has nothing to find and needs no
    second request. Conservative on malformed input: too few bytes to decide
    reads as "no".
    """
    if len(prefix) <= _VP8X_FLAGS_OFFSET:
        return False
    if prefix[_VP8X_OFFSET:_VP8X_OFFSET + 4] != _VP8X_FOURCC:
        return False
    return bool(prefix[_VP8X_FLAGS_OFFSET] & _EXIF_FLAG)


async def _fetch_prefix(http, url: str, headers: dict, prefix_bytes: int) -> bytes:
    """The leading ``prefix_bytes`` of ``url``, or ``b""`` if they cannot be had."""
    request_headers = dict(headers)
    request_headers["Range"] = f"bytes=0-{prefix_bytes - 1}"
    response = await http.get(url, headers=request_headers)
    if response.status_code == 206:
        return response.content
    if response.status_code == 200:
        # The server ignored the Range and sent the whole file; still usable.
        return response.content[:prefix_bytes]
    return b""


async def probe_exif(
    http, url: str, headers: dict, *,
    prefix_bytes: int = PREFIX_BYTES, tail_bytes: int = 4096,
) -> dict[int, object]:
    """Read an artwork URL's EXIF in as few range requests as the format allows.

    One prefix request always happens. What it buys depends on what came back:

    1. **JPEG** -- the metadata is in the header, so it is already in hand.
       One request, no tail read ever.
    2. **WebP without EXIF** -- no ``VP8X`` at offset 12, or its flags byte has
       the EXIF bit clear. The file *cannot* carry EXIF, so ``{}`` is the whole
       answer. One request; this is the case the tail read used to waste a
       second one on.
    3. **WebP with EXIF declared** -- only now is the suffix request worth
       issuing, because the chunk is at the end of the file. Two requests.
    4. Anything else -- a format we do not stamp, a truncated or malformed
       response, an error status -- ``{}``, one request.

    Defensive throughout, deliberately: this runs across ~16,000 items, and one
    strange image must cost that item its optimisation, never the sweep.
    """
    try:
        prefix = await _fetch_prefix(http, url, headers, prefix_bytes)
    except Exception:
        logger.debug("prefix read failed for %s", url, exc_info=True)
        return {}
    if not prefix:
        return {}

    if prefix.startswith(_JPEG_MAGIC):
        return read_exif_from_header(prefix)

    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        if not webp_declares_exif(prefix):
            return {}
        try:
            return await fetch_exif_tail(http, url, headers, tail_bytes)
        except Exception:
            logger.debug("tail read failed for %s", url, exc_info=True)
            return {}

    return {}


__all__ = [
    "OVERLAY_TAG",
    "PREFIX_BYTES",
    "PROVENANCE_TAG",
    "format_provenance",
    "parse_provenance",
    "probe_exif",
    "read_exif_from_header",
    "read_exif_from_tail",
    "fetch_exif_tail",
    "webp_declares_exif",
]
