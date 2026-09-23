"""Response compression for the served application (perf spec A1).

``GZipMiddleware`` for what a browser downloads in bulk -- the SPA's
JavaScript, most of a cold page load, and the larger JSON bodies (the config
document, the Action Center listing) -- behind a filter that keeps two kinds
of path away from it entirely:

- ``/api/dashboard/stream`` and ``/api/logs/stream``, the two NDJSON streams.
  Whether gzip would still deliver them line by line is the installed
  Starlette's decision: 1.6, the release this project runs today, flushes the
  compressor after every streamed chunk, so each line does leave at once --
  but that is one release's behaviour, not a contract, and a gzipped stream
  is no longer the bytes the endpoint yielded. Kept off the compressor, what
  a live dashboard reads is exactly what was written, on any version.
- ``/api/items/{id}/artwork...``: JPEG, PNG and WebP bytes, already
  compressed. Starlette 1.6 happens to skip those three content types itself,
  but the endpoint serves an unrecognised file as
  ``application/octet-stream``, which it would compress; gzip would only
  spend CPU on every Library tile.

By path, not by content type, on purpose: which content types the installed
Starlette declines to compress has changed between releases (a recent one
began excluding ``text/event-stream``), and NDJSON has never been on that
list. Which responses stream is a property of this application, and the path
is how the application names them.
"""

import re

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Below this a response goes out as it is: gzip's own header and trailer are
# ~20 bytes, and a small JSON answer gains nothing worth a compressor pass.
MINIMUM_SIZE = 1024

# The two NDJSON streams, by exact path.
UNCOMPRESSED_PATHS = frozenset({"/api/dashboard/stream", "/api/logs/stream"})
# `/api/items/*/artwork*`: the stored image and its /live sibling, whatever
# art kind the last segment names.
_ARTWORK_PATH = re.compile(r"^/api/items/[^/]+/artwork")


def bypasses_compression(path: str) -> bool:
    """Whether a response to ``path`` must never be gzipped."""
    return path in UNCOMPRESSED_PATHS or _ARTWORK_PATH.match(path) is not None


class PathFilteredGZipMiddleware:
    """``GZipMiddleware`` for every HTTP request except the bypassed paths,
    which reach the application directly with their bytes untouched."""

    def __init__(self, app: ASGIApp, minimum_size: int = MINIMUM_SIZE) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not bypasses_compression(scope["path"]):
            await self.gzip(scope, receive, send)
            return
        await self.app(scope, receive, send)


def install_compression(app: FastAPI) -> None:
    """Wrap ``app`` in the filtered gzip middleware.

    Called from ``main.build()`` beside ``mount_spa``, for the same reason the
    SPA mount lives there: it is production wiring, kept out of ``create_app``
    so every test application answers with exactly the bytes and headers the
    suite's assertions were written against. tests/test_main.py proves
    ``build()`` installs it; tests/test_compression.py proves what it does.
    """
    app.add_middleware(PathFilteredGZipMiddleware, minimum_size=MINIMUM_SIZE)
