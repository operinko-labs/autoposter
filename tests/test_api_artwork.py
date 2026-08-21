"""GET /api/items/{item_id}/artwork/{art_kind} and its /live sibling.

The first is the base image bytes. It is the first endpoint in the project that
reads a file for a caller, and the path it reads comes out of the database, so
most of what is pinned here is not "does it serve the image" but "what does it
refuse to serve": a row pointing outside the asset tree, a symlink inside the
tree aimed out of it, and a URL segment that looks like a path.

The second is what Plex is currently showing, proxied. Nothing there touches
disk, so what is pinned instead is which Plex field each art kind reads, that
the outbound request carries its own timeout, and that the three ways it can
fail stay three distinguishable statuses -- 404 for "Plex has nothing" against
503 for "Plex could not be asked". Every Plex request in this file goes to an
``httpx.MockTransport``; conftest's ``no_outbound_network`` fails the test if
one ever escapes.
"""
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import requests
from httpx import ASGITransport, AsyncClient
from plexapi.exceptions import NotFound as PlexNotFound
from sqlalchemy import select
from test_spa_serving import _raw_asgi_get

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem, Render
from autoposter.plex.artwork import ARTWORK_FETCH_TIMEOUT

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
# Recognisable bytes rather than a real JPEG: nothing in the endpoint decodes
# the image, and a fixture file would only hide which bytes came back.
IMAGE_BYTES = b"\xff\xd8\xff\xe0 not really a jpeg, but these exact bytes"
SECRET = "do not serve me"
# Distinct bytes from IMAGE_BYTES so a live response can never be mistaken for
# the base one having been served instead.
LIVE_BYTES = b"RIFF\x00\x00\x00\x00WEBP what plex is actually showing"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token-for-this-test"


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token=PLEX_TOKEN, tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest_asyncio.fixture
async def app(session_factory, assets_root):
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
    # The example config names a real host. Pinned to an unroutable one so that
    # a request escaping MockTransport cannot possibly reach it.
    config.plex.url = PLEX_URL
    return create_app(config, session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _item_with_render(session, asset_path: str, art_kind: str = "poster") -> int:
    """One media item and one render row for it; returns the item id."""
    session.add(MediaItem(rating_key="rk1", library="Movies", kind="movie", title="A"))
    await session.flush()
    item_id = (await session.execute(select(MediaItem))).scalars().one().id
    session.add(
        Render(
            item_id=item_id, art_kind=art_kind, status="rendered", asset_path=asset_path
        )
    )
    await session.commit()
    return item_id


async def test_serves_the_base_image_for_a_render(client, auth_headers, session, assets_root):
    asset = assets_root / "Movies" / "A (1999)" / "poster.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == IMAGE_BYTES


@pytest.mark.parametrize(
    ("suffix", "content_type"),
    [(".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")],
)
async def test_content_type_comes_from_the_file_suffix(
    client, auth_headers, session, assets_root, suffix, content_type
):
    """Never from the request. The bytes are identical in all three cases, so
    the only thing that can be deciding the header is the name on disk."""
    asset = assets_root / f"poster{suffix}"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type


async def test_an_unknown_art_kind_is_404(client, auth_headers, session, assets_root):
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), art_kind="poster")

    response = await client.get(f"/api/items/{item_id}/artwork/background", headers=auth_headers)

    assert response.status_code == 404


async def test_an_unknown_item_is_404(client, auth_headers):
    response = await client.get("/api/items/999999/artwork/poster", headers=auth_headers)
    assert response.status_code == 404


async def test_a_render_row_pointing_at_a_missing_file_is_404(
    client, auth_headers, session, assets_root
):
    """asset_path is written when the row is created, before anything exists on
    disk (see render/pipeline.py's _get_or_create_render), so every no_art,
    truncated, skipped and failed row names a file that was never produced.
    That is an ordinary 404, not a 500."""
    item_id = await _item_with_render(session, str(assets_root / "never-rendered.jpg"))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 404


async def test_a_render_row_pointing_outside_assets_root_is_refused(
    client, auth_headers, session, assets_root
):
    """The reason this endpoint does not just do FileResponse(asset_path).

    The path is a Text column, so whatever put it there decides what gets
    read. Nothing this project writes can produce such a row -- which is
    exactly why an implementation that trusts the column looks correct
    forever, right up until an assets_root change, a restored dump, or an
    import path makes it untrue.
    """
    outside = assets_root.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")
    item_id = await _item_with_render(session, str(outside))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 404
    assert SECRET not in response.text


async def test_a_symlink_out_of_assets_root_is_refused(
    client, auth_headers, session, assets_root
):
    """A containment check has to resolve symlinks, not just normalise the
    string. This row's asset_path *is* inside assets_root -- a lexical check
    passes it, and then reads the file it points at."""
    outside = assets_root.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")
    link = assets_root / "poster.jpg"
    link.symlink_to(outside)
    item_id = await _item_with_render(session, str(link))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 404
    assert SECRET not in response.text


@pytest.mark.parametrize(
    "art_kind",
    ["../outside.txt", "./../outside.txt", "Movies/../../outside.txt"],
)
async def test_a_path_shaped_art_kind_is_404_not_a_file_read(
    app, auth_headers, session, assets_root, art_kind
):
    """art_kind selects a database row; it must never reach the filesystem.

    Driven straight at the ASGI app rather than through AsyncClient: httpx
    canonicalises dot segments while building the URL -- `httpx.URL(
    "http://test/a/../../x").raw_path` is `b"/x"` -- so the same request made
    through the client never puts a `..` on the wire and passes against an
    implementation that would have served the file. That mistake shipped in
    this repository once already (see tests/test_spa_serving.py).

    Authenticated, and with a real item and render row present, so that an
    implementation which resolved the row and *then* built its path from the
    URL still gets all the way to the read. Stopping at the 401 or at a
    missing row would make this green for reasons that have nothing to do
    with the path.
    """
    outside = assets_root.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")
    # A real directory inside the tree, so the `Movies/../..` case traverses
    # rather than stopping at an ENOENT the kernel raises on the missing
    # component -- which would make that parameter pass for the wrong reason.
    (assets_root / "Movies").mkdir()
    item_id = await _item_with_render(session, str(assets_root / "poster.jpg"))

    status, _headers, body = await _raw_asgi_get(
        app, f"/api/items/{item_id}/artwork/{art_kind}", headers=auth_headers
    )

    assert status == 404
    assert SECRET not in body.decode("utf-8", "replace")


async def test_artwork_requires_a_session(client, session, assets_root):
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))

    response = await client.get(f"/api/items/{item_id}/artwork/poster")

    assert response.status_code == 401
    assert IMAGE_BYTES not in response.content


# --- GET /api/items/{item_id}/artwork/{art_kind}/live -------------------------


class _FakePlexItem:
    """Only what the endpoint reads off a plexapi object.

    ``refresh`` exists so a test can prove nothing calls it: a metadata refresh
    has Plex re-pull from its agents and can overwrite the artwork this service
    uploaded, which makes it the one thing this endpoint must never do while
    holding an item.
    """

    def __init__(self, thumb=None, art=None):
        self.thumb = thumb
        self.art = art
        self.refreshed = False

    def refresh(self):
        self.refreshed = True


class _FakePlexClient:
    """Stands in for PlexClient. ``fetch_item`` is async and, in the real one,
    a plain GET run in a thread."""

    def __init__(self, item=None, error=None):
        self.item = item
        self.error = error
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        if self.error is not None:
            raise self.error
        return self.item


@pytest_asyncio.fixture
async def wire_plex(app):
    """Give the app a fake Plex client and an httpx client on a MockTransport.

    Mirrors production wiring: ``app.state.plex`` is set by main.build() and
    ``app.state.http`` by the lifespan, neither of which runs under create_app()
    alone.
    """
    clients = []

    def wire(*, item=None, error=None, handler=None):
        def refuse(request):
            raise AssertionError(f"no Plex request expected, got {request.url}")

        http = AsyncClient(transport=httpx.MockTransport(handler or refuse))
        clients.append(http)
        app.state.plex = _FakePlexClient(item=item, error=error)
        app.state.http = http
        return app.state.plex

    yield wire
    for http in clients:
        await http.aclose()


async def _media_item(session, rating_key: str = "rk-live") -> int:
    """One media item, no render row: the live endpoint reads Plex, not disk."""
    item = MediaItem(rating_key=rating_key, library="Movies", kind="movie", title="A")
    session.add(item)
    await session.commit()
    return item.id


def _serves(data: bytes, content_type: str = "image/webp", seen: list | None = None):
    """A MockTransport handler answering every artwork URL with ``data``."""

    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, content=data, headers={"content-type": content_type})

    return handler


async def test_serves_what_plex_is_currently_showing(client, auth_headers, session, wire_plex):
    seen = []
    wire_plex(
        item=_FakePlexItem(thumb="/library/metadata/42/thumb/1700000000"),
        handler=_serves(LIVE_BYTES, seen=seen),
    )
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == LIVE_BYTES
    assert response.headers["content-type"] == "image/webp"
    # Plex's own thumb path under the configured base, with the token in a
    # header rather than the query string.
    assert str(seen[0].url) == f"{PLEX_URL}/library/metadata/42/thumb/1700000000"
    assert seen[0].headers["X-Plex-Token"] == PLEX_TOKEN


async def test_the_live_bytes_come_from_plex_not_from_disk(
    client, auth_headers, session, assets_root, wire_plex
):
    """The base endpoint and the live one must be able to disagree -- that
    disagreement is the whole point of showing them side by side."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))
    wire_plex(
        item=_FakePlexItem(thumb="/library/metadata/42/thumb/1"), handler=_serves(LIVE_BYTES)
    )

    live = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)
    base = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert live.content == LIVE_BYTES
    assert base.content == IMAGE_BYTES


async def test_a_background_reads_plex_art_and_a_title_card_reads_plex_thumb(
    client, auth_headers, session, wire_plex
):
    """Mirrors upload_artwork's split. Reading ``.thumb`` for a background --
    or ``.art`` for a title card -- answers with a real image that is the wrong
    one, which is exactly the kind of wrong this page exists to reveal."""
    seen = []
    wire_plex(
        item=_FakePlexItem(thumb="/thumb/path", art="/art/path"),
        handler=_serves(LIVE_BYTES, seen=seen),
    )
    item_id = await _media_item(session)

    await client.get(f"/api/items/{item_id}/artwork/background/live", headers=auth_headers)
    await client.get(f"/api/items/{item_id}/artwork/title_card/live", headers=auth_headers)

    assert [request.url.path for request in seen] == ["/art/path", "/thumb/path"]


async def test_the_outbound_request_carries_its_own_timeout(
    app, client, auth_headers, session, wire_plex
):
    """Otherwise a Plex that accepts the connection and then says nothing holds
    the request open for whatever the shared client's default happens to be --
    30s in app.py, on a page somebody is waiting in front of."""
    seen = []
    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES, seen=seen))
    item_id = await _media_item(session)

    await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    # Checked against the client's own default too, so this cannot pass by the
    # request merely inheriting whatever the client was built with.
    assert app.state.http.timeout.read != ARTWORK_FETCH_TIMEOUT
    assert seen[0].extensions["timeout"] == {
        "connect": ARTWORK_FETCH_TIMEOUT,
        "read": ARTWORK_FETCH_TIMEOUT,
        "write": ARTWORK_FETCH_TIMEOUT,
        "pool": ARTWORK_FETCH_TIMEOUT,
    }


async def test_a_plex_that_cannot_be_connected_to_is_503(client, auth_headers, session, wire_plex):
    def refused(request):
        raise httpx.ConnectError("connection refused")

    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=refused)
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503
    assert "ConnectError" in response.json()["detail"]


async def test_a_plex_that_accepts_and_then_never_answers_is_503(
    client, auth_headers, session, wire_plex
):
    """The timeout firing has to land as a 503 too, not as an unhandled
    exception -- httpx raises a different class for it than for a refusal."""

    def hangs(request):
        raise httpx.ReadTimeout("timed out")

    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=hangs)
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503
    assert "ReadTimeout" in response.json()["detail"]


async def test_a_plex_that_is_unreachable_while_resolving_the_item_is_503(
    client, auth_headers, session, wire_plex
):
    """Plex is asked twice -- once through plexapi for the item, once over HTTP
    for the image -- and the first is ``requests``, not httpx, so it fails with
    an entirely different exception type."""
    wire_plex(error=requests.exceptions.ConnectionError("no route to host"))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503
    assert "ConnectionError" in response.json()["detail"]


async def test_an_error_status_from_plex_is_503_and_its_body_is_not_passed_through(
    client, auth_headers, session, wire_plex
):
    def unauthorised(request):
        return httpx.Response(401, text=SECRET)

    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=unauthorised)
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503
    assert SECRET not in response.text


async def test_an_item_with_no_artwork_in_plex_is_404(client, auth_headers, session, wire_plex):
    """404, not 503: Plex answered perfectly well, it just has no poster. The
    UI says "nothing uploaded yet" for one and "Plex is down" for the other."""
    # No handler, so any outbound request at all fails this test.
    wire_plex(item=_FakePlexItem(thumb=None))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404


async def test_an_artwork_url_that_404s_is_404(client, auth_headers, session, wire_plex):
    """Plex names a thumb that is no longer there. Still "no artwork", not
    "Plex is unreachable"."""

    def gone(request):
        return httpx.Response(404)

    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=gone)
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404


async def test_an_item_plex_no_longer_has_is_404(client, auth_headers, session, wire_plex):
    wire_plex(error=PlexNotFound("no such item"))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404


async def test_an_unknown_item_is_404_without_asking_plex(client, auth_headers, wire_plex):
    plex = wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))

    response = await client.get("/api/items/999999/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404
    assert plex.fetched == []


async def test_an_unknown_art_kind_is_404_without_asking_plex(
    client, auth_headers, session, wire_plex
):
    """``art_kind`` picks which plexapi field to read. An unrecognised one must
    not fall back to the poster and answer 200 with a plausible wrong image."""
    plex = wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/banner/live", headers=auth_headers)

    assert response.status_code == 404
    assert plex.fetched == []


async def test_the_content_type_is_not_taken_from_plex_verbatim(
    client, auth_headers, session, wire_plex
):
    """Plex's Content-Type is remote input. Echoing it would let whatever is at
    that URL decide how a browser interprets a body served from our origin."""
    wire_plex(
        item=_FakePlexItem(thumb="/thumb/path"),
        handler=_serves(b"<script>alert(1)</script>", content_type="text/html"),
    )
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"


async def test_the_plex_object_is_never_refreshed(client, auth_headers, session, wire_plex):
    """A metadata refresh has Plex re-pull from its agents, which can overwrite
    the artwork this service uploaded. Forbidden project-wide -- see
    tests/test_plex_writer.py's source scan, which covers plex/ but not api/."""
    item = _FakePlexItem(thumb="/thumb/path")
    wire_plex(item=item, handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 200
    assert item.refreshed is False


async def test_live_artwork_is_503_when_this_instance_has_no_plex_connection(
    client, auth_headers, session
):
    """create_app leaves app.state.plex/http None; main.build() and the lifespan
    fill them in. Without them there is nothing to ask, which is a 503 rather
    than an AttributeError 500."""
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503


async def test_live_artwork_requires_a_session(client, session, wire_plex):
    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live")

    assert response.status_code == 401
    assert LIVE_BYTES not in response.content
