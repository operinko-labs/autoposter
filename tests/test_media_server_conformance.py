"""The same behavioural questions, asked of every MediaServer (spec §10.2).

``impl`` yields ``(server, seed)`` where ``seed(intent, native_id)`` makes the
server hold that item. Task 3 adds the Plex arm; Task 14 adds Jellyfin.
"""
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import PlexClient
from autoposter.servers.base import (
    CAP_LOCK_ARTWORK, ItemNotFound, MediaServer, ServerItemRef, UnsupportedOnServer,
)
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

INTENT = RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020)


class _PlexItem:
    """A bare movie: enough of plexapi's shape for ``PlexClient`` to resolve,
    upload to and lock, without a real Plex server. No ``http`` client backs
    this double, which is exactly what makes ``fetch_artwork`` unusable here --
    see the skip on ``test_upload_without_lock_works_everywhere`` below."""

    def __init__(self, rating_key, section):
        self.ratingKey = int(rating_key)
        self.type = "movie"
        self.title = "Title"
        self.year = 2020
        self.librarySectionTitle = section.title
        self.guids = [type("G", (), {"id": "tmdb://1"})()]
        self.locations = ["/media/Movies/Title (2020)/Title (2020).mkv"]
        self.media = [type("M", (), {"parts": [type("P", (), {"file": self.locations[0]})()]})()]
        self.art = None
        self.thumb = None
        self.originalTitle = None
        self.uploaded = {}
        self.locked = set()

    def uploadPoster(self, filepath):
        self.uploaded["poster"] = open(filepath, "rb").read()

    def lockPoster(self):
        self.locked.add("poster")

    def uploadArt(self, filepath):
        self.uploaded["background"] = open(filepath, "rb").read()

    def lockArt(self):
        self.locked.add("background")


class _PlexSection:
    def __init__(self):
        self.title = "Movies"
        self.type = "movie"
        self.locations = ["/media/Movies"]
        self.items = {}

    def getGuid(self, guid):
        for item in self.items.values():
            if any(g.id == guid for g in item.guids):
                return item
        from plexapi.exceptions import NotFound
        raise NotFound(guid)


class _PlexServer:
    def __init__(self, section):
        self._section = section
        self.library = type("L", (), {"sections": lambda s: [section]})()

    def fetchItem(self, key):
        from plexapi.exceptions import NotFound
        try:
            return self._section.items[str(key)]
        except KeyError:
            raise NotFound(str(key))


@pytest.fixture(params=["fake-plex", "fake-jellyfin", "plex"])
def impl(request):
    if request.param == "fake-plex":
        server = FakeMediaServer(name="plex")
    elif request.param == "fake-jellyfin":
        server = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)
    else:
        section = _PlexSection()
        server = PlexClient(_PlexServer(section), excluded_libraries=[])

        def seed(intent, native_id):
            section.items[native_id] = _PlexItem(native_id, section)

        return server, seed

    def seed(intent, native_id):
        server.items[intent.dedupe_key] = resolved(server.name, native_id, tmdb_id=intent.tmdb_id)

    return server, seed


def test_it_is_a_media_server(impl):
    server, _ = impl
    assert isinstance(server, MediaServer)


async def test_resolve_answers_a_ref_naming_the_server(impl):
    server, seed = impl
    seed(INTENT, "42")
    item = await server.resolve(INTENT)
    assert item.ref == ServerItemRef(server.name, "42", "Movies", "movie")


async def test_an_unknown_item_is_item_not_found(impl):
    server, _ = impl
    with pytest.raises(ItemNotFound):
        await server.resolve(INTENT)


async def test_upload_without_lock_works_everywhere(impl):
    server, seed = impl
    if isinstance(server, PlexClient):
        pytest.skip("Plex read-back needs http; covered by tests/test_plex_artwork.py")
    seed(INTENT, "42")
    item = await server.resolve(INTENT)
    await server.upload_artwork(item.ref, b"png", "poster", lock=False)
    assert await server.fetch_artwork(item.ref, "poster") == b"png"


async def test_lock_is_a_capability_not_a_surprise(impl):
    server, seed = impl
    seed(INTENT, "42")
    item = await server.resolve(INTENT)
    if CAP_LOCK_ARTWORK in server.capabilities:
        await server.upload_artwork(item.ref, b"png", "poster", lock=True)
    else:
        with pytest.raises(UnsupportedOnServer):
            await server.upload_artwork(item.ref, b"png", "poster", lock=True)
