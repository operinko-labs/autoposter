"""Authentication for the Web UI's API."""
import time

from sqlalchemy import event, select, text

from autoposter.api.auth import (
    LoginRateLimiter,
    create_session,
    hash_password,
    prune_expired,
    revoke,
    session_for_token,
    verify_password,
)
from autoposter.db.models import Session


def test_a_password_verifies_against_its_hash():
    hashed = hash_password("correct horse")
    assert verify_password("correct horse", hashed) is True
    assert verify_password("Correct horse", hashed) is False


def test_hashing_is_salted():
    """Two hashes of the same password must differ, or the hash file leaks
    which accounts share a password."""
    assert hash_password("same") != hash_password("same")


def test_a_malformed_hash_does_not_raise():
    """A truncated or hand-edited AUTOPOSTER_ADMIN_PASSWORD_HASH must fail
    the login, not 500 the endpoint."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


async def test_a_session_token_is_returned_in_plaintext_once(session):
    token = await create_session(session, ttl_hours=1)
    assert token and len(token) >= 32


async def test_only_the_hash_of_the_token_is_stored(session):
    """A database dump must not hand someone a working session."""
    token = await create_session(session, ttl_hours=1)
    row = (await session.execute(select(Session))).scalar_one()
    assert row.token_hash != token
    assert token not in row.token_hash


async def test_a_valid_token_resolves(session):
    token = await create_session(session, ttl_hours=1)
    assert await session_for_token(session, token) is not None


async def test_an_unknown_token_does_not_resolve(session):
    await create_session(session, ttl_hours=1)
    assert await session_for_token(session, "nope") is None


async def test_an_expired_token_does_not_resolve(session):
    token = await create_session(session, ttl_hours=1)
    await session.execute(text("UPDATE sessions SET expires_at = now() - interval '1 hour'"))
    assert await session_for_token(session, token) is None


async def test_a_revoked_token_does_not_resolve(session):
    token = await create_session(session, ttl_hours=1)
    await revoke(session, token)
    assert await session_for_token(session, token) is None


async def test_two_sessions_are_independent(session):
    first = await create_session(session, ttl_hours=1)
    second = await create_session(session, ttl_hours=1)
    await revoke(session, first)
    assert await session_for_token(session, first) is None
    assert await session_for_token(session, second) is not None


async def test_token_lookup_filters_on_the_hash_in_sql(session):
    """The unique index on token_hash has to do the work: loading every live
    session and comparing in Python is O(sessions) on every authenticated
    request, and that set grows with every login."""
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        token = await create_session(session, ttl_hours=1)
        statements.clear()
        assert await session_for_token(session, token) is not None
    finally:
        event.remove(engine, "before_cursor_execute", record)

    selects = [s for s in statements if "FROM sessions" in s]
    assert selects, "no query against sessions was issued"
    assert all("sessions.token_hash =" in s for s in selects), selects


async def test_prune_expired_removes_only_expired_rows(session):
    live = await create_session(session, ttl_hours=1)
    expired = await create_session(session, ttl_hours=1)
    expired_hash = (
        await session.execute(select(Session.token_hash).order_by(Session.id.desc()).limit(1))
    ).scalar_one()
    await session.execute(
        text("UPDATE sessions SET expires_at = now() - interval '1 hour' WHERE token_hash = :h"),
        {"h": expired_hash},
    )
    await session.commit()

    assert await prune_expired(session) == 1

    remaining = (await session.execute(select(Session))).scalars().all()
    assert len(remaining) == 1
    assert await session_for_token(session, live) is not None
    assert await session_for_token(session, expired) is None


def test_the_rate_limiter_allows_up_to_its_limit_then_refuses():
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False


def test_the_rate_limiter_counts_each_client_separately():
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=60)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    assert limiter.allow("5.6.7.8") is True


def test_the_rate_limiter_forgets_attempts_older_than_its_window():
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=0.01)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False
    time.sleep(0.02)
    assert limiter.allow("1.2.3.4") is True
