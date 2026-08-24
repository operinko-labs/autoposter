"""``GET /api/version`` -- what this pod is running, and whether Harbor has newer.

The running version is not derived, it is *stamped*: the CI workflow passes
the commit's short sha to ``docker build`` as ``GIT_SHA``, the Dockerfile turns
that into ``AUTOPOSTER_VERSION=sha-<git>``, and that is byte-identical to the
tag the same job then pushes. So the image knows its own name in the registry,
which is the only thing that makes the comparison below meaningful. A build
that was given no ``GIT_SHA`` -- every local ``docker build`` -- reports
``dev`` rather than a half-formed ``sha-``.

The newest version comes from **Harbor**, not from git. A commit whose image
failed to build, or failed the vulnerability scan, was never pushed: git would
report an update to something nobody can deploy. The registry holds exactly
the images that exist.

Two rules shape the rest of this module.

**The Harbor URL never leaves the process.** It is operator config -- an
internal hostname -- and it is not in the response, not in an event row, and
not in the log. That last one is the trap: every httpx exception renders the
full request URL in its own message, so ``logger.warning("...%s", exc)``,
``repr(exc)`` or ``exc_info=True`` would each publish it into a log an
operator pastes into a ticket. Only ``type(exc).__name__`` is logged.

**A registry that cannot be answered is not an error.** The half of this
endpoint an operator actually needs -- the version they are running -- is
known regardless, so a failure answers ``latest: null`` and
``update_available: null`` (the sidebar then shows the version alone) rather
than a 5xx out of a decoration.
"""
import logging
import os
import time

from fastapi import APIRouter, Depends, Request

from autoposter.api.auth import require_session
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

# What a build with no GIT_SHA reports. Honest about being unidentifiable
# rather than inventing a tag that would never match anything in the registry.
DEV_VERSION = "dev"

# Only `sha-*` tags are candidates. Every push tags the artifact twice --
# `sha-<git>` and `latest` -- and comparing the running version against
# `latest` would never match, so the marker would be lit forever.
TAG_PREFIX = "sha-"

# How long one Harbor answer stands for. The sidebar asks on every mount, and
# an operator may have several tabs open; without this the registry sees a
# request per page load. Fifteen minutes is far below any plausible deploy
# cadence, so a genuinely new image is still noticed within one coffee.
CACHE_TTL_SECONDS = 900

# ``(monotonic reading when filled, latest tag or None)``, for the process.
#
# A module global rather than a scheduler job, deliberately: a background poll
# would keep asking a registry nobody is currently looking at the answer from,
# and it would need its own lifespan wiring for a value that has exactly one
# reader. Failures are cached too -- an outage must not be hammered by every
# mount for as long as it lasts.
#
# Note the consequence: for up to CACHE_TTL_SECONDS after the Harbor settings
# are edited, this still answers from the old target. That is a stale
# decoration, not a stale decision, and it costs nothing to wait out.
_cache: tuple[float, str | None] | None = None


def _running_version() -> str:
    """The tag this image was built as, or ``dev``.

    The bare prefix matters as much as the unset case, and is in fact the
    common one: ``ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}`` in a build given no
    ``--build-arg`` does not leave the variable unset, it sets it to ``sha-``.
    A ``getenv`` default alone would report that as the running version, and
    the sidebar would show a tag that names no commit.
    """
    raw = os.environ.get("AUTOPOSTER_VERSION", "").strip()
    if raw in ("", TAG_PREFIX):
        return DEV_VERSION
    return raw


async def _ask_harbor(http, harbor_url: str, project: str, repository: str, token: str):
    """Harbor's newest ``sha-*`` tag for the repository, or ``None``.

    One artifact, newest first, with its tags attached: the repository holds
    one artifact per commit ever pushed, so an unsorted or uncapped listing
    would read the whole history to answer a one-line question -- and without
    ``with_tag`` the artifact comes back tagless, which is a null answer
    however many pages are fetched.
    """
    response = await http.get(
        f"{harbor_url.rstrip('/')}/api/v2.0/projects/{project}"
        f"/repositories/{repository}/artifacts",
        params={"page_size": 1, "sort": "-push_time", "with_tag": "true"},
        # A Harbor robot account's credential, supplied already base64-encoded
        # as `robot$name:secret` -- see Secrets.harbor_token.
        headers={"Authorization": f"Basic {token}"},
    )
    response.raise_for_status()
    for artifact in response.json():
        for tag in artifact.get("tags") or []:
            name = tag.get("name") or ""
            if name.startswith(TAG_PREFIX):
                return name
    return None


async def latest_tag(http, harbor_url: str, project: str, repository: str, token: str):
    """``_ask_harbor`` behind the process-wide cache, never raising."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        latest = await _ask_harbor(http, harbor_url, project, repository, token)
    except Exception as exc:
        # The class name and nothing else. See this module's docstring: the
        # exception's own message carries the Harbor URL.
        logger.warning("the update check could not reach the registry (%s)",
                       type(exc).__name__)
        latest = None
    _cache = (now, latest)
    return latest


@router.get("/version")
async def get_version(
    request: Request, _: SessionModel = Depends(require_session)
) -> dict:
    """``{version, update_available, latest}`` for the sidebar's version line.

    ``update_available`` is a tri-state: ``true``/``false`` when the registry
    answered, and ``null`` when it was not asked or could not be reached --
    which is not the same as "no update" and must not be shown as one.
    """
    version = _running_version()
    check = request.app.state.config_holder.current.version_check
    token = request.app.state.secrets.harbor_token
    http = request.app.state.http

    # All four, not just the URL: an empty project or repository builds a
    # request that can only 404, and the credential is what makes a private
    # project's listing readable at all. `http` is None until the lifespan
    # runs, which is every test application that has not wired one.
    configured = all([check.harbor_url, check.project, check.repository, token, http])
    latest = (
        await latest_tag(
            http, check.harbor_url, check.project, check.repository, token
        )
        if configured
        else None
    )

    return {
        "version": version,
        "latest": latest,
        "update_available": None if latest is None else latest != version,
    }
