from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, future=True, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def database_answers(url: str) -> tuple[bool, str]:
    """Whether the database at ``url`` accepts a connection and answers.

    ``make_engine`` is lazy, so a well-formed URL pointing at nothing
    constructs perfectly and fails on the first real query hours later. Two
    callers need to know now instead: ``boot``, which will not run a migration
    against a database that is not there, and the setup wizard's database step,
    which must not persist a URL it has never used.

    Returns the failure's exception CLASS NAME and never its message: a
    connection error's own text carries the DSN -- host, user and password --
    and both callers put this string somewhere it can be read (the pod log at
    boot, a response body in the wizard).
    """
    engine = None
    try:
        engine = make_engine(url)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        if engine is not None:
            await engine.dispose()
