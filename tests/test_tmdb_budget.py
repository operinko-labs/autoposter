"""The TMDb rate budget: a shared backoff window on the database clock.

Built on ``ImdbMissRefresh``'s pattern (``facts/imdb.py:444-513``) -- the DB
clock so multiple pods share one window, an ``asyncio.Lock`` for the
intra-process race, and a state row written whether or not anything succeeds.
The difference is what triggers it: IMDb's cooldown is triggered by a MISS, and
this one by TMDb's own 429.
"""
import httpx
import pytest
from sqlalchemy import func, insert, select

from conftest import session_factory_for
from autoposter.db.models import ProviderCache as ProviderCacheRow, TmdbRateState
from autoposter.facts.tmdb_budget import (
    _MAX_BACKOFF_SECONDS,
    TmdbRateBudget,
    TmdbRateLimited,
    retry_after_seconds,
)
from autoposter.facts.tmdb_facts import TMDBFactsClient
from autoposter.providers.cache import ProviderCache


async def test_a_fresh_budget_is_not_blocked(session):
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    assert await budget.blocked() is False


async def test_a_refusal_blocks_the_next_ask(session):
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    await budget.note_refusal(None)
    assert await budget.blocked() is True


async def test_a_window_that_has_passed_does_not_block(session):
    """The window is compared against the DATABASE clock, not this process's --
    the rule ``facts/imdb.py``'s ``_is_stale``/``_miss_refresh_due`` state and
    this module keeps."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=0)
    await budget.note_refusal(None)
    assert await budget.blocked() is False


async def test_a_stored_window_already_in_the_past_does_not_block(session):
    """The other half of the same rule, and the one the off-switch above cannot
    reach: a window that a *previous* refusal opened and that has since closed.

    The row is written directly, with a ``blocked_until`` the database itself
    places an hour in the past, so the comparison under test is the real
    ``blocked_until > now()`` branch rather than the ``backoff_seconds <= 0``
    short-circuit.
    """
    await session.execute(
        insert(TmdbRateState).values(
            id=1, blocked_until=func.now() - func.make_interval(0, 0, 0, 0, 1)
        )
    )
    await session.commit()
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    assert await budget.blocked() is False


async def test_retry_after_wins_over_the_configured_backoff(session):
    """TMDb said how long; believe it rather than the local default."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=1)
    await budget.note_refusal(3600)
    row = (await session.execute(select(TmdbRateState))).scalar_one()
    assert row.blocked_until is not None
    assert await budget.blocked() is True
    # The window really is the hour TMDb asked for and not the configured
    # second: a second-long window would already have closed by the time the
    # database compared it, and an assertion that only reads "blocked" cannot
    # tell those two apart. The margin below is 30 MINUTES -- make_interval's
    # sixth positional argument is mins, not secs -- comfortably inside the
    # honoured 3600s window and comfortably past anything a second-long window
    # could reach.
    still_open = (
        await session.execute(
            select(TmdbRateState.blocked_until > func.now() + func.make_interval(0, 0, 0, 0, 0, 30))
        )
    ).scalar_one()
    assert still_open, "Retry-After was ignored in favour of backoff_seconds"


async def test_a_shorter_refusal_does_not_shorten_an_open_window(session):
    """The multi-pod trace: pod A's honoured 3600s window must survive
    pod B's no-Retry-After 429 landing second and falling back to the
    configured 60s. Before the fix, ``note_refusal`` overwrote unconditionally
    and this collapsed the hour to a minute regardless of which pod won the
    race -- this test writes the longer window first and the shorter one
    second, the exact order that trace describes."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    await budget.note_refusal(3600)
    await budget.note_refusal(None)
    still_open = (
        await session.execute(
            select(TmdbRateState.blocked_until > func.now() + func.make_interval(0, 0, 0, 0, 0, 30))
        )
    ).scalar_one()
    assert still_open, "a later, shorter refusal collapsed the longer window"


async def test_the_longer_window_wins_regardless_of_write_order(session):
    """The other ordering: the shorter (fallback) refusal lands first, then
    the honoured Retry-After arrives. ``GREATEST`` makes the outcome the same
    either way, which is the whole point of writing it instead of an
    unconditional overwrite."""
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    await budget.note_refusal(None)
    await budget.note_refusal(3600)
    still_open = (
        await session.execute(
            select(TmdbRateState.blocked_until > func.now() + func.make_interval(0, 0, 0, 0, 0, 30))
        )
    ).scalar_one()
    assert still_open, "the longer refusal did not survive"


def test_retry_after_seconds_rejects_a_non_finite_value():
    """``float("inf")`` parses and passes a bare ``> 0`` guard, which would
    otherwise reach Postgres's ``make_interval`` as ``Infinity`` and raise a
    DBAPI error ``_get``'s ``except httpx.HTTPStatusError`` does not catch."""
    response = httpx.Response(429, headers={"Retry-After": "inf"})
    assert retry_after_seconds(response) is None


def test_retry_after_seconds_caps_an_absurd_value():
    """A hostile or broken intermediary's ``Retry-After`` must not write a
    window that outlives the process which could otherwise clear it -- the
    state row is persistent and its only off switch is frozen."""
    response = httpx.Response(429, headers={"Retry-After": "999999999"})
    assert retry_after_seconds(response) == _MAX_BACKOFF_SECONDS


def test_retry_after_seconds_still_reads_an_ordinary_value():
    response = httpx.Response(429, headers={"Retry-After": "120"})
    assert retry_after_seconds(response) == 120.0


async def test_a_429_notes_the_refusal_and_raises_its_own_class(session):
    """Its own class, like ``MDBListLimitReached``: the gather catches it and
    keeps everything else it found, rather than losing a whole item's facts to
    one provider's budget."""
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"status": 25})

    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(940143)

    assert await budget.blocked() is True


async def test_a_refusal_body_is_never_cached(session):
    """Roadmap row 147's MDBList defect, refused here by construction.

    A cached 429 body would be served as an ANSWER for the whole TTL -- the
    provider cache stores ``{"found": …, "payload": …}`` and knows nothing
    about status codes. ``fetch_json`` writes the cache only on a 404 and on a
    2xx, and ``raise_for_status`` fires before either; this is the test that
    keeps it that way.
    """
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "1"}, json={"status": 25})

    cache = ProviderCache(session_factory_for(session))
    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, cache=cache, cache_ttl_seconds=3600,
                                 budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(940143)

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_a_blocked_budget_costs_no_request_at_all(session):
    """The point of the window: the second item in the queue must not re-ask."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, json={"status": 25})

    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, budget=budget)
        with pytest.raises(TmdbRateLimited):
            await client.movie(1)
        with pytest.raises(TmdbRateLimited):
            await client.movie(2)

    assert len(calls) == 1, "the second ask should not have reached the network"


async def test_a_non_429_error_opens_no_window_and_raises_as_it_did(session):
    """429 ONLY. A 500 is not a budget: opening a window on one would stop the
    whole library being read because one title's record is broken."""
    def handler(request):
        return httpx.Response(500, json={"status": 11})

    budget = TmdbRateBudget(session_factory_for(session), backoff_seconds=60)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TMDBFactsClient("tok", http, budget=budget)
        with pytest.raises(httpx.HTTPStatusError):
            await client.movie(1)

    assert await budget.blocked() is False


async def test_no_budget_configured_is_todays_behaviour_exactly(session):
    """``budget=None`` must leave the client as it shipped: a 429 raises
    httpx's own error out of ``raise_for_status``. A knob that changes
    behaviour when it is switched off is not a knob."""
    def handler(request):
        return httpx.Response(429, json={"status": 25})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await TMDBFactsClient("tok", http).movie(1)
