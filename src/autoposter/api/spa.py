"""Serve the built single-page application alongside the API.

The SPA owns the browser's URL bar, so a reload of `/failures` arrives here as
a request for a path this server has no route for. Answering it with
`index.html` is what makes client-side routing survive a reload -- but a
catch-all broad enough to do that is also broad enough to swallow
`/api/does-not-exist`, turning a JSON 404 into an HTML page and breaking every
API client's error handling, and to shadow `/healthz`, which would leave
Kubernetes probing a page that says nothing about whether the service works.

Hence the prefix guard below, and `tests/test_spa_serving.py` pinning the
boundary from both sides.
"""

import logging
import os
from os import PathLike
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger(__name__)

# Paths the SPA must never answer for. Anything under these belongs to the
# API, the probes or the interactive docs, and must keep its own response --
# including its own 404.
RESERVED_PREFIXES = (
    "api",
    "healthz",
    "metrics",
    "webhook",
    "docs",
    "redoc",
    "openapi.json",
)

# Perf spec A2. Everything under /assets is named by Vite after its content
# hash, so one URL's bytes never change: a browser may keep it for a year and
# skip even the revalidation round trip.
IMMUTABLE_ASSET_CACHE = "public, max-age=31536000, immutable"
# The shell is the opposite: it is the one file whose name never changes and
# whose content names the hashed files of the build that produced it. Cached,
# a shell from the previous deploy asks the new pod for assets it no longer
# has. `no-cache` still lets the browser keep a copy -- it just revalidates
# every load, which a FileResponse's ETag makes a 304.
SHELL_CACHE = "no-cache"


class ImmutableStaticFiles(StaticFiles):
    """StaticFiles whose successful answers carry ``IMMUTABLE_ASSET_CACHE``.

    A miss raises before ``file_response`` is reached, so a 404 is never
    marked immutable -- a missing file cached for a year would outlive the
    deploy that fixes it."""

    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = IMMUTABLE_ASSET_CACHE
        return response


def spa_dist() -> Path | None:
    """Where the built SPA lives, or None if this deployment has no UI.

    The image sets AUTOPOSTER_SPA_DIST; a source checkout usually has not run
    `npm run build` at all, and must still start.
    """
    configured = os.environ.get("AUTOPOSTER_SPA_DIST")
    if configured:
        return Path(configured)
    default = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
    return default if default.is_dir() else None


def mount_spa(app: FastAPI, dist: Path | None) -> None:
    """Serve `dist` at `/`, leaving the API and probes untouched.

    Call this *after* every router is included: the catch-all matches whatever
    is left, so anything registered afterwards would be unreachable.

    A missing directory is not an error. The Python test suite and a bare
    `uvicorn` run would otherwise need a Node toolchain to start.
    """
    if dist is None:
        logger.info("no SPA build configured; serving the API only")
        return

    index = dist / "index.html"
    if not index.is_file():
        logger.warning("no SPA build at %s; serving the API only", dist)
        return

    assets = dist / "assets"
    if assets.is_dir():
        # StaticFiles resolves against the directory and rejects traversal
        # itself, so `/assets/../../secret` cannot escape.
        app.mount("/assets", ImmutableStaticFiles(directory=assets), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        first = full_path.split("/", 1)[0]
        if first in RESERVED_PREFIXES:
            # Re-raise as this server's own 404 rather than serving the shell.
            # The route matched only because nothing more specific did, which
            # for these prefixes means the path genuinely does not exist.
            raise HTTPException(status_code=404, detail="Not Found")
        # Any other path is assumed to be a client-side route. Serving a file
        # named by the request is deliberately not attempted -- that is what
        # the /assets mount is for, and reading arbitrary paths from the URL
        # is how directory traversal gets in.
        return FileResponse(index, headers={"Cache-Control": SHELL_CACHE})

    logger.info("serving the web UI from %s", dist)
