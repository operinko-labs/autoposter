"""The event-loop lag monitor (src/autoposter/loop_lag.py, perf spec A9).

Each test stalls the loop on purpose with ``time.sleep`` -- the shape of the
defect the monitor exists to catch: a synchronous call on the one loop every
worker, request and stream shares. Nothing here asserts on elapsed wall time,
only on what was logged. The stall is longer than the monitor's period plus
its threshold, so however the stall and the monitor's sleep line up, the
wake-up is at least ``STALL_SECONDS - INTERVAL_SECONDS`` late -- 350 ms against
a 250 ms threshold.
"""
import asyncio
import contextlib
import logging
import time

from autoposter.loop_lag import INTERVAL_SECONDS, THRESHOLD_SECONDS, monitor_loop_lag

LOGGER = "autoposter.loop_lag"
STALL_SECONDS = INTERVAL_SECONDS + THRESHOLD_SECONDS + 0.1


def lag_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.WARNING
    ]


async def stall_until_warned(caplog) -> None:
    """Let the monitor reach its first sleep, block the loop, then wait (on the
    loop, bounded) for the WARNING that stall must produce."""
    await asyncio.sleep(0)
    time.sleep(STALL_SECONDS)
    async with asyncio.timeout(5):
        while not lag_warnings(caplog):
            await asyncio.sleep(0.01)


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_a_stall_is_logged_naming_the_scheduled_job_running_at_the_time(caplog):
    monitor = asyncio.create_task(monitor_loop_lag(lambda: "plex_prune"))
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await stall_until_warned(caplog)
    finally:
        await stop(monitor)

    warnings = lag_warnings(caplog)
    assert warnings, "a 600 ms stall of the loop went unreported"
    assert all(message.startswith("event loop lag: woke ") for message in warnings), warnings
    assert any("scheduled job running: plex_prune" in message for message in warnings), warnings


async def test_a_stall_with_no_scheduled_job_running_says_none(caplog):
    """``none`` is itself the finding: the stall came from a worker or a
    request, not from the scheduler."""
    monitor = asyncio.create_task(monitor_loop_lag(lambda: None))
    try:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            await stall_until_warned(caplog)
    finally:
        await stop(monitor)

    assert any("scheduled job running: none" in m for m in lag_warnings(caplog)), (
        lag_warnings(caplog)
    )


async def test_an_on_time_loop_logs_only_the_start_line(caplog):
    """The threshold is what separates a stall from ordinary scheduling
    jitter. A 60-second threshold cannot be crossed by an unblocked loop, so
    any WARNING here would mean the comparison is wrong, not that the host is
    slow. The INFO line is what an operator greps for to see the monitor is
    alive at all in a pod whose loop never stalls."""
    monitor = asyncio.create_task(
        monitor_loop_lag(lambda: "plex_prune", interval=0.01, threshold=60.0)
    )
    try:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            await asyncio.sleep(0.2)
    finally:
        await stop(monitor)

    assert lag_warnings(caplog) == []
    assert [
        r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.INFO
    ] == ["event loop lag monitor started: warns when a 10 ms sleep wakes more than 60000 ms late"]
