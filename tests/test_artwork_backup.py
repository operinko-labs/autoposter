"""BackupMode: copy Plex's live artwork into the backup tree.

Fake Plex throughout -- a ``FakeItem`` carrying the ``.thumb``/``.art`` fields
``fetch_artwork`` reads, an ``httpx.MockTransport`` answering the artwork URL,
and a tmp ``plex_backup_root``. conftest's ``no_outbound_network`` fails the
test if a real request ever escapes.
"""
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import AsyncClient

from autoposter.artwork_modes.backup import BackupMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token"
POSTER_BYTES = b"\xff\xd8 these bytes are the poster"
BACKGROUND_BYTES = b"RIFF these bytes are the background"


class FakeItem:
    """The plexapi surface backup reads. ``uploadPoster``/``uploadArt`` exist
    only to prove backup never calls them -- it is Plex-read-only."""

    def __init__(self, thumb=None, art=None):
        self.thumb = thumb
        self.art = art

    def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
        raise AssertionError("backup must not upload to Plex")

    def uploadArt(self, filepath=None):  # noqa: N802 - plexapi name
        raise AssertionError("backup must not upload to Plex")


class FakePlexClient:
    def __init__(self, items=None, error=None):
        self._items = items or {}
        self._error = error
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        if self._error is not None:
            raise self._error
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


def _serves():
    """A MockTransport answering ``/thumb`` and ``/art`` with distinct bytes,
    and 404 for anything else -- so the wrong field reads as no artwork."""

    def handler(request):
        if request.url.path == "/thumb":
            return httpx.Response(200, content=POSTER_BYTES, headers={"content-type": "image/jpeg"})
        if request.url.path == "/art":
            return httpx.Response(
                200, content=BACKGROUND_BYTES, headers={"content-type": "image/jpeg"}
            )
        return httpx.Response(404)

    return handler


@pytest_asyncio.fixture
async def http_serving():
    client = AsyncClient(transport=httpx.MockTransport(_serves()))
    yield client
    await client.aclose()


def _headers():
    return {"X-Plex-Token": PLEX_TOKEN}


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    root_folder="A (1999)", season=None, episode=None):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder=root_folder, season_number=season, episode_number=episode,
    )
    session.add(item)
    await session.commit()
    return item


async def test_backup_writes_the_tree_at_the_right_paths(
    session, config, backup_root, http_serving
):
    await _add_item(session, rating_key="rk1", kind="movie", root_folder="A (1999)")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb", art="/art")})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 1, 0, 0)
    # library_folders is true in the example config -> the nested Kometa tree.
    poster = backup_root / "Movies" / "A (1999)" / "poster.jpg"
    background = backup_root / "Movies" / "A (1999)" / "background.jpg"
    assert poster.read_bytes() == POSTER_BYTES
    assert background.read_bytes() == BACKGROUND_BYTES


async def test_backup_skips_an_item_plex_serves_nothing_for(session, config, http_serving):
    await _add_item(session, rating_key="rk1")
    # No thumb and no art -> fetch_artwork returns None for every kind.
    plex = FakePlexClient({"rk1": FakeItem(thumb=None, art=None)})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 1, 0)


async def test_backup_skips_an_item_without_a_root_folder(session, config, http_serving):
    await _add_item(session, rating_key="rk1", root_folder=None)
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb")})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 1, 0)
    # An item with nowhere to file assets is never even resolved against Plex.
    assert plex.fetched == []


async def test_backup_never_uploads_to_plex(session, config, http_serving):
    """FakeItem.uploadPoster/uploadArt raise; a green run proves backup only
    reads. (DB read-only is covered by the row count staying put below.)"""
    await _add_item(session, rating_key="rk1")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb", art="/art")})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert result.written == 1  # got here without the upload assertions firing


async def test_backup_counts_a_failed_fetch(session, config, http_serving):
    await _add_item(session, rating_key="rk1")
    plex = FakePlexClient(error=RuntimeError("plex unreachable"))

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 0, 1)


async def test_backup_refuses_an_empty_table(session, config, http_serving):
    plex = FakePlexClient({})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert result.refused is not None
    assert "media_items" in result.refused
    assert result.as_response()["status"] == "refused"


async def test_backup_backs_up_a_title_card_at_the_episode_path(
    session, config, backup_root, http_serving
):
    """An episode's badged image is its poster, so a title card reads Plex's
    ``.thumb`` and files as ``S01E02.jpg`` under the show's folder."""
    await _add_item(
        session, rating_key="rk-ep", kind="episode", library="TV Shows",
        root_folder="The Show", season=1, episode=2,
    )
    plex = FakePlexClient({"rk-ep": FakeItem(thumb="/thumb")})

    result = await BackupMode(config, plex, http_serving, _headers()).run(session)

    assert result.written == 1
    assert (backup_root / "TV Shows" / "The Show" / "S01E02.jpg").read_bytes() == POSTER_BYTES
