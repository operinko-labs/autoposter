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
                "message": self.format(record),
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
