import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, future=True, pool_pre_ping=True)


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
