"""The store's own columns, proven up/down/up on a scratch database.

The test image has no `psql`, so the scratch database is created and dropped
over asyncpg through AUTOPOSTER_MAINTENANCE_DATABASE_URL, which
docker-compose.yml's `test` service already exports.
"""
import os
import subprocess
import uuid

import asyncpg
import pytest


async def _scratch() -> tuple[str, str]:
    maintenance = os.environ["AUTOPOSTER_MAINTENANCE_DATABASE_URL"]
    name = f"store_{uuid.uuid4().hex[:12]}"
    connection = await asyncpg.connect(maintenance)
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()
    base = maintenance.rsplit("/", 1)[0]
    return name, f"{base}/{name}"


async def _drop(name: str) -> None:
    connection = await asyncpg.connect(os.environ["AUTOPOSTER_MAINTENANCE_DATABASE_URL"])
    try:
        await connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await connection.close()


def _alembic(command: list[str], url: str) -> None:
    environment = {**os.environ, "AUTOPOSTER_DATABASE_URL": url.replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )}
    result = subprocess.run(["alembic", *command], env=environment, check=False)
    assert result.returncode == 0, f"alembic {' '.join(command)} failed"


async def _columns(url: str, table: str) -> dict[str, str]:
    connection = await asyncpg.connect(url)
    try:
        rows = await connection.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = $1",
            table,
        )
    finally:
        await connection.close()
    return {row["column_name"]: row["data_type"] for row in rows}


@pytest.mark.asyncio
async def test_the_store_migration_goes_up_down_and_up_again():
    name, url = await _scratch()
    try:
        _alembic(["upgrade", "head"], url)
        assert "meta" in await _columns(url, "config_overrides")
        assert "format" in await _columns(url, "config_override_snapshots")
        assert "ciphertext" in await _columns(url, "secrets")

        _alembic(["downgrade", "b3d91f7c05ea"], url)
        assert "meta" not in await _columns(url, "config_overrides")
        assert "format" not in await _columns(url, "config_override_snapshots")
        assert await _columns(url, "secrets") == {}

        _alembic(["upgrade", "head"], url)
        assert "meta" in await _columns(url, "config_overrides")
        assert "ciphertext" in await _columns(url, "secrets")
    finally:
        await _drop(name)


@pytest.mark.asyncio
async def test_an_existing_row_is_backfilled_as_a_delta():
    """A store written before this lands is format 1 and must SAY so, because
    Task 3 turns exactly those rows into documents."""
    name, url = await _scratch()
    try:
        _alembic(["upgrade", "b3d91f7c05ea"], url)
        connection = await asyncpg.connect(url)
        try:
            await connection.execute(
                "INSERT INTO config_overrides (id, document) VALUES (1, $1::jsonb)",
                '{"workers": 3}',
            )
            snapshot_id = await connection.fetchval(
                "INSERT INTO config_override_snapshots (document, path_count, reason) "
                "VALUES ($1::jsonb, $2, $3) RETURNING id",
                '{"workers": 2}',
                1,
                "save",
            )
        finally:
            await connection.close()
        _alembic(["upgrade", "head"], url)
        connection = await asyncpg.connect(url)
        try:
            meta = await connection.fetchval("SELECT meta FROM config_overrides WHERE id = 1")
            format_ = await connection.fetchval(
                "SELECT format FROM config_override_snapshots WHERE id = $1", snapshot_id
            )
        finally:
            await connection.close()
        assert meta == "{}", "an existing row must carry the empty meta, i.e. format 1"
        assert format_ == 1, "an existing snapshot must carry format 1, i.e. a delta"
    finally:
        await _drop(name)
