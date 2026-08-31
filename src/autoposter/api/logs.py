"""The live log endpoints: a bounded in-process capture of the pod's own log
stream, served as a snapshot and as an NDJSON tail.

This exists because the only complete run log is the process's stdout, and
"kubectl logs -f" is not an answer the Web UI can give. The buffer is not a
second log store: it holds the most recent ``capacity`` lines in memory,
nothing is persisted, and a restart starts empty -- history beyond the buffer
belongs to the cluster's log pipeline, not this service.

The handler is attached to the root logger by the app lifespan (app.py), and
only there: attaching in ``create_app`` would stack one handler per app
instance onto the process-global root logger, which in the test suite means
every earlier test's app still capturing the later tests' lines.
"""
import asyncio
import collections
import json
import logging
import re
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import StreamingResponse

from autoposter.api.auth import require_session
from autoposter.db.models import Session as SessionModel

DEFAULT_LOGS_LIMIT = 200

# One slow reader must not cost the process memory without bound; a full
# queue drops that subscriber's oldest-undelivered lines (they still have the
# snapshot endpoint and the buffer to catch up from).
SUBSCRIBER_QUEUE_SIZE = 500

# How long the stream stays silent before a keepalive line goes out. Long
# enough to be negligible traffic, short enough that a dead connection is
# noticed by the write failing rather than lingering for hours.
HEARTBEAT_SECONDS = 15.0

# Roadmap row 117: Fanart's api_key rides the query string, httpx embeds full
# URLs in HTTPStatusError messages, and ladder.py:81 plus api/candidates.py's
# two fan-out sites log provider failures with exc_info -- so a credential
# could reach this buffer inside a traceback's last line and be served by
# /api/logs and the stream. Scrubbed HERE, where the served surface's text is
# assembled, rather than at the logging sites: stdout (the pod log) keeps the
# full traceback under the repo's trusted-sink rule (row 207's decision), and
# a future exc_info site is covered without knowing about this module. Matches
# any query-param name ending in api_key/api-key/apikey/token (X-Plex-Token,
# plex_token, ...); the name survives, the value never does. The over-match is
# deliberate: a param whose name merely ENDS that way (e.g. a hypothetical
# next_token) is redacted too, trading a few false positives for never missing
# a credential (row 117's close records the same tradeoff). Row 207 added the
# api-key spelling and the %3D alternation -- a URL nested inside another
# URL's query value carries its '=' percent-encoded, and the encoded form
# must redact exactly like the literal one.
_CREDENTIAL_PARAM = re.compile(r"(?i)([-\w]*(?:api[-_]?key|token))(=|%3D)[^&\s'\"]+")

# Roadmap row 207's other half: the credential pattern redacts the PARAM and
# leaves the host, so /api/logs still served the operator's base URL
# (http://plex.internal:32400/...) -- main.py's connect failure, the health
# probe's unreachable/reachable lines and any traceback carrying a request URL
# all land in this buffer. The authority is redacted, the path deliberately
# kept: the path is what makes the line debuggable, and the operator-URL
# clause is about hosts. Public API hosts are redacted too -- row 117's
# over-match tradeoff taken the same way on purpose: never miss an operator
# host, and the pod log keeps the full line under the trusted-sink decision.
# Row 212 added the encoded-scheme alternation -- a URL nested inside another
# URL's query value carries its :// as %3A%2F%2F, and after row 207 the
# nested credential was redacted while the nested host beside it was not.
# The tempered class stops the encoded match at %2F so the nested URL's
# encoded path survives exactly as the plain path does. This buys exactly one
# level: %253A (double-encoded) still passes, the same accepted residual the
# row records -- no shipped provider or client produces it, and the pod log
# keeps the full line either way.
_URL_HOST = re.compile(r"(?i)\b(https?(?:://|%3A%2F%2F))(?:(?!%2F)[^\s/?#'\"<>])+")

# Roadmap row 214: requests formats its own connection target scheme-less and
# keyword-form -- HTTPConnectionPool(host='plex.internal', port=32400) -- so
# nothing ://-anchored can ever match it, and it is LIVE: worker.py's
# "waiting for Plex" INFO line (row 209's own compensating control) and every
# requests-flavored exc_info traceback carry it into this buffer. The literal
# host=/port= tokens are the anchors, so there is no false-positive surface
# to speak of; the keyword names and the quotes survive, the values never do
# (row 117's rule). The bare scheme-less host:port shape is deliberately NOT
# matched -- it is not the live carrier, and a naive rule eats timestamps,
# ratios and this repo's own file.py:N citations; row 214's close files it
# forward.
_HOST_KEYWORD = re.compile(r"(?i)\b(host=)(['\"]?)[^\s,'\")]+\2")
_PORT_KEYWORD = re.compile(r"(?i)\b(port=)\d+")


def _scrub(text: str) -> str:
    """All patterns in sequence: credential params, then URL authorities,
    then the scheme-less keyword host/port shapes requests itself writes."""
    text = _CREDENTIAL_PARAM.sub(r"\1\2REDACTED", text)
    text = _URL_HOST.sub(r"\1REDACTED", text)
    text = _HOST_KEYWORD.sub(r"\1\2REDACTED\2", text)
    return _PORT_KEYWORD.sub(r"\1REDACTED", text)


class LogBuffer(logging.Handler):
    """A ``logging.Handler`` holding the last ``capacity`` formatted records
    and fanning new ones out to live subscribers.

    ``emit`` runs on whatever thread logs -- the event loop's thread for most
    of this application, but also worker threads via ``asyncio.to_thread``
    (bcrypt, ImageMagick). The deque append happens under a lock on the
    emitting thread; subscriber queues are asyncio objects and are only
    touched on the loop, via ``call_soon_threadsafe``. The loop reference is
    captured when the first subscriber arrives, because a handler attached at
    process start has no running loop to ask.

    ``uvicorn.access`` is excluded at the source: with access lines included
    the log view would be mostly a mirror of the viewer's own requests --
    every page the operator opens, every stream they hold open -- rather than
    a record of what the service did.
    """

    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.setFormatter(logging.Formatter("%(message)s"))
        self._lines: collections.deque = collections.deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("uvicorn.access"):
            return
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                # format() appends the traceback when the record carries one.
                "message": _scrub(self.format(record)),
            }
        except Exception:
            self.handleError(record)
            return
        with self._lock:
            self._lines.append(entry)
            loop = self._loop
            has_subscribers = bool(self._subscribers)
        if loop is not None and has_subscribers:
            # A subscriber arriving between the append above and the scheduled
            # fanout sees this entry twice: once in its backlog, once from the
            # queue. One duplicated line at connect time, accepted -- closing
            # the window would mean holding the lock across the loop hop.
            #
            # Outside the try/handleError guard above deliberately: a closed
            # loop here means the lifespan failed to detach this handler
            # before shutting the loop down, which should surface, not be
            # swallowed as a formatting error.
            loop.call_soon_threadsafe(self._fanout, entry)

    def _fanout(self, entry: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                # The slow reader loses this line; the buffer keeps it.
                pass

    def lines(self, limit: int | None = None) -> list[dict]:
        with self._lock:
            snapshot = list(self._lines)
        if limit is not None:
            snapshot = snapshot[-limit:]
        return snapshot

    def subscribe(self) -> tuple[list[dict], asyncio.Queue]:
        """The current backlog and a queue that receives everything after it.

        One method rather than ``lines()`` + a separate subscribe, so no line
        emitted between the two calls can fall into the gap. Must be called
        from the event loop -- the queue is bound to it, and this is where the
        loop reference ``emit`` fans out through gets captured.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            backlog = list(self._lines)
            self._subscribers.add(queue)
        return backlog, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)


router = APIRouter()


@router.get("/logs")
async def get_logs(
    request: Request,
    limit: int = Query(default=DEFAULT_LOGS_LIMIT, ge=1),
    _: SessionModel = Depends(require_session),
) -> dict:
    buffer: LogBuffer = request.app.state.log_buffer
    # The cap is the buffer's own capacity: asking for more than it holds is
    # not an error, there is simply nothing more to give.
    return {"lines": buffer.lines(limit=limit)}


async def ndjson_lines(buffer: "LogBuffer"):
    """The backlog, then every new line as it is logged, one JSON object per
    line, forever -- the body of ``GET /api/logs/stream``.

    A module-level generator rather than a closure inside the endpoint so the
    tests can drive it directly. httpx's ``ASGITransport`` runs the app to
    completion before it returns a response, so an endpoint-level test of a
    stream that never ends does not fail -- it hangs until the runner is
    killed. Endpoint-level tests here cover only what terminates (the 401,
    the response's shape); the streaming behaviour is exercised against this
    generator with bounded iteration and ``aclose()``.

    A ``{"heartbeat": true}`` line goes out after HEARTBEAT_SECONDS of
    silence, so a broken connection fails the next write instead of holding
    the subscription open indefinitely.
    """
    backlog, queue = buffer.subscribe()
    try:
        for entry in backlog:
            yield json.dumps(entry) + "\n"
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield json.dumps({"heartbeat": True}) + "\n"
                continue
            yield json.dumps(entry) + "\n"
    finally:
        # Reached when the client disconnects (Starlette closes the generator)
        # as well as on any error: without it every dead reader's queue would
        # stay in the fanout set, costing a put_nowait per line forever.
        buffer.unsubscribe(queue)


@router.get("/logs/stream")
async def stream_logs(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> StreamingResponse:
    """The live log tail, as NDJSON.

    NDJSON over a plain streamed response rather than Server-Sent Events
    because the session travels in an ``Authorization`` header (see
    api/auth.py), which ``EventSource`` cannot send -- the SPA reads this with
    ``fetch`` and a stream reader either way, and NDJSON is the simpler frame.
    """
    buffer: LogBuffer = request.app.state.log_buffer

    return StreamingResponse(
        ndjson_lines(buffer),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            # Tells nginx-style proxies not to buffer the stream; harmless
            # everywhere else.
            "X-Accel-Buffering": "no",
        },
    )
