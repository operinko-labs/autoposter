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


def _host_of(url: str) -> str:
    """The one URL component that may ever be logged or stored."""
    try:
        return httpx.URL(url).host or "(unknown host)"
    except Exception:  # a malformed URL must not break a send or construction
        return "(unknown host)"


# Strong references to in-flight sends: asyncio holds only a weak reference to
# a created task, so a fire-and-forget send nothing else references could be
# garbage-collected mid-flight. The done-callback drops each reference.
_background_tasks: set = set()


def send_in_background(coroutine) -> None:
    """Fire one ``Notifier.send`` without awaiting it.

    The collections pass must not wait on a webhook: one send's worst case is
    ``retry_count * timeout_seconds`` plus backoff (~31.5s on the defaults),
    and a pass can have several collections to report. ``send`` never raises
    and does its own outcome logging, so the result is deliberately dropped --
    in particular a disabled notifier's vacuous ``True`` is never reported as
    a delivery.
    """
    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_done)


def _background_done(task) -> None:
    _background_tasks.discard(task)
    # send never raises by contract, but an exception a task holds unretrieved
    # becomes a GC-time warning; retrieve and log it here so a misbehaving
    # notifier is named, not leaked.
    if not task.cancelled() and task.exception() is not None:
        logger.warning("notification task failed", exc_info=task.exception())


class NullNotifier:
    """Stand-in used when notifications are off or unconfigured.

    ``send`` answers ``True`` as vacuous success: nothing was owed, so
    nothing failed. ``False`` is reserved for "a notification was owed and
    could not be delivered", so a caller branching on the result never
    treats an intentionally disabled config as a failure.
    """

    async def send(
        self, event: str, summary: str, detail: dict, url: str | None = None
    ) -> bool:
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
        self._host = _host_of(config.url)

    async def send(
        self, event: str, summary: str, detail: dict, url: str | None = None
    ) -> bool:
        """Deliver one notification. Never raises: ``False`` means failed.

        ``url`` overrides the configured target for this send alone -- row
        19's per-collection webhooks, which are read off the collection's
        definition at dispatch time and so are not part of the frozen config
        this notifier was built from. Everything else -- the mode, the
        timeout, the retry policy, the host-only rule -- is the same for
        every target.
        """
        try:
            return await self._send(event, summary, detail, url or self._url)
        except Exception:
            # Unexpected -- _send already contains transport failures, so
            # reaching here means a bug in OUR code: error, not warning, so it
            # outranks webhook noise in log filtering. The work this
            # notification describes is done; do not fail it.
            logger.error("notification %r failed unexpectedly", event, exc_info=True)
            return False

    async def _send(self, event: str, summary: str, detail: dict, url: str) -> bool:
        payload = build_payload(self._mode, event, summary, detail)
        host = self._host if url == self._url else _host_of(url)
        attempts = 0
        failure = "not attempted"
        for attempt in range(self._retry_count):
            if attempt:
                await _sleep(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
            attempts = attempt + 1
            try:
                response = await self._http.post(
                    url, json=payload, timeout=self._timeout
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
                logger.debug("notification %r delivered to %s", event, host)
                return True
            failure = f"HTTP {response.status_code}"
            if response.status_code < 500:
                # A 4xx is a misconfiguration (wrong path, revoked token):
                # retrying cannot help, so fail now.
                break
        logger.warning(
            "notification %r to %s failed after %d attempt(s): %s",
            event,
            host,
            attempts,
            failure,
        )
        try:
            await self._record_failure(event, summary, host, attempts, failure)
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
        self, event: str, summary: str, host: str, attempts: int, failure: str
    ) -> None:
        # Host only, never the URL: events_log payloads reach the operator
        # through the API and must not carry an embedded token. The host is
        # the one this send actually used, which for a per-collection webhook
        # is not the configured target's.
        async with self._session_factory() as session:
            session.add(
                EventLog(
                    source="notifier",
                    event_type=event,
                    payload={
                        "summary": summary,
                        "host": host,
                        "attempts": attempts,
                        "error": failure,
                    },
                    outcome=f"notification failed after {attempts} attempt(s): {failure}",
                )
            )
            await session.commit()
