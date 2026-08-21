"""GET /api/items/{item_id}/artwork/{art_kind}: the base image bytes.

This is the first endpoint in the project that reads a file for a caller, and
the path it reads comes out of the database. So most of what is pinned here is
not "does it serve the image" but "what does it refuse to serve": a row
pointing outside the asset tree, a symlink inside the tree aimed out of it, and
a URL segment that looks like a path.
"""
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from test_spa_serving import _raw_asgi_get

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
# Recognisable bytes rather than a real JPEG: nothing in the endpoint decodes
# the image, and a fixture file would only hide which bytes came back.
IMAGE_BYTES = b"\xff\xd8\xff\xe0 not really a jpeg, but these exact bytes"
SECRET = "do not serve me"


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest_asyncio.fixture
async def app(session_factory, assets_root):
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
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
