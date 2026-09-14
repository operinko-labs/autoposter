import io
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from autoposter.config.loader import load_config
from autoposter.db.base import Base
from autoposter.db.models import MediaItem, MediaItemServerRef

# The suites a pull request defers to the merge commit. The contract itself is
# written at the "Test" step in .forgejo/workflows/ci.yml; in short, a pull
# request runs everything except these, main and workflow_dispatch run
# everything, and nothing is published from a run that skipped any of it.
#
# Chosen by measurement rather than by taste. A serial run of the whole suite
# spends 459.7s inside tests; these 23 files are 299.7s of that -- about two
# thirds of the time for 525 of the 4,170 non-ImageMagick tests. They have one
# cause in common, which is also why nothing cheaper is on the list: each of
# these tests builds the ASGI application and drives it against a real
# database.
#
# tests/test_migrations.py is deliberately *not* here, though it was briefly.
# It is the cheapest thing that was ever on this list (21.5s, 3 tests) and the
# only one that is not an API suite, and what it guards is model/migration
# drift -- the incident in its own module docstring, an empty autogenerate that
# ran clean, got stamped as head and shipped unable to boot. The image job's
# "Verify migrations apply to an empty database" does run on pull requests, but
# it proves the migrations *apply*, not that they match the models, so
# deferring this file would let a PR add a model column with no migration and
# see nothing but green. 21.5s buys pull requests back their only drift guard.
#
# Written out rather than matched by glob, because which lane a suite belongs
# in is a decision and a glob would make it silently. The other half of that is
# FAST_API_SUITES below and the guard in tests/test_ci_path_filters.py that
# reads both: a new `test_api_*.py` file has to be named in one list or the
# other, on the pull request that adds it, or the suite goes red.
DEEP_SUITES = frozenset(
    {
        "test_api_action_center.py",
        "test_api_action_center_rebuild.py",
        "test_api_actions.py",
        "test_api_artwork.py",
        "test_api_artwork_modes.py",
        "test_api_auth.py",
        "test_api_candidates.py",
        "test_api_catch_up.py",
        "test_api_clear_override.py",
        "test_api_collections_builders.py",
        "test_api_config_drift.py",
        "test_api_config_editor.py",
        "test_api_dashboard.py",
        "test_api_dashboard_stream.py",
        "test_api_files.py",
        "test_api_files_upload.py",
        "test_api_filters.py",
        "test_api_full_pass.py",
        "test_api_gate.py",
        "test_api_item_overrides.py",
        "test_api_jobs.py",
        "test_api_key.py",
        "test_api_library.py",
        "test_api_login.py",
        "test_api_logs.py",
        "test_api_manual.py",
        "test_api_manual_upload.py",
        "test_api_mismatches.py",
        "test_api_pick.py",
        "test_api_playlists.py",
        "test_api_scheduled_runs.py",
        "test_api_secret_rotation.py",
        "test_api_setup.py",
        "test_api_setup_arr.py",
        "test_api_setup_check.py",
        "test_api_setup_jellyfin.py",
        "test_api_setup_plex.py",
        "test_api_stats_runs.py",
        "test_api_stats_storage.py",
        "test_api_testing.py",
        "test_api_version.py",
        "test_migrate_preview.py",
        "test_migration_identity.py",
    }
)

# The other answer to the same question: a `test_api_*.py` suite that is
# cheap enough to keep on both lanes. Empty today -- every one of the 23 builds
# the ASGI app against a real database, which is what the deep lane *is* -- and
# it exists so that the answer "this one stays fast" can be given explicitly
# rather than by omission. tests/test_ci_path_filters.py asserts every
# `test_api_*.py` file appears in one set or the other.
FAST_API_SUITES: frozenset[str] = frozenset()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Apply the ``deep`` marker to every test in ``DEEP_SUITES``.

    ``pytestmark = pytest.mark.deep`` in each of the 23 modules would say the
    same thing, but it would say it in 23 places while the lane is one decision;
    here the whole of it is readable at once and CI's ``-m`` expression has a
    single thing to point at.

    ``tryfirst`` because the marker has to exist before pytest's own
    ``-m`` deselection runs, and that is also a
    ``pytest_collection_modifyitems``. Relying on the registration order of
    conftest against a builtin plugin would work today and be silent the day it
    stopped: everything would simply run, on both lanes.
    """
    for item in items:
        if item.path.name in DEEP_SUITES:
            item.add_marker(pytest.mark.deep)


def _required_env(name: str) -> str:
    """An environment variable the suite cannot run without.

    No fallback: a hardcoded ``localhost:5433`` default here once happened to
    match one developer's own PostgreSQL, so a misconfigured environment
    connected anyway instead of saying so. The sanctioned way to run this
    suite is the container, which sets this.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Run the suite in the container instead: "
            "`docker compose run --rm test pytest` (see docker-compose.yml's "
            "`test` service, which sets it)."
        )
    return value


XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")


def _database_for_this_process(url: str) -> str:
    """The database this pytest process owns outright.

    Serially there is one process and it gets the URL untouched -- exactly what
    this module did before parallel runs were possible at all.

    Under pytest-xdist there are several, and they may not share: the schema
    below is dropped and rebuilt by the first database test in each *process*,
    so N workers against one database means N ``drop_all`` calls landing
    mid-suite underneath each other. That is not a hypothetical -- it is the
    StaleDataError this file used to carry a warning against, and the same one
    the workflow's ImageMagick step records from the time it shared this
    database with the main run. So each worker gets ``<base>_gw0``,
    ``<base>_gw1`` and so on, created on demand by
    ``_create_this_process_database`` below.
    """
    if not XDIST_WORKER:
        return url
    parsed = urlsplit(url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"{parsed.path}_{XDIST_WORKER}",
            parsed.query,
            parsed.fragment,
        )
    )


TEST_DB_URL = _database_for_this_process(_required_env("AUTOPOSTER_TEST_DATABASE_URL"))

# Written back so the variable and this module cannot name two different
# databases. tests/test_config_overrides.py reads it directly and hands it to
# the CLIs as AUTOPOSTER_DATABASE_URL, which has to be the database the
# ``session`` fixture just wrote the overrides row into. Outside xdist this
# stores exactly the string it read.
os.environ["AUTOPOSTER_TEST_DATABASE_URL"] = TEST_DB_URL


async def _create_this_process_database() -> None:
    """Create this worker's database if the server does not have it yet.

    A no-op serially, where the database is the one the environment named and
    something else already made it.

    ``CREATE DATABASE`` cannot run inside a transaction block, so it goes over
    a bare asyncpg connection rather than through a SQLAlchemy engine -- the
    same constraint .forgejo/workflows/ci.yml's ImageMagick step records where
    it uses two separate ``psql`` invocations for the same reason. Workers do
    not race each other over this: each one only ever creates the single
    database its own name spells.
    """
    if not XDIST_WORKER:
        return
    name = urlsplit(TEST_DB_URL).path.lstrip("/")
    maintenance = _required_env("AUTOPOSTER_MAINTENANCE_DATABASE_URL")
    connection = await asyncpg.connect(maintenance, timeout=10)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", name
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


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
def no_outbound_network(request, monkeypatch):
    """Fail loudly if a test reaches the real network.

    Nothing else in the suite does today, but that is avoidance rather than
    enforcement: the IMDb GraphQL API and the Kometa GitHub dataset are one
    forgotten ``MockTransport`` away, and IMDb's own response carries a
    non-commercial-use disclaimer. ``MockTransport`` and ``ASGITransport``
    are unaffected -- only the real connecting transport is blocked.

    Exempt: tests marked ``jellyfin`` (tests/test_jellyfin_live.py) reach the
    operator's own throwaway instance on purpose -- that is the entire point
    of ``jellyfin_live``, and it is gated by that fixture's own skip, not by
    this one.
    """
    if request.node.get_closest_marker("jellyfin") is not None:
        return

    async def blocked(self, request):
        raise RuntimeError(
            "tests must not make real network calls; %s %s was attempted"
            % (request.method, request.url)
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)


async def seed_media_item(
    session, native_id: str, *, server: str = "plex", kind: str = "movie",
    library: str = "Movies", title: str = "A", root_folder: str | None = None,
    season_number: int | None = None, episode_number: int | None = None,
    parent: MediaItem | None = None, plex_ref: bool = True, **extra,
) -> MediaItem:
    """Seed one ``media_items`` row, plus its ``media_item_server_refs`` row
    unless ``plex_ref`` is False.

    ``media_items.rating_key`` no longer exists: a row is identified
    by ``identity_key``, and its per-server native id lives in a separate
    ``MediaItemServerRef``. Shared here so every artwork-mode and
    metadata-backup suite that used to write ``MediaItem(rating_key=...)``
    seeds the same pair the same way, rather than repeating it per file.
    ``identity_key`` is synthesized from ``native_id`` rather than from any
    external id a test also passes in -- these suites care about identifying
    an item by its server id, exactly as ``rating_key`` did, not about the
    identity-key scheme itself (``test_scheduler_prune_job.py``'s ``_add_item``
    precedent).

    ``plex_ref=False`` seeds the row with no server ref at all -- the shape a
    row this service has never resolved against Plex takes (or one dropped
    from a re-key/prune), for the code paths that exist specifically to
    handle it: db/refs.native_ids answering nothing for it, so the caller
    counts it "missing" rather than probing Plex, and the API's 409 "this
    item has no Plex id" when a ResolvedItem would otherwise have to be
    rebuilt from nothing. ``native_id`` still names the row's identity_key
    in that case -- only the ref row is skipped.
    """
    item = MediaItem(
        identity_key=f"{kind}:legacy:{server}:{native_id}",
        library=library, kind=kind, title=title, root_folder=root_folder,
        season_number=season_number, episode_number=episode_number,
        parent_id=parent.id if parent is not None else None,
        **extra,
    )
    session.add(item)
    await session.flush()
    if plex_ref:
        session.add(MediaItemServerRef(
            item_id=item.id, server=server, native_id=native_id, library=library,
        ))
    await session.commit()
    return item


def decodable_png(size: tuple[int, int] = (8, 12)) -> bytes:
    """A small, genuinely decodable PNG for a fake artwork response.

    ``render/pipeline._download`` decodes every body it keeps, so a mock
    transport that answered with a placeholder byte string would send every
    render test down the refusal path instead of the behaviour it is about.
    Shared from here so the several suites that fake a provider CDN cannot
    drift about what "an image" is.
    """
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format="PNG")
    return buffer.getvalue()


# Per-process on purpose: each worker process runs its own first-test drop_all,
# which is only safe because ``_database_for_this_process`` above has given it a
# database nobody else is using. Sharing one database across workers is what
# produced the StaleDataErrors this flag used to carry a warning against.
_SCHEMA_READY = False


@pytest_asyncio.fixture
async def engine():
    """A per-test engine over a database that is empty when the test starts.

    The schema is dropped and rebuilt once per process, then TRUNCATEd between
    tests rather than rebuilt. Rebuilding all tables for every test cost
    0.6-0.8s of setup per DB test locally and several seconds each on the CI
    runner -- ~340 DB tests made that the whole of a 21-minute Test step. One
    TRUNCATE is a single round trip; RESTART IDENTITY makes it equivalent to a
    fresh schema for anything reading generated ids. The engine itself stays
    function-scoped because asyncpg connections are bound to the current
    test's event loop.
    """
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        await _create_this_process_database()
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        if not _SCHEMA_READY:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            _SCHEMA_READY = True
        else:
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
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
def jpeg_bytes():
    """A real 4x4 JPEG, so ``_is_image`` is exercised against real bytes rather
    than mocked."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


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


@pytest.fixture
def jellyfin_live():
    url, key = os.environ.get("AUTOPOSTER_TEST_JELLYFIN_URL"), os.environ.get("AUTOPOSTER_TEST_JELLYFIN_APIKEY")
    if not (url and key):
        pytest.skip("set AUTOPOSTER_TEST_JELLYFIN_URL and AUTOPOSTER_TEST_JELLYFIN_APIKEY")
    return url.rstrip("/"), key
