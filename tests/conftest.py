import os
from pathlib import Path

import httpx
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


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    """Fail loudly if a test reaches the real network.

    Nothing in the suite does today, but that is avoidance rather than
    enforcement: the IMDb GraphQL API and the Kometa GitHub dataset are one
    forgotten ``MockTransport`` away, and IMDb's own response carries a
    non-commercial-use disclaimer. ``MockTransport`` and ``ASGITransport``
    are unaffected -- only the real connecting transport is blocked.
    """

    async def blocked(self, request):
        raise RuntimeError(
            "tests must not make real network calls; %s %s was attempted"
            % (request.method, request.url)
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


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


@pytest.fixture
def config_factory():
    """A config with the given attribute overrides applied to the example config."""

    def make(**overrides):
        cfg = load_config(EXAMPLE_CONFIG)
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    return make


def session_factory_for(session):
    """Adapt the function-scoped test session to ProviderCache's factory API."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory():
        yield session

    return factory
