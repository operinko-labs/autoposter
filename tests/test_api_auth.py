"""Authentication for the Web UI's API."""
from sqlalchemy import select, text

from autoposter.api.auth import (
    create_session,
    hash_password,
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
