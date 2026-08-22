"""The notification sender: one POST per event, bounded retries, containment.

A notification describes work that already finished, so its failure must
never fail that work (the plan's Global Constraints): ``Notifier.send``
returns ``False`` instead of raising, logs exactly one warning naming the
HOST only -- the URL may embed a token in its path, Uptime-Kuma style -- and
records the failure as an ``events_log`` row so the UI's activity feed shows
it. Success logs at debug only.

Construction follows the ``NullMDBListClient`` precedent
(``app.py:_build_mdblist``): ``build_notifier`` returns a ``NullNotifier``
-- never ``None`` -- for a config that cannot send, so callers construct
once and call ``send`` unconditionally. The events-log write borrows the
caller's ``session_factory``, opening a short-lived session per failure --
the same seam every background writer here uses (``Scheduler``,
``ImdbAutoRefresh``).
"""

import asyncio
import logging

import httpx

from autoposter.config.schema import NotificationsConfig
from autoposter.db.models import EventLog
from autoposter.notify.payload import build_payload

logger = logging.getLogger(__name__)

# Backoff before retry attempt n (1-based) is BACKOFF_BASE_SECONDS * 2**(n-1):
# 0.5s, then 1s, then 2s, ...
BACKOFF_BASE_SECONDS = 0.5

# Module-level so tests record the backoff schedule instead of living through
# it (the payload._utcnow precedent).
_sleep = asyncio.sleep


def build_notifier(config: NotificationsConfig, http, session_factory):
    """The real sender when the config can send, otherwise a no-op stand-in.

    Enabled-without-a-URL is a real misconfiguration (``url`` defaults to
    empty), named once here rather than once per send.
    """
    if not config.enabled:
        return NullNotifier()
    if not config.url:
        logger.warning(
            "notifications.enabled is true but notifications.url is empty; "
            "no notifications will be sent"
        )
        return NullNotifier()
    return Notifier(config, http, session_factory)


class NullNotifier:
    """Stand-in used when notifications are off or unconfigured.

    ``send`` answers ``True`` as vacuous success: nothing was owed, so
    nothing failed. ``False`` is reserved for "a notification was owed and
    could not be delivered", so a caller branching on the result never
    treats an intentionally disabled config as a failure.
    """

    async def send(self, event: str, summary: str, detail: dict) -> bool:
        return True


class Notifier:
    """POSTs one payload per event to the configured URL.

    Worst-case duration of one ``send``: ``retry_count`` attempts each
    bounded by ``timeout_seconds``, plus the backoff sleeps between them --
    ``retry_count * timeout_seconds + BACKOFF_BASE_SECONDS *
    (2**(retry_count - 1) - 1)``. With the defaults (3 attempts, 10s
    timeout) that is 3*10 + 0.5*(4-1) = 31.5 seconds.
    """

    def __init__(
        self, config: NotificationsConfig, http: httpx.AsyncClient, session_factory
    ):
        self._url = config.url
        self._mode = config.mode
        self._timeout = httpx.Timeout(config.timeout_seconds)
        self._retry_count = config.retry_count
        self._http = http
        self._session_factory = session_factory
        # The only URL component that may ever be logged or stored.
        try:
            self._host = httpx.URL(config.url).host or "(unknown host)"
        except Exception:  # a malformed URL must not break construction
            self._host = "(unknown host)"

    async def send(self, event: str, summary: str, detail: dict) -> bool:
        """Deliver one notification. Never raises: ``False`` means failed."""
        try:
            return await self._send(event, summary, detail)
        except Exception:
            # Unexpected -- _send already contains transport failures, so
            # reaching here means a bug in OUR code: error, not warning, so it
            # outranks webhook noise in log filtering. The work this
            # notification describes is done; do not fail it.
            logger.error("notification %r failed unexpectedly", event, exc_info=True)
            return False

    async def _send(self, event: str, summary: str, detail: dict) -> bool:
        payload = build_payload(self._mode, event, summary, detail)
        attempts = 0
        failure = "not attempted"
        for attempt in range(self._retry_count):
            if attempt:
                await _sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
            attempts = attempt + 1
            try:
                response = await self._http.post(
                    self._url, json=payload, timeout=self._timeout
                )
            except httpx.HTTPError as exc:
                # Transport-level: the next attempt may find the host back.
                # Leak-safety of logging/storing str(exc) rests on the shared
                # client having no event hooks and this method never calling
                # raise_for_status() -- HTTPStatusError's message embeds the
                # full URL, token and all.
                failure = f"{type(exc).__name__}: {exc}"
                continue
            if response.is_success:
                logger.debug("notification %r delivered to %s", event, self._host)
                return True
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500:
                # A 4xx is a misconfiguration (wrong path, revoked token):
                # retrying cannot help, so fail now.
                break
        logger.warning(
            "notification %r to %s failed after %d attempt(s): %s",
            event,
            self._host,
            attempts,
            failure,
        )
        try:
            await self._record_failure(event, summary, attempts, failure)
        except Exception:
            # The events-log write is best-effort bookkeeping: if the DB is
            # down too, keep the invariant of exactly one WARNING per failed
            # send -- this secondary problem gets a secondary (info) line.
            logger.info(
                "could not record notification failure in events_log",
                exc_info=True,
            )
        return False

    async def _record_failure(
        self, event: str, summary: str, attempts: int, failure: str
    ) -> None:
        # Host only, never the URL: events_log payloads reach the operator
        # through the API and must not carry an embedded token.
        async with self._session_factory() as session:
            session.add(
                EventLog(
                    source="notifier",
                    event_type=event,
                    payload={
                        "summary": summary,
                        "host": self._host,
                        "attempts": attempts,
                        "error": failure,
                    },
                    outcome=f"notification failed after {attempts} attempt(s): {failure}",
                )
            )
            await session.commit()
