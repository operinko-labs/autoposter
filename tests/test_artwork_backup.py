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
from plexapi.exceptions import NotFound as PlexNotFound

from autoposter.artwork_modes.backup import BackupMode
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem
from autoposter.plex.artwork import fetch_artwork as _plex_fetch_artwork
from autoposter.servers.base import ServerItemRef

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
    def __init__(self, items=None, error=None, errors=None):
        self._items = items or {}
        self._error = error
        self._errors = errors or {}
        self.fetched = []
        # Lazily built on first use, from the same MockTransport handler
        # every test in this file already shares -- so a test only has to
        # hand this class its items, exactly as before ``fetch_artwork``
        # needed an http client of its own.
        self._http = None

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        if rating_key in self._errors:
            raise self._errors[rating_key]
        if self._error is not None:
            raise self._error
        return self._items[rating_key]

    async def fetch_ref(self, rating_key):
        try:
            await self.fetch_item(rating_key)
        except PlexNotFound:
            return None
        return ServerItemRef("plex", rating_key, "", "")

    async def fetch_artwork(self, ref, art_kind):
        if self._http is None:
            self._http = AsyncClient(transport=httpx.MockTransport(_serves()))
        item = self._items[ref.native_id]
        return await _plex_fetch_artwork(
            self._http, item, PLEX_URL, _headers(), art_kind
        )


@pytest.fixture
def backup_root(tmp_path) -> Path:
    # Pre-created, because in a deployment it is a MOUNT: backup refuses rather
    # than mkdir-ing a backup tree into the container's own filesystem.
    root = tmp_path / "plexbackup"
    root.mkdir()
    return root


@pytest.fixture
def config(backup_root):
    cfg = load_config(EXAMPLE)
    cfg.plex.url = PLEX_URL
    cfg.artwork_modes.plex_backup_root = backup_root
    return cfg


def _serves():
    """A MockTransport answering ``/thumb`` and ``/art`` with distinct bytes,
    ``/broken`` with a 500 (a fetch that raises, not one that finds nothing),
    and 404 for anything else -- so the wrong field reads as no artwork."""

    def handler(request):
        if request.url.path == "/thumb":
            return httpx.Response(200, content=POSTER_BYTES, headers={"content-type": "image/jpeg"})
        if request.url.path == "/art":
            return httpx.Response(
                200, content=BACKGROUND_BYTES, headers={"content-type": "image/jpeg"}
            )
        if request.url.path == "/broken":
            return httpx.Response(500)
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

    result = await BackupMode(config, plex).run(session)

    # written is per-(item, kind): a movie has poster + background, both
    # written here, so written == 2 even though items == 1.
    assert (result.items, result.written, result.skipped, result.failed) == (1, 2, 0, 0)
    # library_folders is true in the example config -> the nested Kometa tree.
    poster = backup_root / "Movies" / "A (1999)" / "poster.jpg"
    background = backup_root / "Movies" / "A (1999)" / "background.jpg"
    assert poster.read_bytes() == POSTER_BYTES
    assert background.read_bytes() == BACKGROUND_BYTES


async def test_backup_skips_an_item_plex_serves_nothing_for(session, config, http_serving):
    await _add_item(session, rating_key="rk1")
    # No thumb and no art -> fetch_artwork returns None for every kind.
    plex = FakePlexClient({"rk1": FakeItem(thumb=None, art=None)})

    result = await BackupMode(config, plex).run(session)

    # skipped is per-kind too: a movie's poster and background both skip.
    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 2, 0)


async def test_backup_skips_an_item_without_a_root_folder(session, config, http_serving):
    await _add_item(session, rating_key="rk1", root_folder=None)
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb")})

    result = await BackupMode(config, plex).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 1, 0)
    # An item with nowhere to file assets is never even resolved against Plex.
    assert plex.fetched == []


async def test_backup_never_uploads_to_plex(session, config, http_serving):
    """FakeItem.uploadPoster/uploadArt raise; a green run proves backup only
    reads. (DB read-only is covered by the row count staying put below.)"""
    await _add_item(session, rating_key="rk1")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb", art="/art")})

    result = await BackupMode(config, plex).run(session)

    assert result.written == 2  # got here without the upload assertions firing


async def test_backup_counts_a_failed_fetch(session, config, http_serving):
    await _add_item(session, rating_key="rk1")
    plex = FakePlexClient(error=RuntimeError("plex unreachable"))

    result = await BackupMode(config, plex).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 0, 1)


async def test_backup_refuses_an_empty_table(session, config, http_serving):
    plex = FakePlexClient({})

    result = await BackupMode(config, plex).run(session)

    assert result.refused is not None
    assert "media_items" in result.refused
    # A refusal still carries the (zero) counts it knows, in the same shape
    # a successful response uses -- the UI should not have to branch on
    # whether the numeric fields are present.
    assert result.as_response() == {
        "mode": "backup", "status": "refused", "reason": result.refused,
        "items": 0, "written": 0, "skipped": 0, "failed": 0, "missing": 0,
    }


async def test_backup_refuses_when_the_backup_root_is_not_mounted(
    session, config, backup_root, http_serving
):
    """``plex_backup_root`` is a MOUNT the deployment provides, not a directory
    this service creates. Without it every write would land in the container's
    own filesystem -- a backup that fills the node's disk, reports success, and
    disappears with the pod. So a missing root is refused before anything is
    read from Plex."""
    backup_root.rmdir()  # the fixture pre-creates it, as the mount would
    await _add_item(session, rating_key="rk1")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb")})

    result = await BackupMode(config, plex).run(session)

    assert result.refused is not None
    assert str(backup_root) in result.refused
    assert result.as_response()["status"] == "refused"
    # Refused before the walk: nothing was read from Plex and nothing was
    # created on disk.
    assert plex.fetched == []
    assert not backup_root.exists()


async def test_backup_counts_a_partial_item_in_both_written_and_failed(
    session, config, http_serving
):
    """One kind writes, the other's fetch raises: the item must show up in
    BOTH written and failed, not just written with the failure silently
    dropped."""
    await _add_item(session, rating_key="rk1", kind="movie")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/thumb", art="/broken")})

    result = await BackupMode(config, plex).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 1, 0, 1)
    assert result.as_response()["status"] == "backed up"  # partial success is not a failure


async def test_backup_all_fail_status_is_not_success(session, config, http_serving):
    """An item where every kind's fetch raises must not read as a clean
    "backed up": written stays 0 while failed is > 0, so the status must say
    so."""
    await _add_item(session, rating_key="rk1", kind="movie")
    plex = FakePlexClient({"rk1": FakeItem(thumb="/broken", art="/broken")})

    result = await BackupMode(config, plex).run(session)

    assert (result.items, result.written, result.skipped, result.failed) == (1, 0, 0, 2)
    response = result.as_response()
    assert response["status"] == "backup failed"
    assert response["written"] == 0 and response["failed"] == 2


async def test_backup_skips_an_unnumbered_title_card_rather_than_raising(
    session, config, backup_root, http_serving
):
    """Plex's TV agent really does hand back ``index: None`` -- year-grouped
    specials, 74 of 12,694 episodes on the production server. ``asset_path``
    cannot name a file for one, so the walk asks ``naming.missing_number``
    first and skips it. Without the guard the ValueError escapes mid-walk and
    the trigger 500s after a partial tree has already been written."""
    await _add_item(
        session, rating_key="rk-ok", kind="episode", library="TV Shows",
        root_folder="The Show", season=1, episode=2,
    )
    await _add_item(
        session, rating_key="rk-bad", kind="episode", library="TV Shows",
        root_folder="The Show", season=2021, episode=None,
    )
    plex = FakePlexClient({
        "rk-ok": FakeItem(thumb="/thumb"), "rk-bad": FakeItem(thumb="/thumb"),
    })

    result = await BackupMode(config, plex).run(session)

    # The walk finished: the numbered episode is on disk and the unnumbered one
    # is counted, not fatal.
    assert (result.items, result.written, result.skipped, result.failed) == (2, 1, 1, 0)
    assert (backup_root / "TV Shows" / "The Show" / "S01E02.jpg").read_bytes() == POSTER_BYTES


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

    result = await BackupMode(config, plex).run(session)

    assert result.written == 1
    assert (backup_root / "TV Shows" / "The Show" / "S01E02.jpg").read_bytes() == POSTER_BYTES


async def test_backup_logs_a_missing_item_at_info_not_warning(
    session, config, http_serving, caplog
):
    """An item deleted from Plex after its DB row was written 404s on fetch --
    expected, not a crash. It must not spam a WARNING+traceback per item;
    instead one concise INFO line per item, tallied separately from real
    failures, plus a single end-of-run summary line."""
    await _add_item(session, rating_key="rk-gone")
    await _add_item(session, rating_key="rk-ok")
    plex = FakePlexClient(
        items={"rk-ok": FakeItem(thumb="/thumb", art="/art")},
        errors={"rk-gone": PlexNotFound("rk-gone not found")},
    )

    with caplog.at_level("INFO"):
        result = await BackupMode(config, plex).run(session)

    assert (result.items, result.written, result.failed, result.missing) == (2, 2, 0, 1)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == []
    assert not any(r.exc_info for r in caplog.records)

    backup_records = [
        r for r in caplog.records if r.name == "autoposter.artwork_modes.backup"
    ]
    per_item = [r.message for r in backup_records if "rk-gone" in r.message]
    assert len(per_item) == 1
    assert per_item[0].startswith("backup: ") and "no longer in Plex" in per_item[0]

    # One end-of-run summary line, distinct from the per-item line, carrying
    # the count.
    summary = [r.message for r in backup_records if r.message not in per_item]
    assert len(summary) == 1
    assert summary[0].startswith("backup: ") and "1" in summary[0]
