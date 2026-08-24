"""POST /api/artwork-modes/{backup,restore,revert,reset} -- the trigger endpoints.

Built like test_api_artwork.py: a real ``create_app`` whose ``app.state.plex``
and ``app.state.http`` are wired by hand (the lifespan's run_background branch
does not run under create_app alone). Fake Plex throughout; a tmp
``plex_backup_root``. The load-bearing assertion here is the worker-pause fence:
an applied restore must hold the pool paused while it uploads, proven by a
FakeItem that records ``worker_pause.is_paused`` at the moment of upload.
"""
import io
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem, Render
from autoposter.plex.exif import PROVENANCE_TAG, format_provenance

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token-for-this-test"
POSTER_BYTES = b"\xff\xd8 poster bytes from plex"
AGENT_KEY = "metadata://posters/tmdb_12345"


class FakePoster:
    """One entry of a plexapi ``posters()`` listing."""

    def __init__(self, rating_key):
        self.ratingKey = rating_key  # noqa: N815 - plexapi name


class FakeItem:
    """Serves ``.thumb``/``.art`` to backup's fetch and the provenance probe,
    records restore's and revert's uploads, and carries reset's unlock/select
    spies. ``refresh`` exists to prove nothing calls it; ``pause`` lets a write
    record whether the worker fence was raised at that instant."""

    def __init__(self, thumb=None, art=None, pause=None, posters=(AGENT_KEY,)):
        self.thumb = thumb
        self.art = art
        self._pause = pause
        self._posters = [FakePoster(key) for key in posters]
        self.uploaded = []
        self.locked = []
        self.unlocked = []
        self.selected = []
        self.paused_during_write = []
        self.refreshed = False

    def refresh(self):
        self.refreshed = True

    def _note_pause(self):
        if self._pause is not None:
            self.paused_during_write.append(self._pause.is_paused)

    def _record(self, field, filepath):
        self._note_pause()
        with open(filepath, "rb") as handle:
            self.uploaded.append((field, handle.read()))

    def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
        self._record("poster", filepath)

    def uploadArt(self, filepath=None):  # noqa: N802 - plexapi name
        self._record("art", filepath)

    def lockPoster(self):  # noqa: N802 - plexapi name
        self.locked.append("poster")

    def lockArt(self):  # noqa: N802 - plexapi name
        self.locked.append("art")

    def unlockPoster(self):  # noqa: N802 - plexapi name
        self._note_pause()
        self.unlocked.append("poster")

    def posters(self):
        return list(self._posters)

    def setPoster(self, poster):  # noqa: N802 - plexapi name
        self.selected.append(poster.ratingKey)


class FakePlexClient:
    def __init__(self, items):
        self._items = items
        self.fetched = []

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return self._items[rating_key]


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token=PLEX_TOKEN, tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def backup_root(tmp_path) -> Path:
    return tmp_path / "plexbackup"


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


@pytest_asyncio.fixture
async def app(session_factory, assets_root, backup_root):
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
    config.plex.url = PLEX_URL
    config.artwork_modes.plex_backup_root = backup_root
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


def _serves_poster():
    def handler(request):
        if request.url.path == "/thumb":
            return httpx.Response(200, content=POSTER_BYTES, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    return handler


@pytest_asyncio.fixture
async def wire(app):
    """Wire app.state.plex/http the way the lifespan would, and return a helper
    that installs the fake items and a MockTransport handler."""
    clients = []

    def install(items, handler=None):
        def refuse(request):
            raise AssertionError(f"no Plex request expected, got {request.url}")

        http = AsyncClient(transport=httpx.MockTransport(handler or refuse))
        clients.append(http)
        app.state.plex = FakePlexClient(items)
        app.state.http = http
        return app.state.plex

    yield install
    for http in clients:
        await http.aclose()


def _seed_backup(backup_root, library, root_folder, name, data):
    path = backup_root / library / root_folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    root_folder="A (1999)"):
    item = MediaItem(
        rating_key=rating_key, library=library, kind=kind, title="A",
        root_folder=root_folder,
    )
    session.add(item)
    await session.commit()
    return item


# --- auth + wiring -----------------------------------------------------------


async def test_backup_requires_a_session(client):
    assert (await client.post("/api/artwork-modes/backup")).status_code == 401


async def test_restore_requires_a_session(client):
    assert (await client.post("/api/artwork-modes/restore", json={})).status_code == 401


async def test_backup_503_when_plex_unset(client, auth_headers):
    # create_app leaves app.state.plex/http None; the lifespan sets them.
    response = await client.post("/api/artwork-modes/backup", headers=auth_headers)
    assert response.status_code == 503


async def test_restore_503_when_plex_unset(client, auth_headers):
    response = await client.post(
        "/api/artwork-modes/restore", headers=auth_headers, json={}
    )
    assert response.status_code == 503


# --- backup ------------------------------------------------------------------


async def test_backup_endpoint_backs_up_the_library(
    client, auth_headers, session, wire, backup_root
):
    await _add_item(session, rating_key="rk1")
    wire({"rk1": FakeItem(thumb="/thumb")}, handler=_serves_poster())

    response = await client.post("/api/artwork-modes/backup", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "backup"
    assert body["items"] == 1 and body["written"] == 1
    assert (backup_root / "Movies" / "A (1999)" / "poster.jpg").read_bytes() == POSTER_BYTES


# --- restore -----------------------------------------------------------------


async def test_restore_dry_run_is_the_default_and_pushes_nothing(
    client, auth_headers, session, wire, backup_root
):
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"poster")
    item = FakeItem()
    wire({"rk1": item})

    # apply omitted -> falls back to config default (restore_apply=False).
    response = await client.post(
        "/api/artwork-modes/restore", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["items_with_backup"] == 1 and body["files"] == 1
    assert item.uploaded == []


async def test_restore_apply_pushes(client, auth_headers, session, wire, backup_root):
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"the-poster")
    item = FakeItem()
    wire({"rk1": item})

    response = await client.post(
        "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["pushed"] == 1
    assert ("poster", b"the-poster") in item.uploaded


async def test_apply_restore_pauses_the_pool_while_uploading(
    client, auth_headers, session, wire, backup_root, app
):
    """The pause mutation-proof: an applied restore holds the worker fence while
    it uploads, so the pool cannot claim and race it on the same Plex item. Drop
    the ``worker_pause.paused()`` wrap in the restore endpoint and the recorded
    value flips to False, reddening this test.

    The fence is also released afterwards -- ``is_paused`` is False once the
    request returns -- so a later pass is not left idled forever."""
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"p")
    item = FakeItem(pause=app.state.worker_pause)
    wire({"rk1": item})

    assert app.state.worker_pause.is_paused is False
    response = await client.post(
        "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    # Recorded at the instant of upload: the fence was up.
    assert item.paused_during_write == [True]
    # And released in the finally once the push finished.
    assert app.state.worker_pause.is_paused is False


async def test_dry_run_restore_does_not_pause_the_pool(
    client, auth_headers, session, wire, backup_root, app
):
    """A dry run touches nothing on Plex, so it must not idle the live pipeline:
    no upload happens and the fence is never raised."""
    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"p")
    item = FakeItem(pause=app.state.worker_pause)
    wire({"rk1": item})

    await client.post("/api/artwork-modes/restore", headers=auth_headers, json={})

    assert item.paused_during_write == []  # never uploaded, never paused
    assert app.state.worker_pause.is_paused is False


# --- revert ------------------------------------------------------------------


async def _add_render(session, item, art_kind, path):
    render = Render(
        item_id=item.id, art_kind=art_kind, asset_path=str(path),
        base_sha256="digest", status="rendered",
    )
    session.add(render)
    await session.commit()
    return render


def _seed_base(assets_root, name, data) -> Path:
    path = assets_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


async def test_revert_requires_a_session(client):
    assert (await client.post("/api/artwork-modes/revert", json={})).status_code == 401


async def test_revert_503_when_plex_unset(client, auth_headers):
    response = await client.post("/api/artwork-modes/revert", headers=auth_headers, json={})
    assert response.status_code == 503


async def test_revert_dry_run_is_the_default_and_pushes_nothing(
    client, auth_headers, session, wire, assets_root
):
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(session, item_row, "poster", _seed_base(assets_root, "a.jpg", b"base"))
    item = FakeItem()
    wire({"rk1": item})

    # apply omitted -> falls back to config default (revert_apply=False).
    response = await client.post("/api/artwork-modes/revert", headers=auth_headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "revert" and body["dry_run"] is True
    assert body["items_with_base"] == 1 and body["files"] == 1
    assert item.uploaded == []


async def test_apply_revert_pushes_the_base_with_the_pool_paused(
    client, auth_headers, session, wire, assets_root, app
):
    """The pause mutation-proof for revert: it uploads to the same Plex items the
    live pipeline does, so the fence must be up at the instant of the push. Drop
    the ``apply`` branch in ``_run_plex_writing_mode`` and the recorded value
    flips to False."""
    item_row = await _add_item(session, rating_key="rk1")
    await _add_render(
        session, item_row, "poster", _seed_base(assets_root, "a.jpg", b"the-clean-base")
    )
    item = FakeItem(pause=app.state.worker_pause)
    wire({"rk1": item})

    response = await client.post(
        "/api/artwork-modes/revert", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["pushed"] == 1
    assert ("poster", b"the-clean-base") in item.uploaded
    assert item.paused_during_write == [True]
    assert app.state.worker_pause.is_paused is False


# --- reset -------------------------------------------------------------------


def _stamped_jpeg(fingerprint: str) -> bytes:
    """A JPEG carrying our provenance in its header, where JPEGs put it."""
    exif = Image.Exif()
    exif[PROVENANCE_TAG] = format_provenance(fingerprint)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _serves_ranged(data):
    """Serve ``data`` for any path, honouring Range the way Plex does."""

    def handler(request):
        header = request.headers.get("Range")
        if header is None:
            return httpx.Response(200, content=data)
        spec = header.removeprefix("bytes=")
        if spec.startswith("-"):
            return httpx.Response(206, content=data[-int(spec[1:]):])
        start, end = spec.split("-")
        return httpx.Response(206, content=data[int(start):int(end) + 1])

    return handler


async def test_reset_requires_a_session(client):
    assert (await client.post("/api/artwork-modes/reset", json={})).status_code == 401


async def test_reset_503_when_plex_unset(client, auth_headers):
    response = await client.post("/api/artwork-modes/reset", headers=auth_headers, json={})
    assert response.status_code == 503


async def test_reset_dry_run_is_the_default_and_changes_nothing(
    client, auth_headers, session, wire
):
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/thumb")
    wire({"rk1": item}, handler=_serves_ranged(_stamped_jpeg("fp-abc")))

    # apply omitted -> falls back to config default (reset_apply=False).
    response = await client.post("/api/artwork-modes/reset", headers=auth_headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "reset" and body["dry_run"] is True
    assert body["items_with_our_art"] == 1
    assert item.unlocked == [] and item.selected == []
    # The UI has to be able to tell the operator the upload is not deleted.
    assert "orphan" in body["note"]


async def test_apply_reset_selects_the_agent_default_with_the_pool_paused(
    client, auth_headers, session, wire, app
):
    """The pause mutation-proof for reset: the unlock/select is a Plex write, so
    the fence must be up while it happens, and released afterwards."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/thumb", pause=app.state.worker_pause)
    wire({"rk1": item}, handler=_serves_ranged(_stamped_jpeg("fp-abc")))

    response = await client.post(
        "/api/artwork-modes/reset", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["reset"] == 1 and body["failed"] == 0
    assert item.unlocked == ["poster"] and item.selected == [AGENT_KEY]
    assert item.paused_during_write == [True]
    assert app.state.worker_pause.is_paused is False
    assert item.refreshed is False
