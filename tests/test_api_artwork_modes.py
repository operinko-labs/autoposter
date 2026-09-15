"""POST /api/artwork-modes/{backup,restore,revert,reset,logo,logo-revert} -- the
trigger endpoints.

Built like test_api_artwork.py: a real ``create_app`` whose ``app.state.plex``
and ``app.state.http`` are wired by hand (the lifespan's run_background branch
does not run under create_app alone). Fake Plex throughout; a tmp
``plex_backup_root``. The load-bearing assertion here is the worker-pause fence:
an applied restore must hold the pool paused while it uploads, proven by a
FakeItem that records ``worker_pause.is_paused`` at the moment of upload.
"""
import asyncio
import io
import threading
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from conftest import decodable_png, seed_media_item

from autoposter.api.auth import hash_password
from autoposter.api.system import RESTART_IN_PROGRESS
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import MediaItem, Render
from autoposter.plex.artwork import artwork_provenance as _plex_artwork_provenance
from autoposter.plex.artwork import clear_logo as _plex_clear_logo
from autoposter.plex.artwork import fetch_artwork as _plex_fetch_artwork
from autoposter.plex.artwork import has_clearlogo as _plex_has_clearlogo
from autoposter.plex.artwork import (
    reset_artwork_to_agent_default as _plex_reset_artwork_to_agent_default,
)
from autoposter.plex.artwork import upload_artwork as _plex_upload_artwork
from autoposter.plex.artwork import upload_logo as _plex_upload_logo
from autoposter.plex.exif import PROVENANCE_TAG, format_provenance
from autoposter.providers.base import LOGO, ArtCandidate
from autoposter.queue.jobs import enqueue
from autoposter.queue.worker import run_worker
from autoposter.servers.base import (
    CAP_ARTWORK_PROVENANCE, CAP_FIELD_LOCKS, CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD,
    CAP_LOGO_UPLOAD_KEY, CAP_RESET_TO_AGENT_DEFAULT, CAP_TITLE_CARD_URL, ServerItemRef,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"
PLEX_URL = "http://plex.local"
PLEX_TOKEN = "plex-token-for-this-test"
POSTER_BYTES = b"\xff\xd8 poster bytes from plex"
AGENT_KEY = "metadata://posters/tmdb_12345"
AGENT_ART_KEY = "metadata://art/tmdb_12345"
LOGO_URL = "https://provider.example/logo.png"
# A genuinely decodable PNG, not a placeholder: the logo updater now decodes
# every body it keeps (``render/artwork_fetch._validate_image``), so a
# placeholder would send the pool-paused pin down the refusal path instead of
# the behaviour it is about.
LOGO_BYTES = decodable_png()
# What Plex keys our clearlogo upload under, and therefore the revert marker.
OUR_LOGO_KEY = "upload://clearLogos/ours-7f3c9a"


class FakePoster:
    """One entry of a plexapi ``posters()`` listing."""

    def __init__(self, rating_key):
        self.ratingKey = rating_key  # noqa: N815 - plexapi name


class FakeItem:
    """Serves ``.thumb``/``.art`` to backup's fetch and the provenance probe,
    records restore's and revert's uploads, and carries reset's unlock/select
    spies. ``refresh`` exists to prove nothing calls it; ``pause`` lets a write
    record whether the worker fence was raised at that instant."""

    def __init__(self, thumb=None, art=None, pause=None, posters=(AGENT_KEY,),
                 arts=(AGENT_ART_KEY,), logo=None, logos=()):
        self.thumb = thumb
        self.art = art
        self.logo = logo
        self._pause = pause
        self._posters = [FakePoster(key) for key in posters]
        self._arts = [FakePoster(key) for key in arts]
        self._logos = [FakeLogo(key, selected) for key, selected in logos]
        self.deleted_logos = 0
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

    def unlockArt(self):  # noqa: N802 - plexapi name
        self._note_pause()
        self.unlocked.append("art")

    def arts(self):
        return list(self._arts)

    def setArt(self, art):  # noqa: N802 - plexapi name
        self.selected.append(art.ratingKey)

    def uploadLogo(self, url=None, filepath=None):  # noqa: N802 - plexapi name
        self._record("logo", filepath)
        self.logo = "/library/metadata/1/clearLogo/1"
        for entry in self._logos:
            entry.selected = False
        self._logos.append(FakeLogo(OUR_LOGO_KEY, selected=True))

    def lockLogo(self):  # noqa: N802 - plexapi name
        self.locked.append("logo")

    def logos(self):
        return list(self._logos)

    def unlockLogo(self):  # noqa: N802 - plexapi name
        self._note_pause()
        self.unlocked.append("logo")

    def deleteLogo(self):  # noqa: N802 - plexapi name
        self.deleted_logos += 1
        self.logo = None


class FakeLogo:
    """One entry of a plexapi ``logos()`` listing: the rating key that tells an
    upload from an agent image, and which of them Plex has selected."""

    def __init__(self, rating_key, selected=False):
        self.ratingKey = rating_key  # noqa: N815 - plexapi name
        self.selected = selected


class FakeLogoProvider:
    """One rung of the real ladder, answering the LOGO request with one PNG."""

    name = "Fake"

    async def fetch(self, request):
        if request.art_kind != LOGO:
            return []
        return [ArtCandidate(
            provider=self.name, url=LOGO_URL, language="en",
            width=800, height=310, score=8.0,
        )]


class FakePlexClient:
    capabilities = frozenset({
        CAP_LOCK_ARTWORK, CAP_LOGO_UPLOAD, CAP_LOGO_UPLOAD_KEY, CAP_FIELD_LOCKS,
        CAP_ARTWORK_PROVENANCE, CAP_TITLE_CARD_URL, CAP_RESET_TO_AGENT_DEFAULT,
    })
    name = "plex"

    def __init__(self, items, http=None):
        self._items = items
        self.fetched = []
        self._http = http
        self._base_url = PLEX_URL
        self._headers = {"X-Plex-Token": PLEX_TOKEN}

    async def fetch_item(self, rating_key):
        self.fetched.append(rating_key)
        return self._items[rating_key]

    async def fetch_ref(self, rating_key):
        if rating_key not in self._items:
            return None
        self.fetched.append(rating_key)
        return ServerItemRef("plex", rating_key, "", "")

    async def upload_artwork(self, ref, data, art_kind, lock):
        item = self._items[ref.native_id]
        await asyncio.to_thread(_plex_upload_artwork, item, data, art_kind, lock)

    async def fetch_artwork(self, ref, art_kind):
        item = self._items[ref.native_id]
        return await _plex_fetch_artwork(
            self._http, item, self._base_url, self._headers, art_kind
        )

    async def artwork_provenance(self, ref, art_kind):
        item = self._items[ref.native_id]
        return await _plex_artwork_provenance(
            self._http, item, self._base_url, self._headers, art_kind
        )

    async def reset_artwork_to_agent_default(self, ref, art_kind):
        item = self._items[ref.native_id]
        return await asyncio.to_thread(_plex_reset_artwork_to_agent_default, item, art_kind)

    async def has_clearlogo(self, ref):
        item = self._items[ref.native_id]
        return await _plex_has_clearlogo(item)

    async def upload_logo(self, ref, data, suffix=".png"):
        item = self._items[ref.native_id]
        return await asyncio.to_thread(_plex_upload_logo, item, data, suffix)

    async def clear_logo(self, ref):
        item = self._items[ref.native_id]
        await asyncio.to_thread(_plex_clear_logo, item)


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token=PLEX_TOKEN, tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def backup_root(tmp_path) -> Path:
    # Pre-created, because in a deployment it is a MOUNT: the backup mode
    # refuses rather than mkdir-ing a tree into the container's filesystem.
    root = tmp_path / "plexbackup"
    root.mkdir()
    return root


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

    def install(items, handler=None, providers=None):
        def refuse(request):
            raise AssertionError(f"no Plex request expected, got {request.url}")

        http = AsyncClient(transport=httpx.MockTransport(handler or refuse))
        clients.append(http)
        app.state.plex = FakePlexClient(items, http=http)
        app.state.http = http
        # create_app leaves the ladder empty; the lifespan builds it. Only the
        # logo updater reads it.
        app.state.providers = providers if providers is not None else []
        return app.state.plex

    yield install
    for http in clients:
        await http.aclose()


def _seed_backup(backup_root, library, root_folder, name, data):
    path = backup_root / library / root_folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _add_item(session, *, rating_key, kind="movie", library="Movies",
                    root_folder="A (1999)", logo_upload_key=None):
    return await seed_media_item(
        session, rating_key, library=library, kind=kind, root_folder=root_folder,
        tmdb_id=101, logo_upload_key=logo_upload_key,
    )


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


async def test_apply_waits_for_an_in_flight_job_before_it_writes(
    client, auth_headers, session, session_factory, wire, backup_root, app
):
    """The fence has to DRAIN, not merely rise. Raising it stops the pool
    *claiming*, but a worker already inside a handler is still writing to the
    same Plex items -- which is precisely the race the fence exists to prevent.
    So the trigger awaits ``WorkerPause.drain()`` before the mode's first
    write.

    A real worker is running here with a real job in flight when the trigger
    arrives. Mutation proof: delete the ``await pause.drain()`` in
    ``_run_plex_writing_mode`` and the upload lands while the job is still
    running -- the ``order == ["job start"]`` assertion below reds.
    """
    order = []
    entered = asyncio.Event()
    finish_job = asyncio.Event()

    async def slow_handler(session_, job):
        order.append("job start")
        entered.set()
        await finish_job.wait()
        order.append("job end")

    class RecordingItem(FakeItem):
        def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
            order.append("upload")
            super().uploadPoster(filepath=filepath)

    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"p")
    wire({"rk1": RecordingItem()})

    async with session_factory() as setup_session:
        await enqueue(setup_session, "slow", {}, dedupe_key="slow-1")

    stop_event = asyncio.Event()
    worker = asyncio.create_task(
        run_worker(
            "worker-1", session_factory, {"slow": slow_handler}, stop_event,
            pause=app.state.worker_pause,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)

        trigger = asyncio.create_task(
            client.post(
                "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
            )
        )
        # Long enough for the whole (tiny) mode to have run if it were not
        # waiting: it is a couple of queries, a stat and one upload.
        await asyncio.sleep(0.3)
        assert order == ["job start"]  # the write has not begun

        finish_job.set()
        response = await asyncio.wait_for(trigger, timeout=10)

        assert response.status_code == 200
        assert response.json()["pushed"] == 1
        assert order == ["job start", "job end", "upload"]
    finally:
        finish_job.set()
        stop_event.set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_a_second_applied_run_is_told_busy_and_writes_nothing(
    client, auth_headers, session, wire, backup_root
):
    """One applied mode at a time in this process. Two concurrent applied runs
    would each raise the fence and the first to finish would drop it under the
    second, and both would be writing to Plex at once besides.

    The first run is held inside ``upload_artwork`` (which runs in a thread, so
    the event loop is free to serve the second request) while the second
    trigger goes out. Mutation proof: drop the ``mode_lock`` in
    ``_run_plex_writing_mode`` and the second run answers 200 and uploads too.
    """
    started = threading.Event()
    release = threading.Event()

    class BlockingItem(FakeItem):
        def uploadPoster(self, filepath=None):  # noqa: N802 - plexapi name
            started.set()
            release.wait(10)
            super().uploadPoster(filepath=filepath)

    await _add_item(session, rating_key="rk1")
    _seed_backup(backup_root, "Movies", "A (1999)", "poster.jpg", b"p")
    item = BlockingItem()
    plex = wire({"rk1": item})

    first = asyncio.create_task(
        client.post(
            "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
        )
    )
    try:
        await asyncio.to_thread(started.wait, 10)

        second = await client.post(
            "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
        )

        assert second.status_code == 409
        assert "already running" in second.json()["detail"]
        # And it got nowhere near Plex: the busy answer comes before the mode
        # is even constructed a session.
        assert plex.fetched == ["rk1"]
    finally:
        release.set()
        first_response = await asyncio.wait_for(first, timeout=10)

    assert first_response.status_code == 200
    assert first_response.json()["pushed"] == 1
    # Exactly one upload happened, not two.
    assert item.uploaded == [("poster", b"p")]


async def test_a_mode_triggered_during_a_restart_is_told_about_the_restart(
    client, auth_headers, app, wire
):
    """The same lock, a different holder, and the sentence has to say which.

    The restart route takes ``mode_lock`` and keeps it until the process is
    replaced, so an operator who pressed Restart and then Confirm would
    otherwise be told an artwork mode is writing to a media server -- a claim
    about a mode that does not exist, when the truth is the button they
    themselves pressed a moment earlier.
    """
    wire({})
    await app.state.mode_lock.acquire()
    app.state.restart_in_flight = True
    try:
        response = await client.post(
            "/api/artwork-modes/restore", headers=auth_headers, json={"apply": True}
        )
    finally:
        app.state.restart_in_flight = False
        app.state.mode_lock.release()

    assert response.status_code == 409
    assert response.json()["detail"] == RESTART_IN_PROGRESS, (
        "a mode trigger during a restart was told another mode is running"
    )


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
    the fence must be up while it happens, and released afterwards. Both fields
    are ours here, so the fence has to cover the background half too."""
    await _add_item(session, rating_key="rk1")
    item = FakeItem(thumb="/thumb", art="/art", pause=app.state.worker_pause)
    wire({"rk1": item}, handler=_serves_ranged(_stamped_jpeg("fp-abc")))

    response = await client.post(
        "/api/artwork-modes/reset", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["reset"] == 2 and body["failed"] == 0
    assert body["fields"] == 2
    assert item.unlocked == ["poster", "art"]
    assert item.selected == [AGENT_KEY, AGENT_ART_KEY]
    assert item.paused_during_write == [True, True]
    assert app.state.worker_pause.is_paused is False
    assert item.refreshed is False


# --- logo + logo revert ------------------------------------------------------


async def _marker(session, item_id):
    """The logo marker as the endpoint's own session committed it."""
    return (
        await session.execute(
            select(MediaItem.logo_upload_key).where(MediaItem.id == item_id)
        )
    ).scalar_one()


def _serves_logo():
    def handler(request):
        if request.url.path == "/logo.png":
            return httpx.Response(200, content=LOGO_BYTES)
        return httpx.Response(404)

    return handler


async def test_logo_requires_a_session(client):
    assert (await client.post("/api/artwork-modes/logo", json={})).status_code == 401


async def test_logo_revert_requires_a_session(client):
    assert (
        await client.post("/api/artwork-modes/logo-revert", json={})
    ).status_code == 401


async def test_logo_503_when_plex_unset(client, auth_headers):
    response = await client.post("/api/artwork-modes/logo", headers=auth_headers, json={})
    assert response.status_code == 503


async def test_logo_revert_503_when_plex_unset(client, auth_headers):
    response = await client.post(
        "/api/artwork-modes/logo-revert", headers=auth_headers, json={}
    )
    assert response.status_code == 503


async def test_logo_dry_run_is_the_default_and_uploads_nothing(
    client, auth_headers, session, wire
):
    await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None)
    wire({"rk1": item}, providers=[FakeLogoProvider()])

    # apply omitted -> falls back to config default (logo_apply=False).
    response = await client.post("/api/artwork-modes/logo", headers=auth_headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "logo" and body["dry_run"] is True
    assert body["items"] == 1 and body["items_missing_logo"] == 1
    assert item.uploaded == []


async def test_apply_logo_uploads_with_the_pool_paused(
    client, auth_headers, session, wire, app
):
    """The pause mutation-proof for the logo updater: it writes to the same Plex
    items the live pipeline does, so the fence must be up at the instant of the
    upload and released afterwards. Drop the ``apply`` branch in
    ``_run_plex_writing_mode`` and the recorded value flips to False."""
    row = await _add_item(session, rating_key="rk1")
    item = FakeItem(logo=None, pause=app.state.worker_pause)
    wire({"rk1": item}, handler=_serves_logo(), providers=[FakeLogoProvider()])

    response = await client.post(
        "/api/artwork-modes/logo", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["uploaded"] == 1
    assert body["unmarked"] == 0
    assert (body["no_logo_available"], body["upload_failed"]) == (0, 0)
    # The fold is gone: no total beside its own summands.
    assert "failed" not in body
    assert ("logo", LOGO_BYTES) in item.uploaded
    assert item.locked == ["logo"]
    assert item.paused_during_write == [True]
    assert app.state.worker_pause.is_paused is False
    assert item.refreshed is False
    # The marker the revert endpoint below depends on.
    assert await _marker(session, row.id) == OUR_LOGO_KEY


async def test_logo_revert_dry_run_is_the_default_and_changes_nothing(
    client, auth_headers, session, wire
):
    await _add_item(session, rating_key="rk1", logo_upload_key=OUR_LOGO_KEY)
    item = FakeItem(logo="/clearLogo", logos=((OUR_LOGO_KEY, True),))
    wire({"rk1": item})

    response = await client.post(
        "/api/artwork-modes/logo-revert", headers=auth_headers, json={}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "logo_revert" and body["dry_run"] is True
    assert body["items_with_our_logo"] == 1
    assert item.unlocked == [] and item.deleted_logos == 0
    # Unlike reset, nothing is left orphaned on the server, so no note is due.
    assert "note" not in body


async def test_apply_logo_revert_clears_with_the_pool_paused(
    client, auth_headers, session, wire, app
):
    """The pause mutation-proof for the logo revert, and the end-to-end shape of
    the marker: an item this service set a logo on, still showing that exact
    upload, is unlocked and cleared, and the marker goes with it."""
    row = await _add_item(session, rating_key="rk1", logo_upload_key=OUR_LOGO_KEY)
    item = FakeItem(
        logo="/clearLogo", logos=((OUR_LOGO_KEY, True),), pause=app.state.worker_pause
    )
    wire({"rk1": item})

    response = await client.post(
        "/api/artwork-modes/logo-revert", headers=auth_headers, json={"apply": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is False and body["cleared"] == 1 and body["failed"] == 0
    assert item.unlocked == ["logo"] and item.deleted_logos == 1
    assert item.paused_during_write == [True]
    assert app.state.worker_pause.is_paused is False
    assert item.refreshed is False
    assert await _marker(session, row.id) is None
