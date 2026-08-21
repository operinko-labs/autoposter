import os
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from autoposter.config.loader import load_config
from autoposter.db.base import Base

TEST_DB_URL = os.environ.get(
    "AUTOPOSTER_TEST_DATABASE_URL",
    "postgresql+asyncpg://autoposter:autoposter@localhost:5433/autoposter",
)

EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s


@pytest.fixture
def config_with_badges():
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.badges.enabled = True
    cfg.badges.upload_to_plex = True
    return cfg


@pytest.fixture
def config_badges_dry_run():
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.badges.enabled = True
    cfg.badges.upload_to_plex = False
    return cfg


@pytest.fixture
def config_badges_disabled():
    cfg = load_config(EXAMPLE_CONFIG)
    cfg.badges.enabled = False
    cfg.badges.upload_to_plex = False
    return cfg


def session_factory_for(session):
    """Adapt the function-scoped test session to ProviderCache's factory API."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory():
        yield session

    return factory
