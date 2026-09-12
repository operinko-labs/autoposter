# tests/test_migration_identity.py
"""Spec §4.7 / §10.4: up, down, up on a scratch database, seeded with every
identity shape and one collision. Skips without a database URL."""
import os
import subprocess

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

BASE_URL = os.environ.get("AUTOPOSTER_TEST_DATABASE_URL", "")
SCRATCH = BASE_URL.rsplit("/", 1)[0] + "/autoposter_migration_scratch" if BASE_URL else ""
pytestmark = pytest.mark.skipif(not SCRATCH, reason="needs AUTOPOSTER_TEST_DATABASE_URL")

SEED = [
    """
    INSERT INTO media_items (rating_key, library, kind, tmdb_id, tvdb_id, imdb_id, title, year,
                             season_number, episode_number, file_path, updated_at) VALUES
     ('1', 'Movies', 'movie', 603, NULL, 'tt0133093', 'The Matrix', 1999, NULL, NULL, '/m/The Matrix (1999)/m-4k.mkv', '2026-01-07 00:00:00+00'),
     ('2', 'Movies', 'movie', 603, NULL, 'tt0133093', 'The Matrix', 1999, NULL, NULL, '/m/The Matrix (1999)/m-1080p.mkv', '2026-01-02 00:00:00+00'),
     ('3', 'Movies', 'movie', NULL, NULL, NULL, 'Unmatched', 2001, NULL, NULL, '/m/Unmatched/u.mkv', '2026-01-03 00:00:00+00'),
     ('4', 'Movies', 'movie', NULL, NULL, NULL, 'Nothing', 2002, NULL, NULL, NULL, '2026-01-04 00:00:00+00'),
     ('5', 'TV', 'show', NULL, 71663, NULL, 'The Simpsons', 1989, NULL, NULL, '/tv/The Simpsons', '2026-01-05 00:00:00+00'),
     ('6', 'TV', 'episode', NULL, 71663, NULL, 'Ep', 1990, 2, 3, '/tv/The Simpsons/s02e03.mkv', '2026-01-06 00:00:00+00'),
     ('7', 'Movies', 'movie', 603, NULL, NULL, 'The Matrix dup', 1999, NULL, NULL, '/m/The Matrix (1999)/m-4k.mkv', '2026-01-01 00:00:00+00');
    """,
    """
    INSERT INTO renders (item_id, art_kind, source_mode, asset_path, upload_status, status) VALUES
     (1, 'poster', 'generate', '/a/1.jpg', 'uploaded', 'rendered'),
     (7, 'poster', 'generate', '/a/7.jpg', 'failed', 'rendered');
    """,
]


def _alembic(*args: str) -> None:
    env = {**os.environ, "AUTOPOSTER_DATABASE_URL": SCRATCH}
    subprocess.run(["alembic", *args], check=True, env=env)


async def _rows(engine, sql: str):
    async with engine.begin() as conn:
        return (await conn.execute(sa.text(sql))).all()


async def test_up_down_up_with_every_identity_shape():
    admin = create_async_engine(BASE_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    async with admin.begin() as conn:
        await conn.execute(sa.text("DROP DATABASE IF EXISTS autoposter_migration_scratch"))
        await conn.execute(sa.text("CREATE DATABASE autoposter_migration_scratch"))
    await admin.dispose()

    _alembic("upgrade", "b7c4e1a92f30")
    engine = create_async_engine(SCRATCH)
    async with engine.begin() as conn:
        for statement in SEED:
            await conn.execute(sa.text(statement))

    _alembic("upgrade", "head")
    keys = dict(await _rows(engine, "SELECT id, identity_key FROM media_items ORDER BY id"))
    refs = {(r[0], r[1], r[2]) for r in await _rows(engine, "SELECT item_id, server, native_id FROM media_item_server_refs")}
    deliveries = {(r[0], r[1], r[2]) for r in await _rows(engine, "SELECT render_id, server, status FROM render_deliveries")}

    assert keys[1] == "movie:tmdb:603::m-4k.mkv"
    assert keys[2] == "movie:tmdb:603::m-1080p.mkv"
    assert keys[3] == "movie:path:::u.mkv"
    assert keys[4] == "movie:legacy:plex:4"
    assert keys[5] == "show:tvdb:71663::"
    assert keys[6] == "episode:tvdb:71663:s2e3:s02e03.mkv"
    assert 7 not in keys, "the duplicate 4K row merged into the newest row"
    assert (1, "plex", "7") in refs, "the merged row's Plex id now points at the survivor"
    assert (1, "plex", "uploaded") in deliveries
    assert all(render_id != 7 for render_id, _, _ in deliveries)

    _alembic("downgrade", "b7c4e1a92f30")
    back = dict(await _rows(engine, "SELECT id, rating_key FROM media_items ORDER BY id"))
    assert back[1] == "1" and back[3] == "3"

    _alembic("upgrade", "head")
    # 6, not the original 7: the collision merge is permanent -- item 7 was
    # consolidated into item 1 on the first upgrade and stays gone, so the
    # round trip is stable at the six surviving items, each with one ref.
    assert len(await _rows(engine, "SELECT 1 FROM media_item_server_refs")) == 6
    await engine.dispose()
