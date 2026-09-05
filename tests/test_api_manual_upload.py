"""The browser-upload source for manual mode.

``POST /api/items/{item_id}/renders/{art_kind}/manual/upload`` is the third
form of the same request ``tests/test_api_manual.py`` covers for the other two:
a multipart body instead of a URL or a mount path, ending in the same tail and
writing the same mirror. What is different is what has to be *withheld*.

The bytes arrive from a browser, so three things are never trusted and never
repeated: the part's ``Content-Type`` (a client claim -- a Pillow decode is the
only content check), the part's file name (never read, so never stored, served
or logged -- row 213), and the body's length (a cap enforced while the body is
still arriving, not after it has been spooled somewhere).

The multipart envelope is hand-built rather than handed to ``httpx``'s
``files=`` for one test in particular: the body has to be yielded lazily so the
number of bytes the application actually consumed can be asserted, which is how
"the cap stopped the read" is told apart from "the cap was checked after the
whole body was already on disk".
"""
import io
import os
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from autoposter.api import manual
from autoposter.api.auth import hash_password
from autoposter.api.candidates import PICK_MAX_BYTES
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job, MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

LIBRARY = "Movies"
ROOT_FOLDER = "A Movie (1999)"
SHOW_ROOT_FOLDER = "A Show (2001)"
TMDB_ID = 550

BOUNDARY = "----autoposteruploadtest"
MULTIPART = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}

# Distinctive, and shaped like an escape attempt: if any of it ever reaches a
# response body, a log line or an events row, the assertions below say so.
UPLOADED_NAME = "../../nikola-secret-name.png"


def _png(color=(10, 20, 30), size=(4, 6), mode: str = "RGBA") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else (*color, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png()


def _envelope(payload: bytes, *, field: str = "file", filename: str = UPLOADED_NAME) -> bytes:
    """One file part, spelled out. ``Content-Type`` is deliberately a lie in
    some tests: it is a client claim this endpoint reads for nothing."""
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode()
    return head + payload + f"\r\n--{BOUNDARY}--\r\n".encode()


class _CountingBody:
    """A request body yielded chunk by chunk, counting what was consumed.

    ``httpx``'s ASGITransport pulls one chunk per ASGI ``receive()``, so
    ``sent`` is the number of bytes the application actually read -- the only
    way to tell a streaming cap from a cap applied after the fact.
    """

    def __init__(self, payload: bytes, chunk: int = 64 * 1024, **envelope):
        self._body = _envelope(payload, **envelope)
        self._chunk = chunk
        self.sent = 0

    async def __aiter__(self):
        for start in range(0, len(self._body), self._chunk):
            piece = self._body[start : start + self._chunk]
            self.sent += len(piece)
            yield piece


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


@pytest.fixture
def manual_root(tmp_path) -> Path:
    root = tmp_path / "manualassets"
    root.mkdir()
    return root


@pytest.fixture
def assets_root(tmp_path) -> Path:
    root = tmp_path / "assets"
    root.mkdir()
    return root


@pytest_asyncio.fixture
async def app(session_factory, manual_root, assets_root):
    config = load_config(EXAMPLE).model_copy(
        update={"manual_assets_root": manual_root, "assets_root": assets_root}
    )
    # No app.state.http at all: an upload must never reach the network, and a
    # missing client makes that a failure rather than a passing test.
    return create_app(config, session_factory, _secrets())


@pytest_asyncio.fixture
async def client(app):
    asgi = ASGITransport(app=app)
    async with AsyncClient(transport=asgi, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    response = await client.post("/api/login", json={"password": PASSWORD})
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def _item(session, kind: str = "movie", with_render: bool = True, **extra) -> int:
    defaults = dict(
        rating_key="rk1", library=LIBRARY, kind=kind, title="A Movie", year=1999,
        tmdb_id=TMDB_ID, tvdb_id=660, imdb_id="tt0137523", root_folder=ROOT_FOLDER,
    )
    defaults.update(extra)
    item = MediaItem(**defaults)
    session.add(item)
    await session.flush()
    if with_render:
        session.add(
            Render(
                item_id=item.id, art_kind="poster", status="rendered",
                asset_path=f"/assets/{LIBRARY}/{ROOT_FOLDER}/poster.jpg",
                fingerprint="f" * 64, badge_fingerprint="b" * 64,
            )
        )
    await session.commit()
    return item.id


def _upload(client, item_id: int, art_kind: str, headers, content, extra_headers=None):
    return client.post(
        f"/api/items/{item_id}/renders/{art_kind}/manual/upload",
        content=content,
        headers={**headers, **MULTIPART, **(extra_headers or {})},
    )


def _mirror(manual_root: Path, name: str = "poster.jpg", root: str = ROOT_FOLDER) -> Path:
    return manual_root / LIBRARY / root / name


# --- the happy path, per art kind -------------------------------------------


@pytest.mark.parametrize("art_kind, name", [("poster", "poster.jpg"), ("background", "background.jpg")])
async def test_an_uploaded_movie_art_kind_writes_the_transcoded_mirror(
    client, auth_headers, session, manual_root, art_kind, name
):
    """The same deterministic mirror a ``/manualassets/...`` path source
    writes, transcoded to the ``.jpg`` the mirror is named for."""
    item_id = await _item(session)

    response = await _upload(client, item_id, art_kind, auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 200
    assert response.json() == {"status": "installed", "queued": True}
    mirror = _mirror(manual_root, name)
    assert mirror.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(mirror) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (4, 6)


async def test_an_uploaded_season_poster_writes_the_season_mirror(
    client, auth_headers, session, manual_root
):
    item_id = await _item(
        session, kind="season", with_render=False, rating_key="rk2",
        root_folder=SHOW_ROOT_FOLDER, season_number=1,
    )

    response = await _upload(client, item_id, "season_poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 200
    assert _mirror(manual_root, "Season01.jpg", SHOW_ROOT_FOLDER).read_bytes()[:3] == b"\xff\xd8\xff"


async def test_an_uploaded_title_card_writes_the_episode_mirror(
    client, auth_headers, session, manual_root
):
    item_id = await _item(
        session, kind="episode", with_render=False, rating_key="rk3",
        root_folder=SHOW_ROOT_FOLDER, season_number=1, episode_number=1,
    )

    response = await _upload(client, item_id, "title_card", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 200
    assert _mirror(manual_root, "S01E01.jpg", SHOW_ROOT_FOLDER).read_bytes()[:3] == b"\xff\xd8\xff"


async def test_an_upload_nulls_both_fingerprints_and_queues_a_reprocess(
    client, auth_headers, session
):
    """The same override semantics the URL source has: one row's fingerprints
    nulled -- the short-circuit sits above the override lookup -- and one job
    queued. Nothing library-wide moves."""
    item_id = await _item(session)

    response = await _upload(client, item_id, "poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 200
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None
    job = (await session.execute(select(Job))).scalars().one()
    assert job.kind == "process_item"
    assert job.payload["tmdb_id"] == TMDB_ID


# --- what is withheld -------------------------------------------------------


async def test_the_events_row_records_the_upload_form_and_not_the_file_name(
    client, auth_headers, session
):
    """``source_kind`` is the third value beside ``url`` and ``file``. The
    uploaded name is a browser's own string and an events row is read casually
    and pasted into tickets, so none of it goes in."""
    item_id = await _item(session)

    await _upload(client, item_id, "poster", auth_headers, _envelope(PNG_BYTES))

    event = (
        await session.execute(
            select(EventLog).where(EventLog.event_type == "manual_source_installed")
        )
    ).scalar_one()
    assert event.payload == {"item_id": item_id, "art_kind": "poster", "source_kind": "upload"}
    assert "nikola" not in event.outcome


async def test_the_uploaded_name_is_never_served_and_never_logged(
    client, auth_headers, session, caplog
):
    """Row 213, at this endpoint: the served string and every log line carry
    class names, counts and fixed sentences -- never a caller's own string."""
    item_id = await _item(session)

    with caplog.at_level("DEBUG"):
        response = await _upload(client, item_id, "poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 200
    assert "nikola" not in response.text
    for record in caplog.records:
        assert "nikola" not in record.getMessage() + (record.exc_text or "")


async def test_a_path_shaped_file_name_cannot_move_the_target(
    client, auth_headers, session, manual_root, tmp_path
):
    """The stored name is built by ``manual_override_target`` and by nothing
    else, so a name carrying ``..`` is not defended against -- it is simply
    never read."""
    item_id = await _item(session)

    response = await _upload(
        client, item_id, "poster", auth_headers,
        _envelope(PNG_BYTES, filename="../../../../escape.jpg"),
    )

    assert response.status_code == 200
    assert _mirror(manual_root).is_file()
    assert sorted(p.name for p in manual_root.rglob("*") if p.is_file()) == ["poster.jpg"]
    assert not (tmp_path / "escape.jpg").exists()


# --- the cap ----------------------------------------------------------------


async def test_an_upload_one_byte_past_the_cap_is_413_and_writes_nothing(
    client, auth_headers, session, manual_root
):
    """Exactly ``PICK_MAX_BYTES + 1``: the boundary itself, at the real
    constant, so a cap that drifted to a bespoke number fails here."""
    item_id = await _item(session)

    response = await _upload(
        client, item_id, "poster", auth_headers,
        _CountingBody(b"\0" * (PICK_MAX_BYTES + 1)),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "the upload exceeds the size cap"}
    assert list(manual_root.rglob("*")) == []
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_the_cap_stops_the_read_instead_of_buffering_the_body(
    client, auth_headers, session, manual_root, monkeypatch
):
    """The refusal is worth nothing if it arrives after the body has been
    spooled: an unbounded upload is a disk-fill whatever the response says.
    The body is yielded lazily and counted, so what is asserted is that the
    application STOPPED READING near the cap -- not that it answered 413.
    """
    monkeypatch.setattr(manual, "PICK_MAX_BYTES", 64 * 1024)
    item_id = await _item(session)
    body = _CountingBody(b"\0" * (8 * 1024 * 1024))

    response = await _upload(client, item_id, "poster", auth_headers, body)

    assert response.status_code == 413
    assert response.json() == {"detail": "the upload exceeds the size cap"}
    # The cap, plus the envelope allowance, plus at most the one chunk that
    # crossed it -- and nothing like the 8 MiB that was offered.
    assert body.sent <= 64 * 1024 + manual.UPLOAD_ENVELOPE_BYTES + 64 * 1024
    assert list(manual_root.rglob("*")) == []


# --- the refusals -----------------------------------------------------------


async def test_a_non_image_upload_is_refused_by_the_decode(
    client, auth_headers, session, manual_root
):
    """A decode is the only content check: the part's ``Content-Type`` says
    ``image/png`` here and is worth nothing. 422, not 502 -- nothing upstream
    failed, the operator picked the wrong file."""
    item_id = await _item(session)

    response = await _upload(client, item_id, "poster", auth_headers, _envelope(b"not a png"))

    assert response.status_code == 422
    assert response.json()["detail"].startswith("undecodable image (")
    assert list(manual_root.rglob("*")) == []


async def test_an_upload_with_no_file_part_is_refused_with_a_fixed_sentence(
    client, auth_headers, session, manual_root
):
    item_id = await _item(session)

    response = await _upload(
        client, item_id, "poster", auth_headers, _envelope(PNG_BYTES, field="picture")
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "the upload has no file part"}
    assert list(manual_root.rglob("*")) == []


async def test_an_empty_file_part_is_refused_with_the_same_sentence(
    client, auth_headers, session, manual_root
):
    item_id = await _item(session)

    response = await _upload(client, item_id, "poster", auth_headers, _envelope(b""))

    assert response.status_code == 422
    assert response.json() == {"detail": "the upload has no file part"}
    assert list(manual_root.rglob("*")) == []


async def test_a_malformed_multipart_body_is_refused_with_a_fixed_sentence(
    client, auth_headers, session, manual_root
):
    """The parser's own message names sizes and part counts; the served
    sentence is fixed instead."""
    item_id = await _item(session)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/manual/upload",
        content=b"not multipart at all",
        headers={**auth_headers, "Content-Type": "multipart/form-data"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "the upload is not a usable multipart form"}
    assert list(manual_root.rglob("*")) == []


async def test_the_refusal_body_is_a_sentence_and_never_an_echo(
    client, auth_headers, session
):
    """The #178 handler (app.py) strips ``input`` from a validation 422 so a
    paste is never reflected. This route declares no pydantic body, so no
    validation error can arise on it at all -- and its own refusals are plain
    sentences with nothing of the request in them. Both halves, asserted."""
    item_id = await _item(session)

    response = await _upload(
        client, item_id, "poster", auth_headers, _envelope(b"junk", filename="nikola.png")
    )

    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], str)
    assert "nikola" not in response.text


async def test_a_logo_cannot_be_uploaded(client, auth_headers, session, manual_root):
    """The one art kind whose stored name is derived from the SOURCE's name
    (``_logo_suffix``), which an upload is not allowed to read. Refused rather
    than guessed, and the URL and mount-path sources still take logos."""
    item_id = await _item(session)

    response = await _upload(client, item_id, "logo", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 422
    assert response.json() == {
        "detail": "a logo cannot be uploaded; give a URL or a mount path instead"
    }
    assert list(manual_root.rglob("*")) == []


# --- refusals that are not about the body -----------------------------------


async def test_the_upload_requires_a_session(client, session, manual_root):
    item_id = await _item(session)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/manual/upload",
        content=_envelope(PNG_BYTES),
        headers=MULTIPART,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "not authenticated"}
    assert list(manual_root.rglob("*")) == []


async def test_an_unknown_item_is_404(client, auth_headers, manual_root):
    response = await _upload(client, 999, "poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 404
    assert list(manual_root.rglob("*")) == []


async def test_an_art_kind_the_item_cannot_have_is_404(
    client, auth_headers, session, manual_root
):
    item_id = await _item(session)

    response = await _upload(client, item_id, "title_card", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 404
    assert list(manual_root.rglob("*")) == []


async def test_an_item_with_no_root_folder_is_409(
    client, auth_headers, session, manual_root
):
    """Checked before the body is read at all -- the mirror layout is rooted
    at ``root_folder`` and there is nowhere for the bytes to go."""
    item_id = await _item(session, root_folder=None)

    response = await _upload(client, item_id, "poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 409
    assert list(manual_root.rglob("*")) == []
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_a_failed_mount_write_is_503_and_leaks_no_path(
    client, auth_headers, session, manual_root, monkeypatch
):
    """A read-only mount. ``os.replace``'s OSError carries the destination's
    full filesystem path, which the detail must not."""
    item_id = await _item(session)

    def refuse(src, dst):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)

    response = await _upload(client, item_id, "poster", auth_headers, _envelope(PNG_BYTES))

    assert response.status_code == 503
    assert response.json() == {"detail": "could not write to the override mount"}
    assert not _mirror(manual_root).exists()
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert (await session.execute(select(Job))).scalars().all() == []
