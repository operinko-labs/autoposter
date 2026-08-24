"""RestoreMode: push the backup tree back to Plex, filtered.

Fake Plex throughout: a ``FakeItem`` with ``uploadPoster``/``uploadArt``/
``lockPoster``/``lockArt`` spies that read back the temp file ``upload_artwork``
writes, and a tmp ``plex_backup_root`` seeded with backup files. The mode reads
disk and pushes to Plex; nothing here touches the network.
"""
from pathlib import Path

import pytest

from autoposter.artwork_modes.restore import RestoreMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token"


class FakeItem:
    """The plexapi surface restore writes. ``uploadPoster``/``uploadArt`` read
    the temp file ``upload_artwork`` hands them, so a test can assert both that
    the item was pushed and that the pushed bytes are the backup file's."""

    def __init__(self):
        self.uploaded = []  # (field, bytes)
        self.locked = []
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
        with open(filepath, "rb") as handle:
            self.uploaded.append(("poster", handle.read()))

    def uploadArt(self, filepath=None):  # noqa: N802 - plexapi name
        with open(filepath, "rb") as handle:
            self.uploaded.append(("art", handle.read()))

    def lockPoster(self):  # noqa: N802 - plexapi name
        self.locked.append("poster")

    def lockArt(self):  # noqa: N802 - plexapi name
        self.locked.append("art")


class FakePlexClient:
    def __init__(self, items=None):
        self._items = items or {}
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return self._items[rating_key]


@pytest.fixture
def backup_root(tmp_path) -> Path:
    return tmp_path / "plexbackup"


@pytest.fixture
def config(backup_root):
    cfg = load_config(EXAMPLE)
    cfg.plex.url = PLEX_URL
    cfg.artwork_modes.plex_backup_root = backup_root
    return cfg


def _headers():
    return {"X-Plex-Token": PLEX_TOKEN}


def _seed_backup(backup_root, library, root_folder, name, data):
    """Write one file into the (nested) Kometa backup tree."""
    path = backup_root / library / root_folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    root_folder="A (1999)", season=None, episode=None):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder=root_folder, season_number=season, episode_number=episode,
    )
    session.add(item)
    await session.commit()
    return item


async def test_dry_run_pushes_nothing(session, config, backup_root):
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"poster")
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    mode = RestoreMode(config, plex, None, _headers(), apply=False)
    result = await mode.run(session)

    assert result.dry_run is True
    assert (result.items, result.items_with_backup, result.files) == (1, 1, 1)
    # Dry run: nothing resolved against Plex, nothing pushed.
    assert plex.fetched == []
    assert item.uploaded == []
    assert result.as_response() == {
        "mode": "restore", "status": "dry run", "dry_run": True, "items": 1,
        "items_with_backup": 1, "files": 1, "skipped": 0,
    }


async def test_apply_pushes_the_backup_bytes(session, config, backup_root):
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"the-real-poster")
    _seed_backup(backup_root, "Movies", "A (1999)", "background.jpg", b"the-real-bg")
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RestoreMode(config, plex, None, _headers(), apply=True).run(session)

    assert result.dry_run is False
    assert (result.pushed, result.failed, result.files) == (2, 0, 2)
    assert ("poster", b"the-real-poster") in item.uploaded
    assert ("art", b"the-real-bg") in item.uploaded
    # background routes to uploadArt+lockArt, the rest to uploadPoster+lockPoster.
    assert set(item.locked) == {"poster", "art"}
    assert result.as_response()["status"] == "restored"


async def test_apply_pushes_exactly_the_filtered_set(session, config, backup_root):
    """The filter mutation-proof: a Movies-only restore must push the movie and
    NOT the show. Drop the library filter in RestoreMode.run and the show gets
    pushed too, reddening the ``tv.uploaded == []`` assertion."""
    await _add_item(session, rating_key="rk-m", kind="movie", library="Movies",
                    root_folder="A (1999)")
    await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows",
                    root_folder="The Show")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"movie-poster")
    _seed_backup(backup_root, "TV Shows", "The Show", "poster.jpg", b"show-poster")
    movie, tv = FakeItem(), FakeItem()
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    result = await RestoreMode(
        config, plex, None, _headers(), apply=True, library="Movies"
    ).run(session)

    assert result.items == 1  # only the Movies item is a candidate
    assert ("poster", b"movie-poster") in movie.uploaded
    assert tv.uploaded == []
    assert plex.fetched == ["rk-m"]


async def test_apply_filters_by_type_and_item(session, config, backup_root):
    await _add_item(session, rating_key="rk-m", kind="movie", library="Movies",
                    root_folder="A (1999)")
    show = await _add_item(session, rating_key="rk-t", kind="show", library="TV Shows",
                           root_folder="The Show")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"movie-poster")
    _seed_backup(backup_root, "TV Shows", "The Show", "poster.jpg", b"show-poster")
    movie, tv = FakeItem(), FakeItem()
    plex = FakePlexClient({"rk-m": movie, "rk-t": tv})

    # item_id narrows to the show; type=show agrees with it.
    result = await RestoreMode(
        config, plex, None, _headers(), apply=True, kind="show", item_id=show.id
    ).run(session)

    assert result.items == 1
    assert ("poster", b"show-poster") in tv.uploaded
    assert movie.uploaded == []


async def test_restore_respects_the_cap(session, config, backup_root):
    """Two items would change but the absolute cap is 1, so it refuses with the
    real numbers and pushes nothing -- proven by fetch_item never being called
    even though apply is true."""
    config.artwork_modes.max_changes = 1
    await _add_item(session, rating_key="rk1", root_folder="A (1999)")
    await _add_item(session, rating_key="rk2", root_folder="B (2000)")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"a")
    _seed_backup(backup_root, "Movies", "B (2000)", "poster.jpg", b"b")
    plex = FakePlexClient({"rk1": FakeItem(), "rk2": FakeItem()})

    result = await RestoreMode(config, plex, None, _headers(), apply=True).run(session)

    assert result.refused is not None
    assert "2 of 2" in result.refused
    assert "1" in result.refused
    assert plex.fetched == []
    response = result.as_response()
    assert response["status"] == "refused"
    assert response["dry_run"] is False  # apply=True was requested
    # The refusal is computed from real numbers -- the UI should not have to
    # parse them back out of the reason string.
    assert (response["items"], response["items_with_backup"], response["files"]) == (2, 2, 2)


async def test_restore_never_refreshes_the_plex_object(session, config, backup_root):
    """The behavioural companion to the project-wide no-.refresh() AST guard: a
    restore pushes art and must never trigger a metadata refresh, which would
    have Plex re-pull from its agents and overwrite what was just restored."""
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"poster")
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    await RestoreMode(config, plex, None, _headers(), apply=True).run(session)

    assert item.refreshed is False


async def test_restore_skips_an_item_with_no_backup_file(session, config, backup_root):
    await _add_item(session, rating_key="rk1")
    # No file seeded on disk.
    item = FakeItem()
    plex = FakePlexClient({"rk1": item})

    result = await RestoreMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_backup, result.files) == (1, 0, 0)
    assert item.uploaded == []


async def test_restore_skips_an_unnumbered_title_card_rather_than_raising(
    session, config, backup_root
):
    """The planning phase's half of the same guard the backup walk carries:
    ``asset_path`` cannot name a file for an episode Plex reports no ``index``
    for, so the row is skipped and counted. Without it the ValueError escapes
    while planning and the trigger 500s before it pushes anything at all."""
    await _add_item(session, rating_key="rk-ok", kind="episode", library="TV Shows",
                    root_folder="The Show", season=1, episode=2)
    await _add_item(session, rating_key="rk-bad", kind="episode", library="TV Shows",
                    root_folder="The Show", season=2021, episode=None)
    _seed_backup(backup_root, "TV Shows", "The Show", "S01E02.jpg", b"the-card")
    ok, bad = FakeItem(), FakeItem()
    plex = FakePlexClient({"rk-ok": ok, "rk-bad": bad})

    result = await RestoreMode(config, plex, None, _headers(), apply=True).run(session)

    assert (result.items, result.items_with_backup, result.files) == (2, 1, 1)
    assert result.skipped == 1
    assert ("poster", b"the-card") in ok.uploaded
    assert bad.uploaded == []


async def test_restore_refuses_an_empty_table(session, config):
    plex = FakePlexClient({})
    result = await RestoreMode(config, plex, None, _headers(), apply=True).run(session)
    assert result.refused is not None
    assert "media_items" in result.refused
    # apply=True was requested, so dry_run mirrors the success paths: False.
    assert result.as_response() == {
        "mode": "restore", "status": "refused", "reason": result.refused,
        "dry_run": False, "items": 0, "items_with_backup": 0, "files": 0,
        "skipped": 0,
    }
