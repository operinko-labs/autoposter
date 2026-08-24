"""Shared scaffolding for the bulk artwork modes (Phase 7b).

Every mode is an operator-triggered job fanned to the worker pool via its own
``job.kind`` (see ``queue/worker.py``'s dispatch map). This module holds the
three things all five modes build on:

* :class:`WorkerPause` -- the in-process fence a Plex-writing mode raises so the
  live render pipeline cannot race it while it uploads to Plex;
* :func:`refuse_if_implausible` -- the plausibility cap that refuses an
  implausibly large operation with the real numbers;
* :func:`refuse_if_empty` -- the empty-table guard that refuses to operate on a
  table that is empty only because a restore has not finished.

Both guards mirror the ``cleanup`` sweep precedent
(``scheduler/jobs.py::_implausible_orphan_count`` and its empty-``renders``
check) exactly, so the modes and the periodic cleanup refuse in the same shape.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# The share cap needs a large enough sample to mean anything: "2 of 3 items
# would change" is a normal small operation, not evidence of a misconfigured
# filter. Below this many candidate items only the absolute cap applies.
# Mirrors ``scheduler/jobs.py::SHARE_CHECK_MIN_DIRS``.
SHARE_CHECK_MIN_ITEMS = 20


def refuse_if_implausible(count: int, total: int, cap: int, share: float) -> str | None:
    """Return a refusal summary if ``count`` changes cannot be believed.

    The shared plausibility cap for every Plex-writing mode, modelled on the
    cleanup sweep's ``_implausible_orphan_count``. Two caps, either of which
    trips: the absolute one (``cap``) catches a large library where a filter or
    a mount went wrong; the share one (``share``) catches a small library,
    where any absolute cap high enough to be useful on the large one would
    never fire. Both report the real numbers, because the operator's next
    question is always "how far off is it".
    """
    if count and count > cap:
        return (
            f"refused: {count} of {total} item(s) would change, more than the "
            f"safety cap of {cap}; this usually means a filter, a mount or the "
            "backup tree is wrong -- change nothing"
        )
    ratio = count / total if total else 0.0
    if total >= SHARE_CHECK_MIN_ITEMS and ratio > share:
        return (
            f"refused: {count} of {total} item(s) would change ({ratio:.0%}), "
            f"more than the safety cap of {share:.0%}; this usually means a "
            "filter, a mount or the backup tree is wrong -- change nothing"
        )
    return None


async def refuse_if_empty(session: AsyncSession, model, *, table_name: str) -> str | None:
    """Return a refusal summary if ``model``'s table has no rows.

    The empty-table guard every mode runs first, mirroring the cleanup sweep's
    empty-``renders`` check. An empty ``media_items`` or ``renders`` would make
    a mode read "nothing to compare against" as "everything qualifies", so it
    refuses outright rather than operating on a table that is empty only
    because a restore has not finished.
    """
    any_row = (await session.execute(select(model.id).limit(1))).first()
    if any_row is None:
        return (
            f"refused: the {table_name} table is empty, so there is nothing to "
            "operate on; change nothing"
        )
    return None


class WorkerPause:
    """An in-process fence that idles the worker pool while a mode writes to Plex.

    A restore or reset pushes artwork to the same Plex items the live render
    pipeline does. To keep the two off each other, the mode :meth:`pause`\\ s the
    pool for the duration of its writes and :meth:`resume`\\ s in a ``finally``;
    the :meth:`paused` context manager does both. ``run_worker`` checks
    :attr:`is_paused` before it claims a job and idles (it does not busy-spin)
    until the fence clears -- a worker already mid-job finishes that job, then
    idles, so in-flight work is never interrupted.

    Scope and known limitation: this is an ``asyncio.Event`` living on one
    process's ``app.state``, so it fences only this replica's pool. A second
    replica's workers are NOT paused, and neither is the database-level
    ``claim_due`` SELECT itself -- another replica could still claim and run a
    job during a restore. That is an accepted limitation of the single-operator
    deployment this targets (one API replica runs the workers); a multi-replica
    deployment would need a database-level pause instead.
    """

    def __init__(self) -> None:
        # ``set()`` == paused. Constructed without a running loop (fine on
        # 3.10+); the pool that awaits it runs inside the lifespan's loop.
        self._event = asyncio.Event()

    @property
    def is_paused(self) -> bool:
        return self._event.is_set()

    def pause(self) -> None:
        self._event.set()

    def resume(self) -> None:
        self._event.clear()

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """Pause for the duration of the block, resuming even on error."""
        self.pause()
        try:
            yield
        finally:
            self.resume()


@runtime_checkable
class ModeJob(Protocol):
    """The contract every bulk artwork mode conforms to.

    A mode is constructed from the effective config and the trigger's filters,
    then :meth:`run` against a session. It returns a human-readable summary --
    dry run vs applied, plus the counts -- exactly as the scheduler jobs do
    (``scheduler/jobs.py``). It is dispatched to the worker pool via its
    ``job.kind``; the worker's dispatch map (``queue/worker.py``) adapts
    :meth:`run` to the ``(session, job)`` handler signature.
    """

    async def run(self, session: AsyncSession) -> str: ...
