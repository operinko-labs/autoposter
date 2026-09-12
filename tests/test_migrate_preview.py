# tests/test_migrate_preview.py
"""Spec §6.5: the pre-merge report of what alembic revision c1d2e3f4a5b6 will
merge, without changing anything. Two unit tests exercise the pure
``preview(rows)`` function; the third proves ``__main__`` end-to-end against a
scratch database (skips without one, the way tests/test_migration_identity.py
does -- a DIFFERENT scratch database name so the two never collide under
xdist)."""
import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from autoposter.migrate_preview import Collision, preview

BASE_URL = os.environ.get("AUTOPOSTER_TEST_DATABASE_URL", "")
SCRATCH = BASE_URL.rsplit("/", 1)[0] + "/autoposter_preview_scratch" if BASE_URL else ""


def test_preview_groups_rows_by_the_migration_rule_newest_first():
    rows = [
        dict(id=1, kind="movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/m-4k.mkv", root_folder=None, rating_key="1",
             title="A", updated_at=2),
        dict(id=7, kind="movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/x/m-4k.mkv", root_folder=None, rating_key="7",
             title="A dup", updated_at=1),
        dict(id=2, kind="movie", tmdb_id=603, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/m-1080p.mkv", root_folder=None, rating_key="2",
             title="B", updated_at=3),
    ]
    assert preview(rows) == [Collision(key="movie:tmdb:603::m-4k.mkv", survivor_id=1, merged_ids=[7])]


def test_preview_uses_root_folder_for_provider_less_movies():
    """Amended §4.2 path: two provider-less movies sharing a root_folder and
    basename collide; two more in different folders, same basename, do not."""
    rows = [
        dict(id=10, kind="movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/x/movie.mkv", root_folder="FolderA", rating_key="10",
             title="Same folder", updated_at=5),
        dict(id=11, kind="movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/y/movie.mkv", root_folder="FolderA", rating_key="11",
             title="Same folder dup", updated_at=4),
        dict(id=12, kind="movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/z/movie.mkv", root_folder="FolderB", rating_key="12",
             title="Different folder", updated_at=3),
        dict(id=13, kind="movie", tmdb_id=None, tvdb_id=None, imdb_id=None, season_number=None,
             episode_number=None, file_path="/w/movie.mkv", root_folder="FolderC", rating_key="13",
             title="Another folder", updated_at=2),
    ]
    assert preview(rows) == [
        Collision(key="movie:path:::FolderA/movie.mkv", survivor_id=10, merged_ids=[11]),
    ]


def test_main_requires_a_database_url():
    """No database needed: __main__ must check up front rather than raise a
    bare KeyError traceback."""
    env = {k: v for k, v in os.environ.items() if k != "AUTOPOSTER_DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, "-m", "autoposter.migrate_preview"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert ("AUTOPOSTER_DATABASE_URL is not set; point it at the database the migration will run against"
            in result.stderr)


def _alembic(*args: str) -> None:
    env = {**os.environ, "AUTOPOSTER_DATABASE_URL": SCRATCH}
    subprocess.run(["alembic", *args], check=True, env=env)


def _run_migrate_preview() -> str:
    env = {**os.environ, "AUTOPOSTER_DATABASE_URL": SCRATCH}
    result = subprocess.run(
        [sys.executable, "-m", "autoposter.migrate_preview"],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.skipif(not SCRATCH, reason="needs AUTOPOSTER_TEST_DATABASE_URL")
async def test_main_reports_the_collision_then_says_the_migration_has_run():
    admin = create_async_engine(BASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    async with admin.begin() as conn:
        await conn.execute(sa.text("DROP DATABASE IF EXISTS autoposter_preview_scratch"))
        await conn.execute(sa.text("CREATE DATABASE autoposter_preview_scratch"))
    await admin.dispose()

    _alembic("upgrade", "b7c4e1a92f30")
    engine = create_async_engine(SCRATCH)
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO media_items (rating_key, library, kind, tmdb_id, title, updated_at) VALUES "
            "('1', 'Movies', 'movie', 603, 'The Matrix', '2026-01-07 00:00:00+00'), "
            "('7', 'Movies', 'movie', 603, 'The Matrix dup', '2026-01-01 00:00:00+00')"
        ))
    await engine.dispose()

    output = _run_migrate_preview()
    assert 'survivor=1 "The Matrix"' in output
    assert "merges=2" in output

    _alembic("upgrade", "head")
    output = _run_migrate_preview()
    assert "media_items.rating_key is already gone; the identity migration has run" in output
