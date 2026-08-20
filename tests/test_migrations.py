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

REPO_ROOT = Path(__file__).parent.parent

MAINTENANCE_DB_URL = os.environ.get(
    "AUTOPOSTER_MAINTENANCE_DATABASE_URL",
    "postgresql://autoposter:autoposter@localhost:5433/postgres",
)
SCRATCH_DB_NAME = "autoposter_migrations_check"
SCRATCH_DB_URL = (
    f"postgresql+asyncpg://autoposter:autoposter@localhost:5433/{SCRATCH_DB_NAME}"
)


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
        pytest.skip("postgres is not reachable")

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
