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

Three rules shape the rest of this module.

**The Harbor URL never leaves the process.** It is derived from
``AUTOPOSTER_IMAGE_REF`` at boot (``config/image_ref.py``, stored on
``app.state.version_check_target`` by ``app.py``'s ``create_app``) -- an
internal hostname -- and it is not in the response, not in an event row, and
not in the log. That last one is the trap: every httpx exception renders the
full request URL in its own message, so ``logger.warning("...%s", exc)``,
``repr(exc)`` or ``exc_info=True`` would each publish it into a log an
operator pastes into a ticket. Only ``type(exc).__name__`` is logged, behind
a fixed category word naming which half of the exchange failed
(``_failure_reason``) -- plus, for an ``httpx.HTTPStatusError``, the
response's status code, which is just an int and names nothing about where it
was fetched from.

**Harbor is polled in the background, not fetched on request.** The
`autoposter` project is public and internet-accessible (an operator decision,
2026-08-26), and a request-path call would mean one Harbor round trip per
sidebar mount. Instead ``VersionPoller`` refreshes ``app.state.version_poller``
every ``POLL_INTERVAL_SECONDS`` from a background task the lifespan starts
beside ``PlexHealth`` (see ``app.py``); this endpoint only ever reads that
cached answer. Because the project is public, ``AUTOPOSTER_HARBOR_TOKEN`` is
optional too: an empty token means an anonymous request, and being
"configured" now means only that the image ref parsed and the process has an
http client -- not that a token was supplied. The token still exists for a
deployment whose registry project is private.

**A registry that cannot be answered is not an error.** The half of this
endpoint an operator actually needs -- the version they are running -- is
known regardless, so a failed poll leaves the previous answer standing (or
``null`` before the first poll completes) rather than turning a decoration
into a 5xx.
"""
import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request

from autoposter.api.auth import ApiKeyPrincipal, api_key_or_session
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

# How often the background poll refreshes the cached answer. Hardcoded,
# deliberately not an operator setting: cadence is a deployment fact, not a
# preference, the same philosophy that moved the Harbor coordinates themselves
# out of the config schema and into AUTOPOSTER_IMAGE_REF. Six hours sits far
# below any plausible deploy cadence, so a genuinely new image is noticed the
# same day it ships.
POLL_INTERVAL_SECONDS = 6 * 3600


async def _ask_harbor(http, harbor_url: str, project: str, repository: str, token: str):
    """Harbor's newest ``sha-*`` tag for the repository, or ``None``.

    One artifact, newest first, with its tags attached: the repository holds
    one artifact per commit ever pushed, so an unsorted or uncapped listing
    would read the whole history to answer a one-line question -- and without
    ``with_tag`` the artifact comes back tagless, which is a null answer
    however many pages are fetched.
    """
    headers = {}
    if token:
        # A Harbor robot account's credential, supplied already base64-encoded
        # as `robot$name:secret` -- see Secrets.harbor_token. Omitted
        # entirely for the public project's anonymous default: an empty
        # Authorization header is not the same as sending none.
        headers["Authorization"] = f"Basic {token}"
    response = await http.get(
        f"{harbor_url.rstrip('/')}/api/v2.0/projects/{project}"
        f"/repositories/{repository}/artifacts",
        params={"page_size": 1, "sort": "-push_time", "with_tag": "true"},
        headers=headers,
    )
    response.raise_for_status()
    for artifact in response.json():
        for tag in artifact.get("tags") or []:
            name = tag.get("name") or ""
            if name.startswith(TAG_PREFIX):
                return name
    return None


def _failure_reason(exc: Exception) -> str:
    """A failed poll as a category plus a class name -- never a URL.

    The class name alone says what was raised, not what an operator should go
    and look at, and three quite different problems all arrive as "some httpx
    exception". So the category leads:

    * ``connect`` -- the registry was not spoken to at all. This is where a
      registry that does not speak **https** lands, and it lands there on
      every poll forever: the URL is built with a hardcoded scheme (see
      ``_poll``), so an ``http``-only registry is a permanent, silent
      no-answer that would otherwise be indistinguishable from an outage.
    * ``status`` -- Harbor answered with a refusal. The code comes too: an
      int, which names nothing about where it was fetched from, and which is
      what separates a rotated robot token (401) from a Harbor that is down.
    * ``parse`` -- Harbor answered with something this module could not read.

    ``httpx.HTTPStatusError`` is checked first because it is not a
    ``TransportError``: the request succeeded, the answer was a refusal.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"status: {type(exc).__name__} {exc.response.status_code}"
    if isinstance(exc, httpx.TransportError):
        return f"connect: {type(exc).__name__}"
    return f"parse: {type(exc).__name__}"


class VersionPoller:
    """Background refresh of Harbor's newest tag, in ``PlexHealth``'s idiom --
    a single ``run(stop_event)`` coroutine the lifespan starts as a task and
    cancels on shutdown (see ``app.py``).

    ``latest`` is the cached answer, read by the endpoint and nowhere else
    fetched from; ``None`` until the first poll completes, or forever if the
    check is unconfigured. A failed poll leaves ``latest`` exactly as the
    previous successful poll left it -- an outage must not erase a
    still-true answer -- and is logged the same way ``PlexHealth.refresh_token``
    contains a plex.tv wobble: caught, logged by class name only (never the
    exception's own message, which carries the Harbor URL), never raised.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None,
        target: tuple[str, str, str] | None,
        token: str,
        interval_seconds: float = POLL_INTERVAL_SECONDS,
    ):
        self._http = http
        self._target = target
        self._token = token
        self._interval_seconds = interval_seconds
        self.latest: str | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll, then wait ``interval_seconds`` (or until ``stop_event``),
        until ``stop_event`` is set. Polling first -- rather than waiting out
        the first interval -- is what gives the sidebar an answer within
        seconds of boot instead of up to ``POLL_INTERVAL_SECONDS`` later.

        A no-op when unconfigured (no image ref, or no http client yet --
        every application not built with ``run_background=True``), same as
        today's behaviour for a deployment that never set
        ``AUTOPOSTER_IMAGE_REF``.
        """
        if self._target is None or self._http is None:
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
        registry, project, repository = self._target
        try:
            # https, always: an image reference carries no scheme to derive one
            # from, and this deployment's registry is https. A registry that is
            # not fails every poll as a `connect` failure -- which is why the
            # warning below carries a category (see `_failure_reason`) rather
            # than a bare class name.
            self.latest = await _ask_harbor(
                self._http, f"https://{registry}", project, repository, self._token
            )
        except Exception as exc:
            # The category and the class name, and nothing else. See this
            # module's docstring: the exception's own message carries the
            # Harbor URL.
            logger.warning("the update check failed (%s)", _failure_reason(exc))


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


@router.get("/version")
async def get_version(
    request: Request, _: SessionModel | ApiKeyPrincipal = Depends(api_key_or_session)
) -> dict:
    """``{version, update_available, latest}`` for the sidebar's version line.

    ``latest`` comes straight off ``app.state.version_poller`` -- this handler
    never talks to Harbor itself. ``update_available`` is a tri-state:
    ``true``/``false`` once the poller has an answer, and ``null`` before the
    first poll completes or when the check is unconfigured -- which is not
    the same as "no update" and must not be shown as one.
    """
    version = _running_version()
    latest = request.app.state.version_poller.latest

    return {
        "version": version,
        "latest": latest,
        "update_available": None if latest is None else latest != version,
    }
