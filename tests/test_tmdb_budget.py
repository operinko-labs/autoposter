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
from autoposter.facts.tmdb_budget import TmdbRateBudget, TmdbRateLimited
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
    # tell those two apart.
    still_open = (
        await session.execute(
            select(TmdbRateState.blocked_until > func.now() + func.make_interval(0, 0, 0, 0, 0, 30))
        )
    ).scalar_one()
    assert still_open, "Retry-After was ignored in favour of backoff_seconds"


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
