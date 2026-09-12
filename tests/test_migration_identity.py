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
                             season_number, episode_number, file_path, root_folder, updated_at) VALUES
     ('1', 'Movies', 'movie', 603, NULL, 'tt0133093', 'The Matrix', 1999, NULL, NULL, '/m/The Matrix (1999)/m-4k.mkv', NULL, '2026-01-07 00:00:00+00'),
     ('2', 'Movies', 'movie', 603, NULL, 'tt0133093', 'The Matrix', 1999, NULL, NULL, '/m/The Matrix (1999)/m-1080p.mkv', NULL, '2026-01-02 00:00:00+00'),
     ('3', 'Movies', 'movie', NULL, NULL, NULL, 'Unmatched', 2001, NULL, NULL, '/m/Unmatched/u.mkv', 'Unmatched', '2026-01-03 00:00:00+00'),
     ('4', 'Movies', 'movie', NULL, NULL, NULL, 'Nothing', 2002, NULL, NULL, NULL, NULL, '2026-01-04 00:00:00+00'),
     ('5', 'TV', 'show', NULL, 71663, NULL, 'The Simpsons', 1989, NULL, NULL, '/tv/The Simpsons', NULL, '2026-01-05 00:00:00+00'),
     ('6', 'TV', 'episode', NULL, 71663, NULL, 'Ep', 1990, 2, 3, '/tv/The Simpsons/s02e03.mkv', NULL, '2026-01-06 00:00:00+00'),
     ('7', 'Movies', 'movie', 603, NULL, NULL, 'The Matrix dup', 1999, NULL, NULL, '/m/The Matrix (1999)/m-4k.mkv', NULL, '2026-01-01 00:00:00+00'),
     -- A second collision pair sharing an IDENTICAL updated_at (unlike 1/7
     -- above), so the merge's primary sort key can't break the tie and the
     -- `, id DESC` secondary sort (spec §6.5) has to: the higher id, 9, must
     -- survive over 8.
     ('8', 'Movies', 'movie', 42, NULL, NULL, 'Extra', 2020, NULL, NULL, '/m/Extra (2020)/e.mkv', NULL, '2026-02-01 00:00:00+00'),
     ('9', 'Movies', 'movie', 42, NULL, NULL, 'Extra dup', 2020, NULL, NULL, '/m/Extra (2020)/e.mkv', NULL, '2026-02-01 00:00:00+00'),
     -- A provider-less show (task 8d, spec §4.2 amendment): no guids at all,
     -- so it keys on its own folder rather than raising -- the amendment
     -- this migration now carries through via `root_folder`.
     ('10', 'TV', 'show', NULL, NULL, NULL, 'Orphan Show', 2001, NULL, NULL, '/tv/Orphan Show (2001)', 'Orphan Show (2001)', '2026-01-08 00:00:00+00');
    """,
    # An adopted-style show/season/episode chain (C1). Adoption before
    # 2026-09-13 stored each EPISODE's OWN tvdb id, while the Plex resolver
    # has always keyed an episode on its SHOW's (its match container is the
    # show). The migration has to reach the show through parent_id, or this
    # row lands under a key no later pass can ever compute and the next full
    # pass mints a second row for the same episode. Three statements,
    # because each parent_id needs the id the statement before it minted.
    """
    INSERT INTO media_items (rating_key, library, kind, tmdb_id, tvdb_id, imdb_id, title, year,
                             season_number, episode_number, file_path, root_folder, updated_at) VALUES
     ('11', 'TV', 'show', NULL, 121361, NULL, 'Game of Thrones', 2011, NULL, NULL, '/tv/Game of Thrones (2011)', 'Game of Thrones (2011)', '2026-03-01 00:00:00+00');
    """,
    """
    INSERT INTO media_items (rating_key, library, kind, tmdb_id, tvdb_id, imdb_id, title, year,
                             season_number, episode_number, file_path, root_folder, parent_id, updated_at) VALUES
     ('12', 'TV', 'season', NULL, 121361, NULL, 'Season 1', 2011, 1, NULL, NULL, 'Game of Thrones (2011)',
      (SELECT id FROM media_items WHERE rating_key = '11'), '2026-03-02 00:00:00+00');
    """,
    """
    INSERT INTO media_items (rating_key, library, kind, tmdb_id, tvdb_id, imdb_id, title, year,
                             season_number, episode_number, file_path, root_folder, parent_id, updated_at) VALUES
     ('13', 'TV', 'episode', NULL, 3254641, NULL, 'Winter Is Coming', 2011, 1, 1, NULL, 'Game of Thrones (2011)',
      (SELECT id FROM media_items WHERE rating_key = '12'), '2026-03-03 00:00:00+00');
    """,
    """
    INSERT INTO renders (item_id, art_kind, source_mode, asset_path, upload_status, status) VALUES
     (1, 'poster', 'generate', '/a/1.jpg', 'uploaded', 'rendered'),
     (7, 'poster', 'generate', '/a/7.jpg', 'failed', 'rendered');
    """,
    # One override the survivor (1) already has (field_x: the stale row's own
    # field_x must be DROPPED, not overwrite it) and one it does not
    # (field_y: the stale row's must be RE-POINTED, not lost) -- proving the
    # per-table unique-key dedupe rather than the old item_id-only one, which
    # would have dropped field_y too.
    """
    INSERT INTO item_metadata_overrides (item_id, field, value) VALUES
     (1, 'field_x', 'survivor-x'),
     (7, 'field_x', 'stale-x'),
     (7, 'field_y', 'stale-y');
    """,
    # A dismissal on the stale twin only (the survivor has none for this
    # art_kind), so its only correct outcome is a plain re-point.
    """
    INSERT INTO action_dismissals (item_id, art_kind, evidence) VALUES
     (7, 'poster', 'deadbeef');
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
    overrides = {(r[0], r[1], r[2]) for r in await _rows(engine, "SELECT item_id, field, value FROM item_metadata_overrides")}
    dismissals = {(r[0], r[1]) for r in await _rows(engine, "SELECT item_id, art_kind FROM action_dismissals")}

    assert keys[1] == "movie:tmdb:603::m-4k.mkv"
    assert keys[2] == "movie:tmdb:603::m-1080p.mkv"
    assert keys[3] == "movie:path:::Unmatched/u.mkv"
    assert keys[4] == "movie:legacy:plex:4"
    assert keys[5] == "show:tvdb:71663::"
    # No file field: an episode never carries its own file_path (the Plex
    # resolver's match container for a season/episode intent is the show).
    # Its parent chain is broken (no parent_id at all), so C1's show lookup
    # falls back to the row's own ids -- there is nothing else to key on.
    assert keys[6] == "episode:tvdb:71663:s2e3:"
    assert keys[10] == "show:path:::Orphan Show (2001)"
    # C1: the season and the episode key on the SHOW's tvdb id, reached
    # through parent_id -- the episode's own 3254641 is never in the key,
    # because the Plex resolver would never produce it.
    assert keys[11] == "show:tvdb:121361::"
    assert keys[12] == "season:tvdb:121361:s1:"
    assert keys[13] == "episode:tvdb:121361:s1e1:"
    assert 7 not in keys, "the duplicate 4K row merged into the newest row"
    # ONE ref per (item, server), spec §4.1: the survivor already has a Plex
    # ref of its own, so the merged row's is DROPPED rather than re-pointed.
    # Two live Plex ids on one item is the state every reader would then
    # have to choose between.
    assert (1, "plex", "1") in refs, "the survivor keeps its own Plex id"
    assert (1, "plex", "7") not in refs, "the merged row's Plex id was dropped, not re-pointed"
    assert deliveries == {(1, "plex", "uploaded")}, "the stale row's render was a duplicate art_kind and was dropped, not delivered"

    assert keys[9] == "movie:tmdb:42::e.mkv"
    assert 8 not in keys, "identical-timestamp tie breaks toward the higher id (id DESC)"
    assert (9, "plex", "9") in refs, "the higher-id survivor keeps its own Plex id"
    assert (9, "plex", "8") not in refs, "the tied stale row's Plex id was dropped too"
    assert len(refs) == 11, "eleven surviving rows, one Plex ref each"

    # The per-table unique-key dedupe (not item_id alone): the survivor's own
    # field_x is untouched, the stale row's clashing field_x is dropped, and
    # the stale row's field_y -- which the survivor never had -- is re-pointed
    # rather than lost.
    assert overrides == {(1, "field_x", "survivor-x"), (1, "field_y", "stale-y")}
    # The stale row's dismissal has no collision on the survivor and simply
    # follows the merge rather than being cascade-deleted with the stale row.
    assert dismissals == {(1, "poster")}

    _alembic("downgrade", "b7c4e1a92f30")
    back = dict(await _rows(engine, "SELECT id, rating_key FROM media_items ORDER BY id"))
    assert back[1] == "1" and back[3] == "3"

    _alembic("upgrade", "head")
    # 11, not the original 13: both collisions (7 into 1, 8 into 9) are
    # permanent, so the round trip is stable at the eleven surviving items,
    # each with one ref.
    assert len(await _rows(engine, "SELECT 1 FROM media_item_server_refs")) == 11
    await engine.dispose()
