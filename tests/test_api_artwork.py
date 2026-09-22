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
import os
import threading
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import requests
from httpx import ASGITransport, AsyncClient
from plexapi.exceptions import BadRequest as PlexBadRequest
from plexapi.exceptions import NotFound as PlexNotFound
from test_spa_serving import _raw_asgi_get

from autoposter.api import artwork as artwork_module
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import Render
from autoposter.plex.artwork import ARTWORK_FETCH_TIMEOUT
from autoposter.plex.artwork import fetch_artwork as _plex_fetch_artwork
from autoposter.servers.base import ServerItemRef

from conftest import seed_media_item

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


async def _item_with_render(
    session, asset_path: str, art_kind: str = "poster", base_sha256: str | None = "9" * 64
) -> int:
    """One media item and one render row for it; returns the item id.

    ``base_sha256`` defaults to a digest because that is the shape of every
    row whose file the pipeline actually produced -- the endpoint refuses to
    serve a NULL-digest row, so a default of None would make every test here
    exercise nothing but that refusal.

    The id comes off the flushed object rather than from a re-query: a
    ``select(MediaItem).one()`` raised MultipleResultsFound the moment a test
    called this twice.
    """
    item = await seed_media_item(session, "rk1", library="Movies", kind="movie", title="A")
    session.add(
        Render(
            item_id=item.id, art_kind=art_kind, status="rendered",
            asset_path=asset_path, base_sha256=base_sha256,
        )
    )
    await session.commit()
    return item.id


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


async def test_the_base_image_carries_nosniff(client, auth_headers, session, assets_root):
    """The suffix whitelist is the entire defence for this body: there is no
    security-headers middleware anywhere in src/, and this is the first
    endpoint in the project serving file bytes from our own origin."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("rewrite", ["new-size-same-mtime", "same-size-new-mtime"])
async def test_rewriting_the_served_file_changes_the_etag_so_the_old_one_gets_200(
    client, auth_headers, session, assets_root, rewrite
):
    """The stale-artwork bug.

    base_sha256 is the digest of the *source* image the pipeline downloaded
    (render/pipeline.py), not of the composite it wrote to asset_path. A
    re-render caused by a text, font, overlay or config change rewrites the
    file at the same path from the same source, so the row -- base_sha256
    included -- does not change at all. An ETag taken from the row stayed the
    same, the browser's If-None-Match matched, and the operator got a 304 for
    an image that no longer existed. Nothing in the database is touched here;
    only the file is.

    Each parameter changes exactly one of the two things the tag is made of,
    so a tag that dropped either would fail one of them. The mtime is set
    explicitly rather than by sleeping: the development Docker host's clock
    steps backwards, so "written later" need not read as a later mtime.
    """
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256="e" * 64)
    url = f"/api/items/{item_id}/artwork/poster"

    first = await client.get(url, headers=auth_headers)
    assert first.status_code == 200
    old_etag = first.headers["etag"]
    before = asset.stat()

    if rewrite == "new-size-same-mtime":
        rewritten = IMAGE_BYTES + b" -- re-rendered with a new title font"
        asset.write_bytes(rewritten)
        os.utime(asset, ns=(before.st_atime_ns, before.st_mtime_ns))
    else:
        rewritten = IMAGE_BYTES.replace(b"these", b"THESE")
        assert len(rewritten) == len(IMAGE_BYTES)
        asset.write_bytes(rewritten)
        os.utime(asset, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))

    second = await client.get(url, headers={**auth_headers, "If-None-Match": old_etag})

    assert second.status_code == 200
    assert second.content == rewritten
    assert second.headers["etag"] != old_etag


async def test_the_asset_stat_and_read_never_run_on_the_event_loop(
    client, auth_headers, session, assets_root, monkeypatch
):
    """assets_root can be an NFS mount, and the realpath walk, the stat and
    the read are all blocking. This loop also carries the queue workers, the
    scheduler and the Plex liveness probe, so any of them done on it stalls
    all three -- and nothing about the response would show it, which is how a
    later "simplification" to FileResponse(resolved), a bare read_bytes(), or
    an ETag stat taken before the thread hop would pass every other test in
    this file."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset))
    real_stat = artwork_module._stat_asset
    real_read = artwork_module._read_asset
    ran_on: dict[str, threading.Thread] = {}

    def recording_stat(*args, **kwargs):
        ran_on["stat"] = threading.current_thread()
        return real_stat(*args, **kwargs)

    def recording_read(*args, **kwargs):
        ran_on["read"] = threading.current_thread()
        return real_read(*args, **kwargs)

    monkeypatch.setattr(artwork_module, "_stat_asset", recording_stat)
    monkeypatch.setattr(artwork_module, "_read_asset", recording_read)

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == IMAGE_BYTES
    # Against the thread this test body runs on -- the one hosting the event
    # loop -- rather than against main_thread(), so this holds whichever
    # thread the loop happens to be on.
    assert set(ran_on) == {"stat", "read"}, f"a stand-in was never called: {ran_on}"
    for step, thread in ran_on.items():
        assert thread is not threading.current_thread(), (
            f"the {step} ran on the event loop thread ({thread!r})"
        )


async def test_the_etag_describes_the_file_on_disk_not_the_rows_digest(
    client, auth_headers, session, assets_root
):
    """W/"<size>-<mtime_ns>" from one stat of the resolved file.

    Not base_sha256. For a pipeline render that column is the digest of the
    downloaded *source* image, while the file served is the composite made
    from it: the two are different bytes, and the column does not move when a
    re-render rewrites the file (the regression test above). Only verbatim
    and adopted rows ever carried the served bytes' digest. A stat describes
    whatever is actually at the path -- including a file replaced on the
    shared NFS mount without the database knowing -- and costs no read. Weak,
    because equal size and mtime is strong evidence of equal bytes, not proof.
    """
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256="a" * 64)

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    stat = asset.stat()
    assert response.status_code == 200
    assert response.headers["etag"] == f'W/"{stat.st_size}-{stat.st_mtime_ns}"'
    assert "a" * 64 not in response.headers["etag"]
    # Private: these bytes sit behind a session, so no shared cache may hold
    # them for the next caller.
    assert "private" in response.headers["cache-control"]


@pytest.mark.parametrize("sent", ["{etag}", "{opaque}", '"other", {etag}', "*"])
async def test_a_matching_if_none_match_is_304_and_reads_no_file(
    client, auth_headers, session, assets_root, monkeypatch, sent
):
    """The point of the ETag: the tile already in the browser's cache costs a
    row lookup and one stat, and the file is never opened. The read is stubbed
    with a failure, so a 304 answered *after* reading cannot pass here.

    ``{opaque}`` is our tag without its ``W/``: RFC 9110's weak comparison,
    which If-None-Match requires, ignores the prefix on both sides. Our tag is
    always weak, so a comparison that stripped only the client's prefix would
    never match anything."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256="b" * 64)
    stat = asset.stat()
    opaque = f'"{stat.st_size}-{stat.st_mtime_ns}"'
    etag = f"W/{opaque}"

    def must_not_read(*args, **kwargs):
        raise AssertionError("the asset was read despite a matching If-None-Match")

    monkeypatch.setattr(artwork_module, "_read_asset", must_not_read)

    response = await client.get(
        f"/api/items/{item_id}/artwork/poster",
        headers={**auth_headers, "If-None-Match": sent.format(etag=etag, opaque=opaque)},
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


async def test_a_stale_if_none_match_serves_the_current_bytes(
    client, auth_headers, session, assets_root
):
    """A tag from a file that has since been replaced names a size and mtime
    the file no longer has, so the browser's copy has to lose."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256="c" * 64)

    response = await client.get(
        f"/api/items/{item_id}/artwork/poster",
        headers={**auth_headers, "If-None-Match": 'W/"1-1"'},
    )

    assert response.status_code == 200
    assert response.content == IMAGE_BYTES


async def test_a_row_with_no_digest_is_404_even_when_a_file_sits_at_its_path(
    client, auth_headers, session, assets_root, monkeypatch
):
    """A NULL base_sha256 means the pipeline never rendered or adopted bytes
    into this row -- and that is a refusal to serve, not merely "no ETag".

    On a cutover library, a Kometa-era file can occupy the exact path a
    no_art row names (naming.asset_path reproduces that layout by design), so
    "the file exists" proves nothing about it being ours. Serving it showed
    badged art on tiles whose status honestly said no_art. The file must not
    even be read, and the body must be indistinguishable from every other
    miss so the response does not reveal that a foreign file exists there.
    """
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256=None)

    def must_not_read(*args, **kwargs):
        raise AssertionError("an unvalidated file was read off disk")

    monkeypatch.setattr(artwork_module, "_read_asset", must_not_read)

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 404
    assert IMAGE_BYTES not in response.content
    assert "etag" not in response.headers
    # Byte-for-byte the same body an absent row answers with.
    missing = await client.get("/api/items/999999/artwork/poster", headers=auth_headers)
    assert response.content == missing.content


async def test_a_row_with_a_digest_still_serves_its_file(
    client, auth_headers, session, assets_root
):
    """The regression guard for adopted libraries: adoption hashes the file
    already on disk into base_sha256 (adopt/walk.py), so an adopted row is
    exactly "digest set, file present" -- the NULL-digest refusal above must
    not blank it."""
    asset = assets_root / "poster.jpg"
    asset.write_bytes(IMAGE_BYTES)
    item_id = await _item_with_render(session, str(asset), base_sha256="9" * 64)

    response = await client.get(f"/api/items/{item_id}/artwork/poster", headers=auth_headers)

    assert response.status_code == 200
    assert response.content == IMAGE_BYTES


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


class _UnreachablePlexItem:
    """A partial plexapi object whose attribute read fails.

    Not artificial: ``PlexPartialObject.__getattribute__`` issues a blocking
    ``_reload()`` GET when the attribute's value is ``None`` -- exactly the
    "this item has no artwork of that kind" case -- and that GET goes through
    ``requests``, so it raises classes no httpx handler catches.
    """

    def __init__(self, exc):
        self._exc = exc

    @property
    def thumb(self):
        raise self._exc


class _ThreadRecordingPlexItem:
    """Records which thread ``.thumb`` was read on. See _UnreachablePlexItem
    for why that read is not free."""

    def __init__(self, thumb):
        self._thumb = thumb
        self.read_on = []

    @property
    def thumb(self):
        self.read_on.append(threading.current_thread())
        return self._thumb


class _FakePlexClient:
    """Stands in for PlexClient. ``fetch_item``/``fetch_ref`` are async and, in
    the real one, a plain GET run in a thread; ``fetch_artwork`` delegates to
    the real ``plex.artwork.fetch_artwork`` against the wired MockTransport,
    exactly as ``PlexClient.fetch_artwork`` does."""

    def __init__(self, item=None, error=None, http=None, base_url="", headers=None):
        self.item = item
        self.error = error
        self.fetched = []
        self._http = http
        self._base_url = base_url
        self._headers = headers or {}

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        if self.error is not None:
            raise self.error
        return self.item

    async def fetch_ref(self, rating_key):
        try:
            await self.fetch_item(rating_key)
        except PlexNotFound:
            return None
        return ServerItemRef("plex", rating_key, "", "")

    async def fetch_artwork(self, ref, art_kind):
        return await _plex_fetch_artwork(
            self._http, self.item, self._base_url, self._headers, art_kind
        )


@pytest_asyncio.fixture
async def wire_plex(app):
    """Give the app a fake Plex client and an httpx client on a MockTransport.

    Mirrors production wiring: ``app.state.plex`` and ``app.state.http`` are
    both set by the lifespan's run_background branch, which does not run under
    create_app() alone.
    """
    clients = []

    def wire(*, item=None, error=None, handler=None):
        def refuse(request):
            raise AssertionError(f"no Plex request expected, got {request.url}")

        http = AsyncClient(transport=httpx.MockTransport(handler or refuse))
        clients.append(http)
        app.state.plex = _FakePlexClient(
            item=item, error=error, http=http, base_url=PLEX_URL,
            headers={"X-Plex-Token": PLEX_TOKEN},
        )
        app.state.http = http
        return app.state.plex

    yield wire
    for http in clients:
        await http.aclose()


async def _media_item(
    session, rating_key: str = "rk-live", kind: str = "movie", **extra
) -> int:
    """One media item, no render row: the live endpoint reads Plex, not disk.

    ``kind`` decides which art kinds the item can have at all (ART_KINDS_FOR),
    so a title card needs an episode and a background needs a movie or show.
    """
    item = await seed_media_item(
        session, rating_key, library="Movies", kind=kind, title="A", **extra
    )
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
    # "Live" means right now: no validator is sent, so no-store keeps a
    # browser's heuristic caching from answering with a stale image.
    assert response.headers["cache-control"] == "no-store"
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
    one, which is exactly the kind of wrong this page exists to reveal.

    Two items, because the two art kinds belong to two item kinds: a movie has
    a background, an episode has a title card, and asking for either on the
    other is now a 404 (see the ART_KINDS_FOR test below)."""
    seen = []
    wire_plex(
        item=_FakePlexItem(thumb="/thumb/path", art="/art/path"),
        handler=_serves(LIVE_BYTES, seen=seen),
    )
    movie_id = await _media_item(session, rating_key="rk-movie", kind="movie")
    episode_id = await _media_item(session, rating_key="rk-episode", kind="episode")

    await client.get(f"/api/items/{movie_id}/artwork/background/live", headers=auth_headers)
    await client.get(f"/api/items/{episode_id}/artwork/title_card/live", headers=auth_headers)

    assert [request.url.path for request in seen] == ["/art/path", "/thumb/path"]


@pytest.mark.parametrize(
    ("kind", "art_kind"),
    [("episode", "background"), ("movie", "title_card"), ("season", "poster")],
)
async def test_an_art_kind_the_item_cannot_have_is_404_without_asking_plex(
    client, auth_headers, session, wire_plex, kind, art_kind
):
    """The two endpoints are read side by side and have to agree on what
    exists. plexapi exposes ``.art`` on an Episode, so this would otherwise
    answer 200 with the *show's* backdrop while the base endpoint 404s the
    same request for want of a render row. ART_KINDS_FOR (render/pipeline.py)
    is the mapping both ends of the pipeline already use."""
    plex = wire_plex(item=_FakePlexItem(thumb="/thumb/path", art="/art/path"))
    item_id = await _media_item(session, kind=kind)

    response = await client.get(
        f"/api/items/{item_id}/artwork/{art_kind}/live", headers=auth_headers
    )

    assert response.status_code == 404
    assert plex.fetched == []


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


async def test_an_item_with_no_plex_ref_is_404_without_asking_plex(
    client, auth_headers, session, wire_plex
):
    """A row this service has never resolved against Plex has no native id to
    fetch_ref with at all -- the same 404 as an item Plex has since lost, and
    for the same reason, but reached without a request: there is nothing to
    ask Plex about."""
    plex = wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session, plex_ref=False)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404
    assert plex.fetched == []


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


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("connection reset by peer"),
        PlexBadRequest("(500) internal_server_error"),
    ],
)
async def test_a_plex_that_drops_while_the_artwork_field_is_read_is_503(
    client, auth_headers, session, wire_plex, error
):
    """The second leg is not httpx-only.

    fetch_artwork begins by reading ``.thumb``/``.art`` off the plexapi object,
    and ``PlexPartialObject.__getattribute__`` answers a ``None``-valued
    attribute with a blocking ``_reload()`` GET -- through ``requests``, and
    precisely on the path that is meant to end in "no artwork of that kind".
    A Plex that dies between resolving the item and reading the field raises a
    class no httpx handler catches, and the endpoint's own contract forbids the
    500 that produced.
    """
    # No handler: the failure must happen before any image request goes out.
    wire_plex(item=_UnreachablePlexItem(error))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 503
    assert type(error).__name__ in response.json()["detail"]


async def test_the_plex_attribute_read_never_runs_on_the_event_loop(
    client, auth_headers, session, wire_plex
):
    """Same reason as the asset read: that attribute can become a blocking
    HTTP GET to Plex, and this loop also carries the workers, the scheduler
    and the liveness probe."""
    item = _ThreadRecordingPlexItem("/thumb/path")
    wire_plex(item=item, handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 200
    assert item.read_on, "the artwork field was never read"
    assert item.read_on[0] is not threading.current_thread(), (
        f"Plex's artwork field was read on the event loop thread ({item.read_on[0]!r})"
    )


async def test_an_empty_body_from_plex_is_404_not_a_zero_byte_200(
    client, auth_headers, session, wire_plex
):
    """The UI draws a zero-byte 200 as a broken image. "Nothing there" is
    already a status this endpoint has, and it is the honest one."""
    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(b""))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 404


async def test_the_live_bytes_carry_nosniff(client, auth_headers, session, wire_plex):
    """These bytes are remote-controlled and served from our own origin under
    a session, so the content-type whitelist must not be sniffable around."""
    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_live_artwork_is_503_when_this_instance_has_no_plex_connection(
    client, auth_headers, session, caplog
):
    """create_app leaves app.state.plex/http None; the lifespan's
    run_background branch fills them in. Without them there is nothing to ask,
    which is a 503 rather than an AttributeError 500.

    And it has to be logged. This is the only 503 here whose cause is local
    wiring rather than Plex, while its body reads exactly like the other two --
    an operator seeing "not connected to Plex" with nothing in the log goes and
    looks at their Plex server.
    """
    item_id = await _media_item(session)

    with caplog.at_level("WARNING"):
        response = await client.get(
            f"/api/items/{item_id}/artwork/poster/live", headers=auth_headers
        )

    assert response.status_code == 503
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("plex and http" in message for message in warnings), warnings


async def test_live_artwork_requires_a_session(client, session, wire_plex):
    wire_plex(item=_FakePlexItem(thumb="/thumb/path"), handler=_serves(LIVE_BYTES))
    item_id = await _media_item(session)

    response = await client.get(f"/api/items/{item_id}/artwork/poster/live")

    assert response.status_code == 401
    assert LIVE_BYTES not in response.content
