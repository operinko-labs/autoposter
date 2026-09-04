import asyncio
import logging
from datetime import datetime, timezone

import httpx
from plexapi.myplex import MyPlexAccount

logger = logging.getLogger(__name__)


class PlexHealth:
    """Tracks two independent Plex signals.

    Server reachability (``check_liveness``) gates job claiming — see
    ``run_worker`` in ``queue/worker.py``. It hits the configured Plex server
    itself via a cheap, unauthenticated ``GET {url}/identity``.

    Token refresh (``refresh_token``) talks to plex.tv, not the user's server,
    via ``plexapi.myplex.MyPlexAccount.ping()``. The two fail independently: a
    self-hosted server can be perfectly reachable while plex.tv is down, or
    the token can be server-scoped rather than account-scoped. A failure here
    is therefore logged as a warning and otherwise ignored — it must never
    flip ``healthy`` or stop job processing.
    """

    def __init__(
        self,
        url: str,
        token: str,
        http: httpx.AsyncClient,
        liveness_interval: float = 60,
        refresh_interval: float = 12 * 3600,
        refresh_enabled: bool = True,
    ):
        self._url = url.rstrip("/")
        self._token = token
        self._http = http
        self._liveness_interval = liveness_interval
        self._refresh_interval = refresh_interval
        self._refresh_enabled = refresh_enabled

        # Optimistic default: a real check runs before workers start (see
        # app.py's lifespan), so this initial value is never actually relied on.
        self.healthy = True
        self.last_success: datetime | None = None
        self.last_error: str | None = None

    async def check_liveness(self) -> None:
        """Poll the configured Plex server. Updates ``healthy`` and logs transitions."""
        # ``error`` is what ``last_error`` holds -- a field nothing serves
        # today, kept class-name-only so it is safe to serve later: an httpx
        # error's str() embeds the request URL. ``logged`` carries the message
        # too, for the transition WARNING below only (the pod log is the
        # trusted sink, roadmap row 207).
        error: str | None = None
        logged: str | None = None
        try:
            response = await self._http.get(f"{self._url}/identity")
            ok = response.is_success
            if not ok:
                error = logged = f"status {response.status_code}"
        except httpx.HTTPError as exc:
            ok = False
            error = type(exc).__name__
            logged = f"{error}: {exc}"

        if ok:
            self.last_success = datetime.now(timezone.utc)

        was_healthy = self.healthy
        self.healthy = ok
        self.last_error = error

        # Log transitions only, not every poll — an outage should produce two
        # log lines (down, then back up), not one per liveness interval.
        if was_healthy and not ok:
            logger.warning("Plex server at %s is unreachable: %s", self._url, logged)
        elif not was_healthy and ok:
            logger.info("Plex server at %s is reachable again", self._url)

    async def refresh_token(self) -> None:
        """Ping plex.tv to keep the token from expiring from disuse.

        Never allowed to affect ``healthy`` or raise — a plex.tv wobble or a
        server-scoped token must not touch job processing, so any failure is
        just logged as a warning.
        """
        if not self._refresh_enabled:
            return
        try:
            await asyncio.to_thread(self._ping_sync)
        except Exception as exc:  # noqa: BLE001 - deliberately never propagates
            logger.warning("Plex token refresh failed (ignored): %s", exc)

    def _ping_sync(self) -> None:
        MyPlexAccount(token=self._token).ping()

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run both periodic checks until ``stop_event`` is set."""
        await asyncio.gather(
            self._liveness_loop(stop_event),
            self._refresh_loop(stop_event),
        )

    async def _liveness_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._liveness_interval)
            except asyncio.TimeoutError:
                pass
            else:
                return
            await self.check_liveness()

    async def _refresh_loop(self, stop_event: asyncio.Event) -> None:
        if not self._refresh_enabled:
            return
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._refresh_interval)
            except asyncio.TimeoutError:
                pass
            else:
                return
            await self.refresh_token()
