import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Perf spec D3. Every worker holds a session for the length of a job, and the
# scheduler, the dashboard stream and the request handlers need connections
# of their own beside them -- so the pool is the workers plus a fixed
# headroom, not SQLAlchemy's flat 5 + 10 that a full pass at five workers
# already crowded. At the default of 10 workers that is 20 + 10 overflow,
# well under PostgreSQL's default max_connections of 100.
POOL_HEADROOM = 10
POOL_MAX_OVERFLOW = 10
# Recycled before any idle-connection reaper between here and the database
# (a pgbouncer, a NAT table) can close it underneath the pool; pre-ping
# still catches whatever closes sooner.
POOL_RECYCLE_SECONDS = 1800
# SQLAlchemy's own default, kept for every engine built without a config: the
# setup wizard's probe below, boot's store reads and the one-shot CLIs.
DEFAULT_POOL_SIZE = 5


def pool_size_for(workers: int) -> int:
    """The pool size for a process running ``workers`` render workers."""
    return workers + POOL_HEADROOM


def make_engine(
    url: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = POOL_MAX_OVERFLOW,
    pool_recycle: int = POOL_RECYCLE_SECONDS,
) -> AsyncEngine:
    """The engine every caller builds. Keyword-only pool arguments with safe
    defaults, so a caller with no config -- ``database_answers`` first among
    them -- keeps calling ``make_engine(url)`` and gets today's pool."""
    return create_async_engine(
        url,
        future=True,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_recycle=pool_recycle,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


# Connect and statement together. The caller is the setup wizard's database
# step, with a human waiting on an HTTP response: a URL that is merely slow is
# as unusable as one that refuses, and answering "no" in five seconds is worth
# more than answering "yes" in sixty.
PROBE_TIMEOUT_SECONDS = 5.0


async def database_answers(url: str) -> tuple[bool, str]:
    """Whether the database at ``url`` accepts a connection and answers.

    ``make_engine`` is lazy, so a well-formed URL pointing at nothing
    constructs perfectly and fails on the first real query hours later. The
    setup wizard's database step needs to know now instead, because it must
    not persist a URL it has never used.

    NOT used by ``boot``: the boot mode is decided by the credentials and the
    config document alone, so that a configured deployment whose database is
    down keeps failing its migration and restarting, exactly as the shell line
    this replaced did, instead of demoting itself into an unauthenticated
    wizard on the production port.

    The whole probe is bounded by ``asyncio.wait_for`` rather than by a
    dialect-specific ``connect_args`` timeout. Only asyncpg has an implicit
    connect bound (60 s, incidental rather than chosen), no dialect bounds the
    QUERY, and a host that accepts the connection and then stops answering --
    a paused VM, a failing-over pgbouncer, a DROP rule applied after accept --
    is precisely the case that would otherwise hang forever. A timeout is
    reported as ``TimeoutError``, which is a class name like every other.

    Returns the failure's exception CLASS NAME and never its message: a
    connection error's own text carries the DSN -- host, user and password --
    and the caller puts this string in a response body.
    """
    engine = None
    try:
        engine = make_engine(url)

        async def probe() -> None:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        await asyncio.wait_for(probe(), timeout=PROBE_TIMEOUT_SECONDS)
        return True, ""
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        if engine is not None:
            await engine.dispose()
