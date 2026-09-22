"""Small JPEG variants of rendered artwork, for the library grid.

The grid shows 48 tiles a page at roughly 150-300 CSS pixels each, and every
tile used to download the full render -- a 2000x3000 poster or a 3840x2160
background at JPEG quality 92 (render/compositor.py), most of a megabyte --
only for the browser to scale it down again. ``?w=320`` and ``?w=640`` on the
artwork endpoint (api/artwork.py) serve one of these instead.

Made on request and kept only in memory, because there is nowhere persistent
to put them. The asset trees (/assets, /manualassets, /assetsbackup) are shared
NFS that Plex and other tooling also read, so a thumbnail written there would
sit beside the real artwork as if it were some. /state is the private
credential volume. The root filesystem is gone on the next pod restart. An
in-process LRU with a byte budget is therefore the whole cache.

Everything here blocks -- an NFS read and a JPEG decode -- and runs in the
artwork endpoint's worker thread, never on the event loop. It deliberately
does not take the render pipeline's RENDER_SLOTS: resizing a finished file is
a small fraction of a compose, and a page of tiles queued behind a full pass's
renders would be exactly the stall this module exists to remove.
"""
import io
import threading
from collections import OrderedDict
from enum import IntEnum
from pathlib import Path

from PIL import Image

# The cache's byte budget. A 320-wide poster thumbnail is roughly 20 KB and a
# 640-wide one roughly 70 KB (measured on tests/fixtures/golden's 2000x3000
# render), so 64 MiB holds on the order of a thousand tiles -- many pages of
# browsing -- while staying small beside one full-size decode. A module
# constant rather than a setting, as the spec decides for this lane.
THUMB_CACHE_BYTES = 64 * 1024 * 1024
# Visibly clean at tile size and about a third smaller than the renders' 92.
THUMB_QUALITY = 82
# What transparent pixels become, since JPEG has no alpha. Black because the
# tiles sit on the dark card background; the renders themselves are opaque, so
# this only ever touches a hand-placed PNG or WebP override.
FLATTEN_BACKGROUND = (0, 0, 0)


class ThumbWidth(IntEnum):
    """The widths ``?w=`` accepts; FastAPI answers anything else with a 422.

    An enum rather than ``Literal[320, 640]``: a query parameter arrives as a
    string, and pydantic does not coerce ``"320"`` to the int literal ``320``
    in lax mode, so the Literal form rejects every request, the valid ones
    included (checked against pydantic 2.12 / FastAPI 0.136 while planning).
    Two sizes and no more, so the cache holds at most two variants per file
    and a caller cannot make the server resize to arbitrary widths.
    frontend/src/pages/Library.tsx and ItemDetail.tsx request these values.
    """

    SMALL = 320
    LARGE = 640


class UndecodableArtwork(Exception):
    """The file at a render's path is not an image Pillow can decode."""


# (resolved path, st_size, st_mtime_ns, width). The stat fields are the same
# ones the ETag is made from, so a rewritten file can never be answered from
# its predecessor's entry.
ThumbKey = tuple[str, int, int, int]


class ThumbnailCache:
    """A least-recently-used map from ThumbKey to JPEG bytes, bounded by size.

    Bounded by bytes, not entries: a background thumbnail is half the size of
    a poster's, and it is memory, not the number of entries, that must stay
    bounded. Thread-safe, because every artwork request runs in its own
    worker thread. Two concurrent misses on one key both render and the
    second put wins; that costs one duplicate resize, which is cheaper than
    tracking in-flight work.
    """

    def __init__(self, budget_bytes: int) -> None:
        self._budget = budget_bytes
        self._entries: OrderedDict[ThumbKey, bytes] = OrderedDict()
        self._total = 0
        self._lock = threading.Lock()

    def get(self, key: ThumbKey) -> bytes | None:
        with self._lock:
            data = self._entries.get(key)
            if data is not None:
                self._entries.move_to_end(key)
            return data

    def put(self, key: ThumbKey, data: bytes) -> None:
        # Larger than the whole budget: storing it would evict everything,
        # then itself.
        if len(data) > self._budget:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._total -= len(previous)
            self._entries[key] = data
            self._total += len(data)
            while self._total > self._budget:
                _, evicted = self._entries.popitem(last=False)
                self._total -= len(evicted)

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_CACHE = ThumbnailCache(THUMB_CACHE_BYTES)


def _flatten(image: Image.Image) -> Image.Image:
    """``image`` as opaque RGB, compositing any transparency onto the background.

    A plain ``convert("RGB")`` drops alpha and keeps whatever colour sits under
    alpha 0 -- often white -- which is not what the transparent image shows.
    """
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, FLATTEN_BACKGROUND)
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def render_thumbnail(path: Path, width: int) -> bytes:
    """A JPEG of the image at ``path``, ``width`` pixels wide at most.

    The aspect ratio is kept and the image is never upscaled. ``draft`` asks
    libjpeg for a DCT-scaled decode (1/2, 1/4 or 1/8) that is still at least
    the target size, so a 2000x3000 poster is decoded at 500x750 for a
    320-wide tile rather than in full. It is a no-op for other formats.
    Flattening happens before the resize, so a palette image is resampled as
    RGB rather than by nearest-neighbour. Uncached; Pillow's errors propagate.
    """
    with Image.open(path) as image:
        target = (width, max(1, round(image.height * width / image.width)))
        image.draft("RGB", target)
        flat = _flatten(image)
        flat.thumbnail(target, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        flat.save(out, "JPEG", quality=THUMB_QUALITY)
    return out.getvalue()


def thumbnail_bytes(path: Path, size: int, mtime_ns: int, width: int) -> bytes:
    """The cached thumbnail for this exact file state, made on a miss.

    ``size`` and ``mtime_ns`` come from the caller's stat of ``path`` -- the
    one the ETag was made from -- so the cache entry and the ETag always
    describe the same file state. Raises ``FileNotFoundError`` if the file has
    gone since that stat (an ordinary 404), and ``UndecodableArtwork`` for
    bytes Pillow cannot read: truncated by a write still in progress, or a
    hand-placed file in a format it does not know.
    """
    key = (str(path), size, mtime_ns, width)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        data = render_thumbnail(path, width)
    except FileNotFoundError:
        raise
    except (OSError, Image.DecompressionBombError) as exc:
        raise UndecodableArtwork(f"{path}: {exc}") from exc
    _CACHE.put(key, data)
    return data
