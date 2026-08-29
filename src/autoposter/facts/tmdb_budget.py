"""A shared backoff window for TMDb, on the database clock.

TMDb has no daily cap of the kind MDBList meters (``facts/mdblist.py``'s
``MDBListLimitReached``), but it does answer 429 when a burst is too fast -- and
the drift sweep's whole job is to produce bursts: 500 items a week, each one a
``/movie/{id}`` or ``/tv/{id}`` read (``scheduler/jobs.py:124-195``). Without a
shared window, every worker in every pod discovers the same 429 independently
and keeps discovering it.

**The pattern is ``ImdbMissRefresh``'s**, deliberately and line for line
(``facts/imdb.py:444-513``):

- the window lives in a single database row, so pods behind one database share
  it -- a process variable would give each pod its own;
- both sides of every comparison come from the DATABASE clock (``func.now()``
  and the stored timestamp), never this host's, because the two can disagree;
- the ``asyncio.Lock`` prevents only THIS process from writing two refusals at
  once, and the state is re-read once the lock is held;
- the state row is written whether or not anything else succeeds.

**What this deliberately does NOT do.** It does not retry, sleep or queue. A
blocked ask raises ``TmdbRateLimited`` immediately and the caller keeps
everything else it gathered -- the same shape ``MDBListLimitReached`` already
has at ``facts/gather.py:104-107``. The item comes round again on the drift
sweep, which is a scheduled pass with a batch size, not a hot loop.

**And it never caches the refusal.** Roadmap row 147 records what caching one
costs: MDBList's error body was written into ``provider_cache`` and then served
as an ANSWER for the whole TTL, because the cache stores ``{"found", "payload"}``
and knows nothing about status codes. Here the 429 never reaches a cache write
at all -- ``providers/fetch.fetch_json`` writes only on a 404 and on a 2xx, and
``raise_for_status`` fires before either -- and
``tests/test_tmdb_budget.py::test_a_refusal_body_is_never_cached`` is what keeps
that true.
"""
import asyncio
import logging

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.db.models import TmdbRateState

logger = logging.getLogger(__name__)

__all__ = ["TmdbRateBudget", "TmdbRateLimited", "retry_after_seconds"]

_STATE_ROW_ID = 1


class TmdbRateLimited(Exception):
    """TMDb refused, or is inside the window a refusal opened.

    Its own class rather than an ``httpx`` error, for ``MDBListLimitReached``'s
    reason: the gather catches it and keeps every other field it found. An
    item's critic rating must not be lost because TMDb was busy.
    """


def retry_after_seconds(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, or None when it is absent or unreadable.

    Only the delta-seconds form is read. The HTTP-date form is legal and TMDb
    does not send it; parsing a date here would mean trusting a REMOTE clock to
    set a window this module keeps on the DATABASE clock, which is the one
    mixing of clocks the pattern exists to avoid. An unreadable value falls
    back to the configured backoff, which is the safe direction.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


class TmdbRateBudget:
    """The window, read and written.

    ``backoff_seconds`` is the fallback used when TMDb's refusal carries no
    ``Retry-After``. Zero disables the window entirely -- the same off switch
    ``operations.imdb_miss_refresh_minutes`` already has, and the same meaning:
    with no window, each refusal is simply that one request's failure.
    """

    def __init__(self, session_factory, backoff_seconds: int = 60):
        self._session_factory = session_factory
        self._backoff_seconds = backoff_seconds
        self._lock = asyncio.Lock()

    async def blocked(self) -> bool:
        """Whether TMDb is inside a backoff window, per the database clock."""
        if self._backoff_seconds <= 0:
            return False
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(TmdbRateState.blocked_until, func.now()).where(
                        TmdbRateState.id == _STATE_ROW_ID
                    )
                )
            ).one_or_none()
        if row is None:
            return False
        blocked_until, now = row
        return blocked_until is not None and blocked_until > now

    async def note_refusal(self, retry_after: float | None) -> None:
        """Open (or extend) the window after a 429.

        Written unconditionally, ``_record_miss_refresh_attempt``'s reasoning:
        a provider that keeps refusing must not turn into a retry storm, so the
        window is recorded even when the caller is about to raise.
        """
        if self._backoff_seconds <= 0:
            return
        seconds = retry_after if retry_after is not None else self._backoff_seconds
        async with self._lock:
            async with self._session_factory() as session:
                # Database clock on both sides, and the interval built by
                # Postgres rather than by Python's ``timedelta``: the value has
                # to be added to ``now()`` inside the statement.
                until = func.now() + func.make_interval(0, 0, 0, 0, 0, 0, seconds)
                stmt = insert(TmdbRateState).values(
                    id=_STATE_ROW_ID, blocked_until=until
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={"blocked_until": until, "refused_at": func.now()},
                )
                await session.execute(stmt)
                await session.commit()
        logger.warning(
            "tmdb refused with 429; not asking again for %ss", seconds
        )
