"""Password hashing and opaque database-backed sessions for the API."""
import hashlib
import hmac
import secrets as secrets_module
import time
from datetime import timedelta

import bcrypt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from autoposter.db.models import Session


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
    exist, is expired, or was revoked.

    The hash is matched in SQL so the unique index on ``token_hash`` does the
    work -- loading every live session and comparing in Python costs
    O(sessions) on every authenticated request. The constant-time comparison
    is kept on the single row that comes back: what is compared there is a
    SHA-256 of the presented token against a stored SHA-256, so an attacker
    who could time it learns nothing they did not already supply.
    """
    token_hash = _token_hash(token)
    row = (
        await session.execute(
            select(Session).where(
                Session.token_hash == token_hash, Session.expires_at > func.now()
            )
        )
    ).scalar_one_or_none()
    if row is None or not hmac.compare_digest(row.token_hash, token_hash):
        return None
    return row


async def prune_expired(session: AsyncSession) -> int:
    """Delete every session row that has already expired, returning how many.

    Nothing else removes them: an expired row is ignored by
    ``session_for_token`` but stays in the table forever, so the table grows
    with every login for the life of the deployment. Called from the login
    endpoint, which is the only rare, non-hot request that touches this
    table.
    """
    result = await session.execute(delete(Session).where(Session.expires_at <= func.now()))
    await session.commit()
    return result.rowcount or 0


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


class LoginRateLimiter:
    """A fixed window of login attempts per client IP, held in memory.

    Defence in depth for one pod, not a distributed limiter: an attacker who
    can reach the port would otherwise be able to spend the whole process's
    CPU on bcrypt (~250 ms a call at cost 12) simply by asking. Deliberately
    in-process -- a shared store would mean a new dependency and a new
    failure mode for something whose only job is to keep one process from
    being trivially exhausted.
    """

    def __init__(self, max_attempts: int = 10, window_seconds: float = 60.0) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, client: str) -> bool:
        """Record an attempt by ``client`` and say whether it may proceed."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Every client's stale entries are dropped, not just this one's: the
        # table is keyed by remote address and would otherwise grow without
        # bound under a distributed flood.
        for key, hits in list(self._hits.items()):
            fresh = [hit for hit in hits if hit > cutoff]
            if fresh:
                self._hits[key] = fresh
            else:
                del self._hits[key]
        hits = self._hits.setdefault(client, [])
        if len(hits) >= self.max_attempts:
            return False
        hits.append(now)
        return True
