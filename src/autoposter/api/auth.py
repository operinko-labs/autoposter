"""Password hashing and opaque database-backed sessions for the API."""
import hashlib
import hmac
import secrets as secrets_module
from datetime import timedelta

import bcrypt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from autoposter.db.models import Session

# Verified on every failed-password path so that whether the deployment has
# a configured admin hash cannot be inferred from response timing -- see
# verify_password's caller in the login endpoint.
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode("ascii")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """False on a wrong password *and* on a malformed hash -- never raises.

    A hand-edited or truncated AUTOPOSTER_ADMIN_PASSWORD_HASH is a realistic
    operator mistake; it must fail the login, not 500 the endpoint.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _token_hash(token: str) -> str:
    # SHA-256, not bcrypt: the token is already high-entropy random
    # (secrets.token_urlsafe(32)), so it needs no key stretching -- unlike
    # the password, which is guessable and uses bcrypt above.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(session: AsyncSession, ttl_hours: float) -> str:
    """Create a session and return its plaintext token. The token is never
    stored -- only its hash is, so a database dump cannot hand out a working
    session."""
    token = secrets_module.token_urlsafe(32)
    row = Session(
        token_hash=_token_hash(token),
        expires_at=func.now() + timedelta(hours=ttl_hours),
    )
    session.add(row)
    await session.commit()
    return token


async def session_for_token(session: AsyncSession, token: str) -> Session | None:
    """Resolve a plaintext token to its session row, or None if it does not
    exist, is expired, or was revoked."""
    token_hash = _token_hash(token)
    rows = (
        (await session.execute(select(Session).where(Session.expires_at > func.now())))
        .scalars()
        .all()
    )
    for row in rows:
        if hmac.compare_digest(row.token_hash, token_hash):
            return row
    return None


async def revoke(session: AsyncSession, token: str) -> None:
    row = await session_for_token(session, token)
    if row is not None:
        await session.delete(row)
        await session.commit()


async def require_session(
    request: Request, authorization: str | None = Header(default=None)
) -> Session:
    """FastAPI dependency: 401s on an absent, malformed, unknown or expired
    token, indistinguishably in every case."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="not authenticated")
    token = authorization.removeprefix("Bearer ")
    session_factory = request.app.state.session_factory
    async with session_factory() as db_session:
        row = await session_for_token(db_session, token)
    if row is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return row


RequireSession = Depends(require_session)
