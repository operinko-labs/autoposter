"""Testing mode: on-demand sample renders.

``POST /api/testing/sample`` renders one styled artifact of the requested kind
against a generated solid canvas and returns it inline -- the styled JPEG bytes
when the title fits, or a 200 JSON body reporting the truncation outcome when it
does not. It writes nothing to any mount and touches no database row.

The split here follows the phase's convention: the auth and validation paths
need no ImageMagick and stay unmarked, while everything that actually composites
carries ``@pytest.mark.imagemagick`` and runs only where a ``magick`` exists
(the dev container and CI's dedicated step -- see ``tests/conftest.py``).

The compositing tests point ``fonts_root`` and ``overlays_root`` at the golden
fixtures, because the font (Comfortaa) and the three overlay PNGs the example
config names live there rather than in the repo's empty ``assets`` tree.
"""
import io
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
GOLDEN = Path(__file__).parent / "fixtures" / "golden"
PASSWORD = "correct horse battery staple"

SAMPLE_KINDS = ["poster", "season_poster", "background", "title_card"]


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest_asyncio.fixture
async def app(session_factory):
    # fonts_root/overlays_root point at the golden fixtures: the example config
    # names Comfortaa-Medium.ttf and the bottom-up-fade overlays, and those live
    # there, not in the repo's empty assets/ tree. Without this the compositor
    # would be handed a font and overlay path that does not exist.
    config = load_config(EXAMPLE).model_copy(
        update={"fonts_root": GOLDEN, "overlays_root": GOLDEN}
    )
    app = create_app(config, session_factory, _secrets())
    yield app


@pytest_asyncio.fixture
async def client(app):
    asgi = ASGITransport(app=app)
    async with AsyncClient(transport=asgi, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _sample(client, headers, art_kind: str = "poster", length: str = "short"):
    return client.post(
        "/api/testing/sample",
        json={"art_kind": art_kind, "length": length},
        headers=headers,
    )


# --- auth and validation (no ImageMagick) -----------------------------------


async def test_requires_a_session(client):
    """Covered structurally by the auth floor test too, but pinned here so a
    change to this endpoint's dependency is caught in its own file."""
    response = await client.post(
        "/api/testing/sample", json={"art_kind": "poster", "length": "short"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize("art_kind", ["logo", "bogus", "collection", ""])
async def test_an_art_kind_with_no_sample_is_422(client, auth_headers, art_kind):
    """``logo`` among them: a logo is composited over a poster and has no canvas
    of its own, so it is not one of the kinds a sample can be rendered for."""
    response = await _sample(client, auth_headers, art_kind=art_kind)
    assert response.status_code == 422


@pytest.mark.parametrize("length", ["tiny", "huge", ""])
async def test_an_unknown_length_is_422(client, auth_headers, length):
    response = await _sample(client, auth_headers, length=length)
    assert response.status_code == 422


async def test_a_missing_field_is_422(client, auth_headers):
    response = await client.post(
        "/api/testing/sample", json={"art_kind": "poster"}, headers=auth_headers
    )
    assert response.status_code == 422


def test_a_season_poster_sample_carries_a_sample_show_title():
    """The config editor's sample sheet is where an operator SEES the row 78
    layout before committing to it, so the synthetic item has to carry the one
    field the block reads. Without this the sample would render the gate as a
    no-op and the operator would conclude the feature does not work.

    Asserted through ``title_text_for`` rather than through the endpoint,
    because the endpoint composites and this file's compositing tests carry
    the imagemagick marker that CI's main run deselects.
    """
    from autoposter.api.testing import SAMPLE_SHOW_TITLE, SAMPLE_TITLES, _sample_item
    from autoposter.render.pipeline import title_text_for

    config = load_config(EXAMPLE)
    config.artwork.season_poster.show_title.add_text = True

    primary, secondary = title_text_for("season_poster", _sample_item("short"), config)

    assert primary == SAMPLE_TITLES["short"]
    assert secondary == SAMPLE_SHOW_TITLE
    assert SAMPLE_SHOW_TITLE, "a sample show title has to be a non-empty string"

    config.artwork.season_poster.show_title.add_text = False
    assert title_text_for("season_poster", _sample_item("short"), config)[1] is None


# --- real compositing (ImageMagick) -----------------------------------------


@pytest.mark.imagemagick
@pytest.mark.parametrize("art_kind", SAMPLE_KINDS)
async def test_a_sample_renders_to_inline_jpeg_bytes(client, auth_headers, art_kind):
    """Every kind renders at the short length: the title fits, so the response
    is the styled JPEG itself, with the no-store/nosniff headers a transient,
    never-cached own-endpoint image should carry."""
    response = await _sample(client, auth_headers, art_kind=art_kind, length="short")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.content
    assert body[:3] == b"\xff\xd8\xff"
    with Image.open(io.BytesIO(body)) as image:
        assert image.format == "JPEG"


@pytest.mark.imagemagick
async def test_an_overflowing_title_is_reported_as_truncated(client, auth_headers):
    """The long title cannot fit at the minimum point size, so the pipeline
    produces no artifact -- and testing mode returns that outcome as JSON rather
    than inventing a poster the running system never would."""
    response = await _sample(client, auth_headers, art_kind="poster", length="long")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "truncated": True, "art_kind": "poster", "length": "long",
    }


@pytest.mark.imagemagick
async def test_the_sample_reflects_a_live_config_change(client, auth_headers, app):
    """The roadmap's acceptance: a config edit is visible in the very next
    sample. The holder is swapped -- and ONLY the holder, not
    ``app.state.config`` -- so this reds against an endpoint that reads the
    stale per-request config instead of ``config_holder.current``."""
    first = await _sample(client, auth_headers, art_kind="poster", length="short")
    assert first.status_code == 200
    before = first.content

    config = app.state.config_holder.current
    # A text setting, changed through nested model_copy: white -> red font.
    new_text = config.artwork.poster.text.model_copy(update={"font_color": "red"})
    new_poster = config.artwork.poster.model_copy(update={"text": new_text})
    new_artwork = config.artwork.model_copy(update={"poster": new_poster})
    app.state.config_holder.swap(config.model_copy(update={"artwork": new_artwork}))

    second = await _sample(client, auth_headers, art_kind="poster", length="short")
    assert second.status_code == 200
    after = second.content

    assert before != after, (
        "the sample did not change after a text-colour edit -- the endpoint is "
        "not reading the live config holder"
    )
