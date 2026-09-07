"""Queue-test helpers that survive this machine's backwards clock steps.

Why this exists. ``queue/jobs.py``'s claim predicate is ``run_after <= now()``
(``_CLAIM_SQL``, jobs.py:16-35) and ``now()`` is ``transaction_timestamp()``.
``enqueue`` commits (jobs.py:98), so ``claim`` (jobs.py:175-179) evaluates that
predicate in a LATER transaction, against a SECOND reading of the clock. This
development host's clock steps backwards by 2.705 s every ~30 s of real time
(docs/research/dev-clock-step/README.md). A step landing in that window makes a
row stamped a millisecond ago not-yet-due: nothing is claimed, the handler never
runs, and the test fails on whatever the handler should have written. That one
mechanism accounts for nine sightings across roadmap rows 119, 193 and 208.

Every helper here therefore backdates ``run_after`` by an HOUR. An hour is three
orders of magnitude larger than the step, and it is still "due" -- nothing in the
queue distinguishes how due a row is. The arithmetic is done by Postgres, inside
the same transaction that writes it, so no clock reading crosses a commit.

Why a plain module and not fixtures in ``conftest.py``: these are called
mid-test, several times per test, against whichever session the test is already
holding -- and ``conftest.py`` is being edited on another branch (row 121),
which this file deliberately stays clear of.

The value is read back and assigned rather than left as a SQL expression on the
attribute so the session's identity map keeps a real datetime: several callers
hold the ``Job`` instance across the call, and a lingering expression object
there fails in a way that reads like a defect in the code under test.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import Job
from autoposter.queue.jobs import enqueue

# Hours. Named rather than inlined so the guard's failure message and row 208's
# cell can both point at one number.
DUE_BACKDATE_HOURS = 1


async def _backdate(session: AsyncSession, job_id: int, *, reset_state: bool) -> None:
    job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()
    if reset_state:
        job.state = "pending"
    job.run_after = (
        await session.execute(
            select(func.now() - func.make_interval(0, 0, 0, 0, DUE_BACKDATE_HOURS))
        )
    ).scalar_one()
    await session.commit()


async def make_due(session: AsyncSession, job_id: int) -> None:
    """Reset a job to ``pending`` and make it due, with an hour of margin."""
    await _backdate(session, job_id, reset_state=True)


async def bring_horizon_forward(session: AsyncSession, job_id: int) -> None:
    """Make a job due WITHOUT touching its state, with an hour of margin.

    The deferral tests are about ``claim()`` picking a ``deferred`` row up by
    itself once ``run_after`` passes; flipping the row to ``pending`` first
    would hide the very thing they assert.
    """
    await _backdate(session, job_id, reset_state=False)


async def enqueue_due(
    session: AsyncSession, kind: str, payload: dict, dedupe_key: str | None = None
) -> int | None:
    """``enqueue`` a job that this test intends to claim immediately.

    Returns exactly what ``enqueue`` returns, ``None`` on a debounce included,
    so it is a drop-in at any site that does not pass ``delay_seconds`` -- a
    site that passes one is asserting about the horizon and must keep the real
    ``enqueue``.
    """
    job_id = await enqueue(session, kind, payload, dedupe_key=dedupe_key)
    if job_id is not None:
        await _backdate(session, job_id, reset_state=False)
    return job_id
