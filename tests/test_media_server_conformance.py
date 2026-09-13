"""The same behavioural questions, asked of every MediaServer (spec §10.2).

``impl`` yields ``(server, seed)`` where ``seed(intent, native_id)`` makes the
server hold that item. Task 3 adds the Plex arm; Task 14 adds Jellyfin.
"""
import httpx
import pytest
import pytest_asyncio

from autoposter.intake.arr import RenderIntent
from autoposter.jellyfin.client import JellyfinApi, JellyfinClient
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


class _JellyfinMock:
    """A ``MockTransport`` handler backing one Movies library, over
    ``JellyfinClient``: the same VirtualFolders/Items/Images shapes
    ``tests/test_jellyfin_index.py`` uses. ``seed`` appends a movie dto;
    uploaded image bytes are recorded and served back by a later GET, which
    is what makes the ``fetch_artwork`` conformance case work on this arm."""

    def __init__(self):
        self.movies: list[dict] = []
        self.images: dict[tuple[str, str], tuple[bytes, str]] = {}

    def seed(self, intent, native_id):
        self.movies.append({
            "Id": native_id, "Name": intent.title, "Type": "Movie",
            "ProductionYear": intent.year,
            "ProviderIds": {"Tmdb": str(intent.tmdb_id)} if intent.tmdb_id else {},
            "Path": f"/media/Movies/{intent.title} ({intent.year})/t.mkv",
        })

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[
                {"Name": "Movies", "CollectionType": "movies", "Locations": ["/media/Movies"], "ItemId": "lib1"},
            ])
        if path == "/Items":
            params = request.url.params
            if "searchTerm" in params:
                hits = [m for m in self.movies if m["Name"] == params["searchTerm"]]
                return httpx.Response(200, json={"Items": hits})
            return httpx.Response(200, json={"Items": list(self.movies)})
        parts = path.split("/")
        if len(parts) == 3 and parts[1] == "Items":
            item_id = parts[2]
            for movie in self.movies:
                if movie["Id"] == item_id:
                    return httpx.Response(200, json=movie)
            return httpx.Response(404)
        if len(parts) == 5 and parts[1] == "Items" and parts[3] == "Images":
            item_id, image_type = parts[2], parts[4]
            if request.method == "POST":
                self.images[item_id, image_type] = (request.content, request.headers["Content-Type"])
                return httpx.Response(204)
            stored = self.images.get((item_id, image_type))
            if stored is None:
                return httpx.Response(404)
            data, content_type = stored
            return httpx.Response(200, content=data, headers={"Content-Type": content_type})
        return httpx.Response(404)


@pytest_asyncio.fixture(params=["fake-plex", "fake-jellyfin", "plex", "jellyfin"])
async def impl(request):
    if request.param == "fake-plex":
        server = FakeMediaServer(name="plex")

        def seed(intent, native_id):
            server.items[intent.dedupe_key] = resolved(server.name, native_id, tmdb_id=intent.tmdb_id)

        yield server, seed
        return
    if request.param == "fake-jellyfin":
        server = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

        def seed(intent, native_id):
            server.items[intent.dedupe_key] = resolved(server.name, native_id, tmdb_id=intent.tmdb_id)

        yield server, seed
        return
    if request.param == "jellyfin":
        mock = _JellyfinMock()
        http = httpx.AsyncClient(transport=httpx.MockTransport(mock.handler))
        api = JellyfinApi(http, "https://jf.example", api_key="k", version="v1")
        server = JellyfinClient(api, excluded_libraries=[], library_map={}, replace_thumb_with_backdrop=False)
        yield server, mock.seed
        await http.aclose()
        return

    section = _PlexSection()
    server = PlexClient(_PlexServer(section), excluded_libraries=[])

    def seed(intent, native_id):
        section.items[native_id] = _PlexItem(native_id, section)

    yield server, seed


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
        pytest.skip(
            "Plex read-back needs http; covered indirectly by "
            "tests/test_artwork_backup.py (fetch_artwork via BackupMode), "
            "tests/test_artwork_reset.py (artwork_provenance via ResetMode) "
            "and tests/test_api_artwork.py"
        )
    seed(INTENT, "42")
    item = await server.resolve(INTENT)
    await server.upload_artwork(item.ref, b"png", "poster", lock=False)
    fetched = await server.fetch_artwork(item.ref, "poster")
    assert fetched is not None and fetched[0] == b"png"


async def test_lock_is_a_capability_not_a_surprise(impl):
    server, seed = impl
    seed(INTENT, "42")
    item = await server.resolve(INTENT)
    if CAP_LOCK_ARTWORK in server.capabilities:
        await server.upload_artwork(item.ref, b"png", "poster", lock=True)
    else:
        with pytest.raises(UnsupportedOnServer):
            await server.upload_artwork(item.ref, b"png", "poster", lock=True)
