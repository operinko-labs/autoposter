import os
import shutil
import subprocess
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

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def _running_in_ci() -> bool:
    return os.environ.get("CI", "").lower() in {"1", "true", "yes"}


def _imagemagick_problem(hdri: bool) -> str | None:
    """Why the ImageMagick-backed tests cannot run here, or None if they can."""
    if shutil.which("magick") is None:
        return (
            "there is no `magick` on PATH. Note that installing Ubuntu's "
            "`imagemagick` package does not fix this: that is ImageMagick 6, "
            "whose binary is `convert`"
        )
    if not GOLDEN.is_dir():
        return f"the harvested golden fixtures are missing from {GOLDEN}"
    if not hdri:
        return None
    version = subprocess.run(
        ["magick", "-version"], capture_output=True, text=True, check=False
    ).stdout
    if "HDRI" not in version:
        first = version.splitlines()[0] if version else "(no output)"
        return (
            "`magick` is not a Q16-HDRI build, and byte-exact parity with the "
            f"replaced tool only holds on one: {first}"
        )
    if not (GOLDEN / "expected_poster.jpg").is_file():
        return f"the harvested reference poster is missing from {GOLDEN}"
    return None


@pytest.fixture(autouse=True)
def imagemagick(request):
    """Gate for tests marked ``@pytest.mark.imagemagick`` (``("hdri")`` for a
    Q16-HDRI build), following tests/test_attribution_present.py.

    Skipping on a developer machine is deliberate: nobody should need
    ImageMagick installed to run ``pytest tests/``. Skipping *in CI* is not,
    and until this fixture existed that is exactly what happened everywhere:
    no runner has a ``magick``, so the byte-identical poster parity this
    project's central claim rests on was verified nowhere, which is how
    ``FakePlex`` went without a ``fetch_item`` until it was found by accident.
    A guard that reports green having run nothing is worse than no guard,
    because it is mistaken for one.

    So when ``CI`` is set these fail instead. The workflow runs them in a
    container carrying the image's own ImageMagick packages; if that ever
    stops provisioning one, CI goes red rather than quietly passing.
    """
    marker = request.node.get_closest_marker("imagemagick")
    if marker is None:
        return
    problem = _imagemagick_problem(hdri="hdri" in marker.args)
    if problem is None:
        return
    if _running_in_ci():
        pytest.fail(
            f"this test needs ImageMagick and {problem}, so the poster parity "
            "it exists to prove went unverified. CI must provide one -- see "
            'the "Test the ImageMagick-gated poster parity" step in '
            ".forgejo/workflows/ci.yml."
        )
    pytest.skip(f"{problem}. This is a hard failure in CI.")


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
