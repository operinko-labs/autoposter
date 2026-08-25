"""The dashboard's push stream: one per-process poller of the database,
fanning a full status+events snapshot to connected viewers as it changes.

This replaces the SPA's 5-second double fetch. It reads the shared database
rather than listening to an in-process event bus because the three
``events_log`` write sites commit independently with no common hook, and this
service is written to tolerate more than one replica -- an in-process bus
would silently miss whatever another replica wrote. Reading the database is
replica-correct by construction and cheaper than what it replaces: one poll
per process while somebody is watching, none at all when nobody is.

NDJSON over a plain streamed response rather than Server-Sent Events, for the
same reason as the log tail: the session travels in an ``Authorization``
header, which ``EventSource`` cannot send. See api/logs.py, whose shape this
module follows deliberately.
"""
import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import TypeAdapter
from starlette.responses import StreamingResponse

from autoposter.api.auth import require_session
from autoposter.api.snapshots import events_snapshot, status_snapshot
from autoposter.db.models import Session as SessionModel

logger = logging.getLogger(__name__)

# How often the database is read while at least one viewer is connected. A
# constant rather than a config knob: it is half the client tick it replaces
# (Dashboard.tsx polled every 5s), and it is now one poll for the whole
# process rather than one per open tab, so the load it can cause does not
# scale with viewers and does not need bounding by an operator.
POLL_SECONDS = 2.0

# Recent events carried with each snapshot -- the count the dashboard renders.
EVENTS_LIMIT = 25

# A snapshot is whole, so a reader that falls behind gains nothing from the
# intermediate ones: a full queue drops the new snapshot and that reader
# catches up on the next change. Small for the same reason.
SUBSCRIBER_QUEUE_SIZE = 16

# How long the stream stays silent before a keepalive line goes out, matching
# the log tail: long enough to be negligible traffic, short enough that a dead
# connection is noticed by the write failing.
HEARTBEAT_SECONDS = 15.0


# Pydantic's own inferring serializer, which is what FastAPI renders the REST
# responses through. See _encode.
_JSONABLE = TypeAdapter(Any)


def _encode(payload: dict, *, sort_keys: bool = False) -> str:
    """One NDJSON line, rendered the way FastAPI renders the REST responses
    this stream mirrors.

    Through pydantic rather than a hand-written ``json.dumps(default=...)``:
    the snapshots carry ``timestamptz`` columns, and pydantic writes UTC as a
    trailing ``Z`` where ``datetime.isoformat`` writes ``+00:00``. The SPA
    renders both payloads with the same code, so "close enough" is not a
    property the two can be allowed to have -- and reproducing the rule by
    hand here would only mean owning a second copy of it.
    """
    return json.dumps(_JSONABLE.dump_python(payload, mode="json"), sort_keys=sort_keys)


class StatusBroadcaster:
    """Polls the database while subscribers are connected and fans a snapshot
    out to each of them whenever it differs from the last one sent.

    **Task lifecycle.** The loop is subscriber-driven: ``subscribe`` starts it
    if it is not already running, ``unsubscribe`` cancels it when the last
    subscriber leaves. Neither method awaits, so both are safe to call from
    the request path, and the loop can therefore still be finishing its
    cancellation when the next ``subscribe`` starts a replacement. A
    generation counter, bumped by every start, is what keeps the two apart:
    each loop carries the generation it was started with and re-checks it
    after every poll, so an outgoing loop can neither publish to the incoming
    one's subscribers nor overwrite the snapshot it built. ``unsubscribe`` of
    a queue that is not subscribed -- a double call, or one from a generator
    closed twice -- discards nothing and leaves the loop alone.

    ``subscribe`` returns the latest snapshot together with the queue, in one
    call, so no snapshot published between a separate read and a separate
    subscribe could fall into the gap. The snapshot is ``None`` only before
    the first poll of a loop generation has completed.

    A tick that raises -- whether building the snapshot or fanning it out --
    is logged and retried on the next one: a database blip must not silently
    end the stream for everyone connected. The loop also re-checks the
    subscriber set every tick, so it stops itself even if the cancellation
    ``unsubscribe`` sends never arrives.
    """

    def __init__(
        self,
        session_factory,
        config_holder,
        scheduler_intervals: dict,
        started_at: datetime | None = None,
        interval_seconds: float = POLL_SECONDS,
        events_limit: int = EVENTS_LIMIT,
    ):
        self._session_factory = session_factory
        # The holder, not the Config it currently holds: a config swap must
        # reach the live stream on its next poll, the same as it reaches
        # /api/status through app.state.config's rebind. See _build_snapshot.
        self._config_holder = config_holder
        # The live mapping create_app publishes, not a copy: the lifespan
        # fills it in place after this object is constructed (see app.py).
        self._scheduler_intervals = scheduler_intervals
        # The process boot instant status_snapshot compares a running job's
        # last_started_at against (see api/snapshots.py). Production always
        # passes app.state.started_at; a caller that does not -- every
        # broadcaster this module's own tests build directly -- gets
        # construction time, which is a harmless stand-in since none of them
        # exercise the derived status field.
        self._started_at = started_at if started_at is not None else datetime.now(UTC)
        self._interval_seconds = interval_seconds
        self._events_limit = events_limit
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._generation = 0
        self._latest: dict | None = None
        self._latest_encoded: str | None = None

    def subscribe(self) -> tuple[dict | None, asyncio.Queue]:
        """The latest snapshot and a queue that receives every change after
        it. Must be called from the event loop -- the queue is bound to it,
        and this is where the poll task is created."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        if self._task is None:
            self._generation += 1
            self._task = asyncio.create_task(self._run(self._generation))
        return self._latest, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)
        if not self._subscribers and self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self, generation: int) -> None:
        while True:
            if not self._subscribers:
                # Nobody is watching. Normally unsubscribe's cancel is what
                # ends this loop; this makes that not the *only* exit. The
                # poll goes through SQLAlchemy's greenlet bridge, and a
                # CancelledError swallowed anywhere in there would otherwise
                # leave the process polling the database forever -- and
                # unrecoverably, since unsubscribe has already dropped its
                # reference to this task.
                return
            try:
                snapshot = await self._build_snapshot()
                if generation != self._generation:
                    # A newer loop owns the broadcaster now; this poll's
                    # result belongs to nobody.
                    return
                self._publish(snapshot)
            except Exception:
                # Never fatal while somebody is still watching: the next tick
                # tries again. This covers the publish as well as the poll --
                # an encoding failure that ended the loop would end it for
                # good, because _task still points here and subscribe only
                # starts a replacement when it is None. CancelledError is a
                # BaseException and passes through here, which is how
                # unsubscribe stops this loop.
                logger.warning("dashboard status poll failed", exc_info=True)
            await asyncio.sleep(self._interval_seconds)

    async def _build_snapshot(self) -> dict:
        # Deref per poll. The only config value in the snapshot is ``workers``,
        # and that one is deliberately reported from the *current* generation
        # even though the pool itself is sized once at startup and needs a
        # restart to change (config/live.py's FROZEN_SECTIONS says so, and the
        # editor renders that): the dashboard and /api/status must agree, and
        # /api/status reads app.state.config, which the swap rebinds.
        async with self._session_factory() as session:
            return {
                "status": await status_snapshot(
                    session, self._config_holder.current, self._scheduler_intervals,
                    self._started_at,
                ),
                "events": await events_snapshot(session, self._events_limit),
            }

    def _publish(self, snapshot: dict) -> None:
        # Encoded rather than compared as dicts so datetimes and the nested
        # lists compare by value without caring about key order; this is also
        # the whole of the change detection, and dropping it would turn the
        # stream back into a 2-second poll pushed at every viewer.
        encoded = _encode(snapshot, sort_keys=True)
        if encoded == self._latest_encoded:
            return
        self._latest_encoded = encoded
        self._latest = snapshot
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(snapshot)
            except asyncio.QueueFull:
                # The stalled reader misses this snapshot and will get the
                # next changed one -- see SUBSCRIBER_QUEUE_SIZE.
                pass


router = APIRouter()


async def ndjson_snapshots(broadcaster: "StatusBroadcaster"):
    """The current snapshot, then every changed one, one JSON object per line,
    forever -- the body of ``GET /api/dashboard/stream``.

    A module-level generator rather than a closure inside the endpoint for the
    reason spelled out in ``api/logs.ndjson_lines``: httpx's ``ASGITransport``
    runs the app to completion before returning a response, so an
    endpoint-level test of a stream that never ends hangs rather than fails.
    The streaming behaviour is exercised against this generator directly.

    The first line is omitted when no poll has completed yet; the reader gets
    the first snapshot the moment it is built.
    """
    snapshot, queue = broadcaster.subscribe()
    try:
        if snapshot is not None:
            yield _encode(snapshot) + "\n"
        while True:
            try:
                snapshot = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield _encode({"heartbeat": True}) + "\n"
                continue
            yield _encode(snapshot) + "\n"
    finally:
        # Reached when the client disconnects (Starlette closes the generator)
        # as well as on any error. Without it a dead reader's queue would stay
        # in the fanout set forever, and -- because the loop runs exactly as
        # long as there are subscribers -- the process would keep polling the
        # database with nobody watching.
        broadcaster.unsubscribe(queue)


@router.get("/dashboard/stream")
async def stream_dashboard(
    request: Request,
    _: SessionModel = Depends(require_session),
) -> StreamingResponse:
    """The dashboard's live status+events feed, as NDJSON."""
    broadcaster: StatusBroadcaster = request.app.state.dashboard_broadcaster

    return StreamingResponse(
        ndjson_snapshots(broadcaster),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            # Tells nginx-style proxies not to buffer the stream; harmless
            # everywhere else.
            "X-Accel-Buffering": "no",
        },
    )
