"""``JellyfinHealth`` -- ``PlexHealth``'s shape (plex/health.py), minus the
token refresh: Jellyfin's API key does not expire from disuse the way a Plex
account token can, so there is nothing here for a second loop to do.

Liveness is a cheap, authenticated ``GET {url}/System/Info`` (capture ->
docs/reference/2026-09-jellyfin-openapi-12.md, "/System/Info" -> get), using
the same ``MediaBrowser`` header every other Jellyfin call uses -- built via
``JellyfinApi.headers()`` rather than restated here, so the header format
lives in exactly one place.
"""
import asyncio
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


class JellyfinHealth:
    def __init__(
        self, url: str, api_key: str, http: httpx.AsyncClient, liveness_interval: float = 60,
        version: str | None = None,
    ):
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._http = http
        self._liveness_interval = liveness_interval
        # Read once, at construction, like ReleasePoller's own running_version:
        # the header's Version="..." is stamped once per process, never per
        # poll (api/version.py's _running_version reads an env var baked into
        # the image, which cannot change while the process runs).
        from autoposter.api.version import _running_version
        self._version = version if version is not None else _running_version()

        # Optimistic default, same reasoning as PlexHealth: a real check runs
        # before workers start (app.py's lifespan).
        self.healthy = True
        self.credential_rejected = False
        self.last_success: datetime | None = None
        self.last_error: str | None = None

    def _headers(self) -> dict[str, str]:
        # Imported here rather than at module scope: this module only needs
        # JellyfinApi for its header format, never its transport, and the
        # local import keeps that the only coupling between the two files.
        from autoposter.jellyfin.client import JellyfinApi
        return JellyfinApi(self._http, self._url, self._api_key, self._version).headers()

    async def check_liveness(self) -> None:
        """Poll the configured Jellyfin server. Updates ``healthy`` and
        ``credential_rejected``, and logs healthy/unhealthy transitions."""
        error: str | None = None
        try:
            response = await self._http.get(f"{self._url}/System/Info", headers=self._headers())
            ok = response.is_success
            self.credential_rejected = response.status_code in (401, 403)
            if not ok:
                error = f"status {response.status_code}"
        except httpx.HTTPError as exc:
            # Class name only -- never str(exc): an httpx transport error's
            # str() embeds the request URL (same reasoning as PlexHealth).
            ok = False
            error = type(exc).__name__

        if ok:
            self.last_success = datetime.now(timezone.utc)

        was_healthy = self.healthy
        self.healthy = ok
        self.last_error = error

        if was_healthy and not ok:
            logger.warning("Jellyfin server at %s is unreachable: %s", self._url, error)
        elif not was_healthy and ok:
            logger.info("Jellyfin server at %s is reachable again", self._url)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run the liveness poll until ``stop_event`` is set. Mirrors
        ``PlexHealth.run``'s loop, minus the token-refresh half."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._liveness_interval)
            except asyncio.TimeoutError:
                pass
            else:
                return
            await self.check_liveness()
