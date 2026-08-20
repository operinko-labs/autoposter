"""TTL cache for provider HTTP responses, backed by the ``provider_cache`` table."""
import hashlib
import json
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from autoposter.db.models import ProviderCache as ProviderCacheRow

# Query parameters that carry credentials and must never be persisted in a cache
# key: Fanart passes its key as `api_key`. TMDB and TVDB send their credentials as
# headers instead, which the key never includes at all (see build_cache_key).
_CREDENTIAL_PARAMS = {"api_key", "apikey", "token", "access_token"}


def build_cache_key(method: str, url: str, params: Mapping[str, object] | None = None) -> str:
    """A stable, credential-free key for one HTTP request.

    Parameters are sorted (not dict-ordered) and the result is hashed rather than
    passed through ``hash()`` (which is randomised per process), so the same
    request produces the same key across processes and restarts. Credential
    parameters are stripped before hashing so the cache table never becomes a
    place an API key is stored in plaintext, and so rotating a key does not
    silently invalidate every entry.
    """
    safe_params = sorted(
        (str(k), str(v)) for k, v in (params or {}).items()
        if k.lower() not in _CREDENTIAL_PARAMS
    )
    raw = json.dumps([method.upper(), url, safe_params], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProviderCache:
    """Reads and writes the ``provider_cache`` table.

    A fresh session is opened per call rather than held across the process
    lifetime: the cache is built once at startup (see app.py) and shared by every
    provider client, unlike the per-job session a queue worker owns.
    """

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def get(self, key: str) -> dict | None:
        """The cached value, or None if absent or expired.

        Expiry is compared against the database clock (``func.now()``) in the
        query itself, not this process's clock.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProviderCacheRow.value).where(
                    ProviderCacheRow.key == key,
                    ProviderCacheRow.expires_at > func.now(),
                )
            )
            return result.scalar_one_or_none()

    async def set(self, key: str, value: dict, ttl_seconds: int) -> None:
        """Upsert. Two workers racing to fill the same miss must not raise."""
        async with self._session_factory() as session:
            stmt = insert(ProviderCacheRow).values(
                key=key,
                value=value,
                expires_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, ttl_seconds),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ProviderCacheRow.key],
                set_={"value": stmt.excluded.value, "expires_at": stmt.excluded.expires_at},
            )
            await session.execute(stmt)
            await session.commit()
