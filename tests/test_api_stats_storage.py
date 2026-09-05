"""``GET /api/stats/storage`` through the real app (roadmap row 52).

Through ``create_app`` + ``ASGITransport``, never through the handler or
``storage_snapshot`` alone (memory: gated features need one test through the
real entry point) -- what is worth pinning is the WIRED surface: that the
route is mounted, that it wears row 51's ``api_key_or_session`` and is named
in ``ALLOWLIST`` (both halves, or the dependency's own path check refuses it),
that it refuses an unkeyed caller, and that it answers while the filesystem is
unusable.

The arithmetic itself is pinned in ``tests/test_storage_stats.py``; this file
seeds the smallest rows that make the shape legible and does not re-test the
sums.
"""

import os
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from autoposter.api import auth as auth_module
from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
# Row 51's one fixed fake, reused rather than invented again: a second literal
# would be a second thing to grep for when proving no real key is in the tree.
FAKE_KEY = "test-api-key-0123456789abcdef"
KEYED = {"X-API-Key": FAKE_KEY}
REFUSED = {"detail": "not authenticated"}
FAKE_ASSET = "/nowhere/fake-asset.jpg"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
        api_key=FAKE_KEY,
    )


@pytest_asyncio.fixture
async def app(session_factory):
    return create_app(load_config(EXAMPLE), session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _seed(session):
    item = MediaItem(rating_key="1", library="Movies", kind="movie", title="Dune")
    session.add(item)
    await session.flush()
    session.add_all([
        Render(item_id=item.id, art_kind="poster", asset_path=FAKE_ASSET,
               status="rendered", size_bytes=100),
        Render(item_id=item.id, art_kind="background", asset_path=FAKE_ASSET,
               status="rendered", size_bytes=None),
    ])
    await session.commit()


async def test_the_endpoint_answers_the_documented_shape(client, session_headers, session):
    await _seed(session)

    response = await client.get("/api/stats/storage", headers=session_headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"totals", "by_library", "generated_at"}
    assert body["totals"] == {
        "items": 1, "assets": 2, "bytes": 100, "unknown_size": 1
    }
    movies = body["by_library"]["Movies"]
    assert set(movies) == {"assets", "bytes", "unknown_size", "by_art_kind"}
    assert set(movies["by_art_kind"]) == {
        "poster", "season_poster", "background", "title_card"
    }
    assert isinstance(body["generated_at"], str)
    # Row 213: numbers, a timestamp, library names and art-kind tokens. No
    # path -- not the row's asset_path and not any root.
    assert FAKE_ASSET not in response.text
    assert "nowhere" not in response.text


async def test_a_key_reads_the_storage_stats_without_a_session(client, session):
    """Row 51's key, on row 52's route -- the whole point of the dependency
    on row 51. Both halves must agree: the handler takes
    ``api_key_or_session`` AND the path is in ``ALLOWLIST``, or the dependency
    refuses its own route."""
    await _seed(session)
    assert "/api/stats/storage" in auth_module.ALLOWLIST

    response = await client.get("/api/stats/storage", headers=KEYED)

    assert response.status_code == 200
    assert response.json()["totals"]["assets"] == 2


async def test_the_endpoint_refuses_a_request_with_no_credential(client):
    """The same fixed sentence every other refusal in this API uses. No 403:
    there is no authenticated-but-forbidden principal to name."""
    response = await client.get("/api/stats/storage")

    assert response.status_code == 401
    assert response.json() == REFUSED


async def test_the_endpoint_never_touches_the_filesystem(
    client, session_headers, session, monkeypatch
):
    """The pin behind the whole design (facts C2's endpoint-never-walks).
    ``assets_root`` is an NFS mount; a widget polling every sixty seconds must
    never be able to make a request wait on it. With ``os.walk`` and
    ``Path.stat`` both raising, the answer must be unchanged."""
    await _seed(session)

    def _boom(*args, **kwargs):
        raise AssertionError("the storage endpoint walked the filesystem")

    monkeypatch.setattr(os, "walk", _boom)
    monkeypatch.setattr(os, "stat", _boom)
    monkeypatch.setattr(Path, "stat", _boom)

    response = await client.get("/api/stats/storage", headers=session_headers)

    assert response.status_code == 200
    assert response.json()["totals"]["assets"] == 2


def test_the_readme_documents_the_widget_with_a_header_and_never_a_query_string():
    """Row 52's operator-facing half, checked rather than assumed.

    The query-string form is the ONE thing not copied from Posterizarr, whose
    docs put ``?api_key=`` in every example -- a key there lands in an ingress
    access log and a browser history. This asserts the README's storage-stats
    recipe carries the key in a ``headers:`` block and uses Homepage's
    ``bytes`` format on the byte fields, and that no recipe in the file puts a
    credential in a URL.
    """
    readme = (Path(__file__).parent.parent / "deploy" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "#### Homepage `customapi` recipe: storage" in readme
    assert "/api/stats/storage" in readme
    assert 'X-API-Key: "{{HOMEPAGE_VAR_AUTOPOSTER_API_KEY}}"' in readme
    assert "format: bytes" in readme
    # Scoped to the recipes' `url:` lines, not the whole file: row 51's own
    # prose explains WHY the query string is never read and QUOTES
    # ``?api_key=`` to do it, so a whole-file ban would fail on this project's
    # own documentation of the rule. What must never appear is a credential in
    # a widget's URL -- every customapi url here is bare.
    for line in readme.splitlines():
        if line.strip().startswith("url:"):
            assert "?" not in line, f"a customapi recipe put a credential in the URL: {line}"
    assert "?secret=" not in readme
    # The backfill has to be documented as a scheduled pass, or an operator
    # reading zero bytes on a fresh deployment has no way to know why.
    assert "`asset_stats_days` (default `7`)" in readme
    assert "`asset_stats_batch_size` (default `500`)" in readme
