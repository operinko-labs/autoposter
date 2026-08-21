"""Guards against a migration that drifts from the models (see the item_facts
incident: an empty autogenerate ran clean because the target DB already had
the table, got stamped as head, and shipped with no DDL).

Runs against its own scratch database so it never races the shared test DB
that ``conftest.py`` drops and recreates on every test.
"""

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from urllib.parse import urlsplit, urlunsplit

REPO_ROOT = Path(__file__).parent.parent

MAINTENANCE_DB_URL = os.environ.get(
    "AUTOPOSTER_MAINTENANCE_DATABASE_URL",
    "postgresql://autoposter:autoposter@localhost:5433/postgres",
)
SCRATCH_DB_NAME = "autoposter_migrations_check"


def _scratch_url(database: str) -> str:
    """A scratch-database URL on the same server as the maintenance URL.

    Derived rather than hardcoded: hardcoding the host and port meant the
    scratch database was created on whichever server the maintenance URL names
    while alembic was pointed at localhost:5433, which is only the same server
    on a developer's machine running docker-compose. In CI it is not, and the
    two migration tests failed with nothing to say about migrations.
    """
    parsed = urlsplit(MAINTENANCE_DB_URL)
    return urlunsplit(("postgresql+asyncpg", parsed.netloc, "/" + database, "", ""))


SCRATCH_DB_URL = _scratch_url(SCRATCH_DB_NAME)


def _unreachable_postgres() -> None:
    """Skip on a machine with no database; fail in CI, which has one.

    Same reasoning as tests/conftest.py's `imagemagick` fixture and
    tests/test_attribution_present.py: these two are the only check that a
    migration matches the models, and the whole point of them is the
    item_facts incident above. A green skip because
    AUTOPOSTER_MAINTENANCE_DATABASE_URL stopped being exported -- it comes
    from .forgejo/scripts/wait_for_postgres.py, not from the workflow -- would
    look exactly like a pass.
    """
    message = f"postgres is not reachable at {MAINTENANCE_DB_URL}"
    if os.environ.get("CI", "").lower() in {"1", "true", "yes"}:
        pytest.fail(
            f"{message}, so nothing verified that the migrations match the "
            "models. CI starts PostgreSQL and exports its URLs; that must "
            "have broken."
        )
    pytest.skip(f"{message}. This is a hard failure in CI.")


async def _postgres_reachable() -> bool:
    try:
        conn = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    except (OSError, asyncpg.PostgresError):
        return False
    await conn.close()
    return True


async def test_alembic_head_matches_models():
    """``alembic upgrade head`` on a fresh DB must leave no drift from the models."""
    if not await _postgres_reachable():
        _unreachable_postgres()

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}"')
    finally:
        await maint.close()

    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=SCRATCH_DB_URL)
    try:
        upgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

        check = subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stdout + check.stderr
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}"')
        finally:
            await maint.close()


async def test_migrations_apply_to_a_populated_renders_table():
    """A deployed instance already has rows in ``renders``.

    ``ADD COLUMN ... NOT NULL`` without a ``server_default`` fails outright
    against a non-empty table, so a migration that passes on a fresh database
    can still break every existing deployment. This walks to the revision
    before the badge columns, puts a row in, and then upgrades.
    """
    if not await _postgres_reachable():
        _unreachable_postgres()

    before_badges = "716a0d6b8941"

    maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
    try:
        await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_data"')
        await maint.execute(f'CREATE DATABASE "{SCRATCH_DB_NAME}_data"')
    finally:
        await maint.close()

    url = SCRATCH_DB_URL.replace(SCRATCH_DB_NAME, SCRATCH_DB_NAME + "_data")
    env = dict(os.environ, AUTOPOSTER_DATABASE_URL=url)

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        )

    try:
        step = alembic("upgrade", before_badges)
        assert step.returncode == 0, step.stdout + step.stderr

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            item_id = await conn.fetchval(
                "INSERT INTO media_items (kind, rating_key, title, library) "
                "VALUES ('movie', '999', 'Populated', 'Movies') RETURNING id"
            )
            await conn.execute(
                "INSERT INTO renders (item_id, art_kind, asset_path, source_mode, status) "
                "VALUES ($1, 'poster', '/x.jpg', 'generate', 'rendered')",
                item_id,
            )
        finally:
            await conn.close()

        head = alembic("upgrade", "head")
        assert head.returncode == 0, (
            "migrating a populated renders table failed:\n" + head.stdout + head.stderr
        )

        conn = await asyncpg.connect(url.replace("postgresql+asyncpg", "postgresql"), timeout=5)
        try:
            status = await conn.fetchval("SELECT upload_status FROM renders LIMIT 1")
        finally:
            await conn.close()
        assert status == "pending", "existing rows must be backfilled, got %r" % status
    finally:
        maint = await asyncpg.connect(MAINTENANCE_DB_URL, timeout=3)
        try:
            await maint.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB_NAME}_data"')
        finally:
            await maint.close()
