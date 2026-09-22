"""Event-loop lag monitor (perf spec, Measurement and A9).

Every worker, every request and the dashboard stream share one asyncio event
loop, so anything that runs synchronously on it -- a plexapi call that was
never moved into a thread, an attribute read that triggers plexapi's
synchronous ``_reload()`` -- stalls all of them at once, and nothing recorded
that it happened. This measures it from the inside: a task that asks to be
woken every ``INTERVAL_SECONDS`` and notices when it was woken late. A late
wake-up is a stall by definition -- the loop could not run this task on time
because something else was holding it.

Each WARNING names the scheduled job running at the moment of the late
wake-up. The scheduled passes (``scheduler/core.py``) are the suspected
stallers and the only work with a name to give; ``none`` means the stall came
from somewhere else -- a worker, a request. The lines are the before/after
baseline for Workstream C, greppable in the pod log as ``event loop lag``.

The job is read at the wake-up, which is necessarily AFTER the stall: the
blocking call has returned by the time this task gets the loop back. That is
why ``Scheduler`` keeps ``current_job`` set until the whole claimed run is
over, its result write included -- that write awaits the database, so a job
that stalled the loop is still the one named when this task runs next.

``loop.time()`` rather than ``time.time()``: it is the loop's own monotonic
clock, the one ``asyncio.sleep`` schedules against, so a wall-clock step (this
development host's Docker clock steps backwards every half-minute) can neither
manufacture a stall nor hide one.

Cost: four wake-ups a second and one subtraction each. Nothing is written
unless the loop was actually held.
"""

import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# How long each sleep asks for, and how late a wake-up has to be before it is
# worth a line. The spec's 250/250: anything under a quarter-second late is
# ordinary scheduling jitter under load, and anything over it is long enough
# to have delayed a dashboard frame, a webhook response and a worker's next
# claim at once.
INTERVAL_SECONDS = 0.25
THRESHOLD_SECONDS = 0.25


async def monitor_loop_lag(
    current_job: Callable[[], str | None],
    *,
    interval: float = INTERVAL_SECONDS,
    threshold: float = THRESHOLD_SECONDS,
) -> None:
    """Warn whenever a ``interval``-second sleep wakes more than ``threshold``
    seconds late, naming ``current_job()`` at that moment. Runs until
    cancelled -- the lifespan's shutdown is what ends it.

    ``current_job`` is a callable rather than a value so the name is read at
    the wake-up, not at startup; the lifespan passes
    ``lambda: scheduler.current_job``.
    """
    loop = asyncio.get_running_loop()
    # Once, so an operator can tell "the monitor is running and the loop never
    # stalled" from "the monitor is not running" in a quiet pod's log.
    logger.info(
        "event loop lag monitor started: warns when a %d ms sleep wakes more than %d ms late",
        round(interval * 1000),
        round(threshold * 1000),
    )
    while True:
        due = loop.time() + interval
        await asyncio.sleep(interval)
        late = loop.time() - due
        if late > threshold:
            logger.warning(
                "event loop lag: woke %d ms late; scheduled job running: %s",
                round(late * 1000),
                current_job() or "none",
            )
