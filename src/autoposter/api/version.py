"""``GET /api/version`` -- what this container is running, and whether a newer
release exists.

The running version is not derived, it is *stamped* at build time, and which
of the two stamps applies says what kind of build this is:

* A **release** build carries ``AUTOPOSTER_RELEASE=v1.2.3``, passed as
  ``RELEASE_VERSION`` by ``.github/workflows/release.yml`` and set in the
  Dockerfile's runtime stage. That is the tag the image was published under on
  GHCR, and the workflow that sets it runs on GitHub Actions -- which is also
  where the releases this module polls for are published.
* Every **other** build carries only ``AUTOPOSTER_VERSION=sha-<git>``, which
  ``ci.yml`` stamps and pushes to Harbor under the same name, and which Flux
  deploys. A build given no ``GIT_SHA`` at all -- every local ``docker build``
  -- reports ``dev`` rather than a half-formed ``sha-``.

**The check runs only for release builds, and that gate is the design, not an
optimisation.** "Is there a newer release?" is a question only a released
version can answer: ``sha-4b2a34d`` is not behind ``v0.1.0`` or ahead of it,
it is off to one side, and comparing them would light the update marker on
every Flux pod in this operator's own cluster forever. So a non-release build
makes no GitHub request at all, and reports ``update_available: null``.

**The newest version comes from GitHub releases**, not from git tags or from a
registry. A tag exists the moment it is pushed; a *release* exists once the
release workflow built, verified and published an image for it. The question
the sidebar is really asking is "is there something I can pull", and the
releases endpoint is the thing that answers it.

That distinction is load-bearing rather than pedantic. The push mirror that
carries tags to GitHub cannot create a Release, and ``/releases/latest`` knows
nothing about tags -- it answered 404 for this repository while ``/tags``
listed ``v0.1.0`` quite happily. The final step of
``.github/workflows/release.yml`` is what creates the Release, after the image
is pushed, and ``tests/test_release_workflow.py`` holds it there. Delete that
step and this module polls a 404 forever while nothing goes red.

**GitHub is polled in the background, not fetched on request.** A request-path
call would mean one GitHub round trip per sidebar mount, and unauthenticated
API calls are limited to 60 per hour per address. ``ReleasePoller`` refreshes
``app.state.version_poller`` every ``POLL_INTERVAL_SECONDS`` from a background
task the lifespan starts beside ``PlexHealth`` (see ``app.py``); this endpoint
only ever reads that cached answer. No credential is involved: the repository
is public and the releases endpoint is anonymous.

**A registry that cannot be answered is not an error.** The half of this
endpoint an operator actually needs -- the version they are running -- is
known regardless, so a failed poll leaves the previous answer standing (or
``null`` before the first poll completes) rather than turning a decoration
into a 5xx.
"""
import asyncio
import logging
import os
import re

import httpx
from fastapi import APIRouter, Depends, Request

from autoposter.api.auth import ApiKeyPrincipal, api_key_or_session
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

router = APIRouter()

# What a build with no stamp at all reports. Honest about being
# unidentifiable rather than inventing a version that names no build.
DEV_VERSION = "dev"

# The prefix ci.yml's stamp carries. Only needed here to recognise the bare,
# argument-less form -- see `_running_version`.
SHA_PREFIX = "sha-"

# Where "the newest release" comes from. Hardcoded for the same reason
# POLL_INTERVAL_SECONDS is: which project this *is* is a fact about the build,
# not an operator preference, and a deployment that could be pointed at another
# project's releases would report updates that do not apply to the code it is
# running. A fork changes this line.
RELEASES_URL = "https://api.github.com/repos/operinko-labs/autoposter/releases/latest"

# GitHub serves this unauthenticated at 60 requests per hour per address, and
# `Accept` pins the response shape rather than taking whatever the API's
# current default happens to be.
RELEASES_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# How often the background poll refreshes the cached answer. Hardcoded,
# deliberately not an operator setting: cadence is a deployment fact, not a
# preference. Six hours sits far below any plausible release cadence, so a new
# release is noticed the same day it ships, and four calls a day leaves the
# anonymous rate limit untouched.
POLL_INTERVAL_SECONDS = 6 * 3600

# `v1.2.3`, and nothing else. The release workflow refuses to publish a tag of
# any other shape, so this is the full set of versions that can exist -- and
# anything failing to match here (a `sha-` build, `dev`, a hand-made tag) is
# reported as "cannot say" rather than guessed at.
_SEMVER = re.compile(r"v(\d+)\.(\d+)\.(\d+)$")


def _version_tuple(tag: str) -> tuple[int, int, int] | None:
    """``v1.2.3`` as a comparable tuple, or ``None`` for anything else.

    Tuple comparison rather than string comparison because strings order
    ``v0.10.0`` before ``v0.9.0``, which would hide a real update for as long
    as the minor version stayed in double digits.
    """
    match = _SEMVER.fullmatch(tag.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


async def _ask_github(http) -> str | None:
    """The newest published release's tag name, or ``None``.

    ``/releases/latest`` rather than ``/releases``: GitHub excludes drafts and
    pre-releases from it, so a release published for testing cannot light the
    update marker for everyone. It answers 404 when a repository has no
    published release yet, which `raise_for_status` turns into the same
    logged, non-fatal failure as any other refusal.
    """
    response = await http.get(RELEASES_URL, headers=RELEASES_HEADERS)
    response.raise_for_status()
    tag = (response.json() or {}).get("tag_name") or ""
    return tag.strip() or None


def _failure_reason(exc: Exception) -> str:
    """A failed poll as a category plus a class name.

    The class name alone says what was raised, not what an operator should go
    and look at, and three quite different problems all arrive as "some httpx
    exception". So the category leads:

    * ``connect`` -- GitHub was not spoken to at all. A deployment with no
      outbound internet lands here on every poll, which is a permanent
      no-answer rather than an outage, and worth being able to tell apart.
    * ``status`` -- GitHub answered with a refusal. The code comes too, which
      is what separates a rate limit (403) from a repository with no published
      release yet (404).
    * ``parse`` -- GitHub answered with something this module could not read.

    ``httpx.HTTPStatusError`` is checked first because it is not a
    ``TransportError``: the request succeeded, the answer was a refusal.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"status: {type(exc).__name__} {exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return f"connect: {type(exc).__name__}"
    return f"parse: {type(exc).__name__}"


class ReleasePoller:
    """Background refresh of the newest published release, in ``PlexHealth``'s
    idiom -- a single ``run(stop_event)`` coroutine the lifespan starts as a
    task and cancels on shutdown (see ``app.py``).

    ``latest`` is the cached answer, read by the endpoint and nowhere else
    fetched from; ``None`` until the first poll completes, or forever on a
    build that is not a release. A failed poll leaves ``latest`` exactly as the
    previous successful poll left it -- an outage must not erase a still-true
    answer -- and is logged the same way ``PlexHealth.refresh_token`` contains
    a plex.tv wobble: caught, logged by class name only, never raised.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None,
        running_version: str | None = None,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
    ):
        self._http = http
        # Read once, at construction, rather than per poll: the stamp is an
        # environment variable baked into the image and cannot change while the
        # process runs, and a test that sets it wants one obvious seam.
        self._running_version = (
            running_version if running_version is not None else _running_version()
        )
        self._interval_seconds = interval_seconds
        self.latest: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether this build can ask the question at all.

        False for `sha-` builds, for `dev`, and for an app built without an
        http client -- every application not built with ``run_background=True``.
        """
        return self._http is not None and _version_tuple(self._running_version) is not None

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll, then wait ``interval_seconds`` (or until ``stop_event``),
        until ``stop_event`` is set. Polling first -- rather than waiting out
        the first interval -- is what gives the sidebar an answer within
        seconds of boot instead of up to ``POLL_INTERVAL_SECONDS`` later.
        """
        if not self.enabled:
            return
        while not stop_event.is_set():
            await self._poll()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                pass
            else:
                return

    async def _poll(self) -> None:
        try:
            self.latest = await _ask_github(self._http)
        except Exception as exc:
            logger.warning("the update check failed (%s)", _failure_reason(exc))


def _running_version() -> str:
    """What this build is, preferring the release stamp over the commit stamp.

    ``AUTOPOSTER_RELEASE`` is set only by the release workflow, so its presence
    is what distinguishes a published version from a build of main. Falling
    back to ``AUTOPOSTER_VERSION`` keeps every existing deployment reporting
    exactly what it reported before.

    The bare prefix matters as much as the unset case, and is in fact the
    common one: ``ENV AUTOPOSTER_VERSION=sha-${GIT_SHA}`` in a build given no
    ``--build-arg`` does not leave the variable unset, it sets it to ``sha-``.
    A ``getenv`` default alone would report that as the running version, and
    the sidebar would show a tag that names no commit.
    """
    release = os.environ.get("AUTOPOSTER_RELEASE", "").strip()
    if release:
        return release
    raw = os.environ.get("AUTOPOSTER_VERSION", "").strip()
    if raw in ("", SHA_PREFIX):
        return DEV_VERSION
    return raw


@router.get("/version")
async def get_version(
    request: Request, _: SessionModel | ApiKeyPrincipal = Depends(api_key_or_session)
) -> dict:
    """``{version, update_available, latest}`` for the sidebar's version line.

    ``latest`` comes straight off ``app.state.version_poller`` -- this handler
    never talks to GitHub itself. ``update_available`` is a tri-state:
    ``true``/``false`` once the poller has an answer this build can be compared
    against, and ``null`` before the first poll completes, on a build that is
    not a release, or when the newest tag does not parse -- none of which are
    the same as "no update", and must not be shown as one.
    """
    version = _running_version()
    latest = request.app.state.version_poller.latest

    running_parts = _version_tuple(version)
    latest_parts = _version_tuple(latest) if latest else None
    if running_parts is None or latest_parts is None:
        update_available = None
    else:
        # Strictly newer, not merely different: an image built from a tag ahead
        # of the newest published release must not be told to downgrade.
        update_available = latest_parts > running_parts

    return {
        "version": version,
        "latest": latest,
        "update_available": update_available,
    }
