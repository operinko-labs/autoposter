"""The same behavioural questions, asked of every MediaServer (spec §10.2).

``impl`` yields ``(server, seed)`` where ``seed(intent, native_id)`` makes the
server hold that item. Task 3 adds the Plex arm; Task 14 adds Jellyfin.
"""
import pytest

from autoposter.intake.arr import RenderIntent
from autoposter.servers.base import (
    CAP_LOCK_ARTWORK, ItemNotFound, MediaServer, ServerItemRef, UnsupportedOnServer,
)
from media_server_doubles import FakeMediaServer, JELLYFIN_CAPS, resolved

INTENT = RenderIntent(kind="movie", title="Title", tmdb_id=1, year=2020)


@pytest.fixture(params=["fake-plex", "fake-jellyfin"])
def impl(request):
    if request.param == "fake-plex":
        server = FakeMediaServer(name="plex")
    else:
        server = FakeMediaServer(name="jellyfin", capabilities=JELLYFIN_CAPS)

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
