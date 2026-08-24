"""The two manual-source endpoints.

``POST /api/items/{item_id}/renders/{art_kind}/manual`` and
``POST /api/collections/{collection_id}/poster`` both take one string that is
either a URL or a path on the manual-assets mount. 6d's pick endpoint could
prove its URL was one a provider had just offered; these cannot, so what
stands in its place is ``net/guard`` for a URL and double-``realpath``
containment for a path. Both are asserted here through a ``MockTransport``
whose **request log** is the first assertion, exactly as ``test_api_pick.py``
argues: "the response was 422" is also what an implementation that fetched the
metadata service and rejected the answer afterwards would say, and by then the
request has left the container.

``manual_assets_root`` and ``assets_root`` are both ``tmp_path`` subdirectories
throughout, so the only files any of this can read or write are inside the
test's own directory.
"""
import io
import os
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from autoposter.api.auth import hash_password
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets
from autoposter.db.models import EventLog, Job, ManagedCollection, MediaItem, Render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

LIBRARY = "Movies"
ROOT_FOLDER = "A Movie (1999)"
TMDB_ID = 550
DEDUPE_KEY = f"process_item:movie:tmdb{TMDB_ID}"

COLLECTION_TITLE = "Oscars Best Picture"

# A public literal, so the guard's resolver answers out of the string and no
# name is ever looked up.
PUBLIC = "93.184.216.34"
SOURCE_URL = f"https://{PUBLIC}/art.png"
LOGO_URL = f"https://{PUBLIC}/logo.png"
# What an SSRF attempt through these endpoints looks like: an address only the
# container can reach, aimed at the cloud metadata service.
FORGED_URL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


def _png(color=(10, 20, 30), size=(4, 6), mode: str = "RGBA") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else (*color, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png()


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


class _RecordingTransport:
    """A MockTransport plus the request log the security tests assert on."""

    def __init__(self, response_for):
        self.requests: list[httpx.Request] = []
        self._response_for = response_for

    def client(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._response_for(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def _ok(content: bytes = PNG_BYTES, content_type: str = "image/png"):
    return lambda request: httpx.Response(
        200, content=content, headers={"content-type": content_type}
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


@pytest.fixture
def outside(tmp_path) -> Path:
    """A file next to the mount, not in it -- what an escape would reach."""
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG_BYTES)
    return secret


@pytest.fixture
def transport() -> _RecordingTransport:
    return _RecordingTransport(_ok())


@pytest_asyncio.fixture
async def app(session_factory, manual_root, assets_root, transport):
    config = load_config(EXAMPLE).model_copy(
        update={"manual_assets_root": manual_root, "assets_root": assets_root}
    )
    app = create_app(config, session_factory, _secrets())
    async with transport.client() as http:
        app.state.http = http
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


async def _collection(session, **extra) -> int:
    defaults = dict(
        library=LIBRARY, title=COLLECTION_TITLE, kind="smart", definition_hash="d" * 64,
    )
    defaults.update(extra)
    collection = ManagedCollection(**defaults)
    session.add(collection)
    await session.commit()
    return collection.id


def _install(client, item_id: int, art_kind: str, headers, source: str = SOURCE_URL):
    return client.post(
        f"/api/items/{item_id}/renders/{art_kind}/manual",
        json={"source": source},
        headers=headers,
    )


def _set_poster(client, collection_id: int, headers, source: str = SOURCE_URL):
    return client.post(
        f"/api/collections/{collection_id}/poster",
        json={"source": source},
        headers=headers,
    )


def _mirror(manual_root: Path, name: str = "poster.jpg") -> Path:
    return manual_root / LIBRARY / ROOT_FOLDER / name


def _collection_poster(assets_root: Path, name: str = "poster.jpg") -> Path:
    return assets_root / LIBRARY / COLLECTION_TITLE / name


# --- the security invariant -------------------------------------------------


async def test_the_metadata_service_is_refused_and_never_requested(
    client, auth_headers, session, manual_root, transport
):
    """The phase's security invariant, at the endpoint.

    The pick endpoint was safe because the URL had to be one a provider had
    just offered. Manual mode gives that up on purpose, so the guard is the
    only thing left -- and a 422 alone would also be the answer from an
    implementation that fetched ``169.254.169.254`` and rejected the response
    afterwards, by which time the credentials are in this process. The request
    log is asserted FIRST for that reason: wrapped the other way round, an
    unguarded implementation fails on the status code and the log is never
    read.
    """
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers, source=FORGED_URL)

    assert transport.urls == []
    assert response.status_code == 422
    assert not _mirror(manual_root).exists()
    assert list(manual_root.rglob("*")) == []
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert render.badge_fingerprint == "b" * 64
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_the_collection_endpoint_refuses_the_metadata_service_too(
    client, auth_headers, session, assets_root, transport
):
    """Same guard, second endpoint. Asserted separately rather than assumed
    from the first: two handlers calling one helper is a convention, and a
    convention is what a later edit breaks."""
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers, source=FORGED_URL)

    assert transport.urls == []
    assert response.status_code == 422
    assert list(assets_root.rglob("*")) == []


async def test_a_refused_source_is_explained_without_being_echoed(
    client, auth_headers, session
):
    """The guard's reason reaches the operator -- "that source was refused"
    with nothing after it leaves them with a URL and no idea why -- but the
    URL itself does not come back. This response is read out of a browser
    console and pasted into tickets, and an operator's URL can carry userinfo
    credentials or a signed query parameter."""
    item_id = await _item(session)
    credentialed = "http://user:hunter2@169.254.169.254/latest/meta-data/"

    response = await _install(client, item_id, "poster", auth_headers, source=credentialed)

    assert response.status_code == 422
    assert "hunter2" not in response.text
    assert "169.254" not in response.text
    assert "link-local" in response.json()["detail"]


async def test_a_redirect_into_the_metadata_service_is_refused(
    client, auth_headers, session, manual_root, transport
):
    """The hop the operator cannot see. A perfectly public URL that answers
    302 to ``169.254.169.254`` is the SSRF that survives any check made on the
    typed URL alone."""
    def respond(request):
        if request.url.host == PUBLIC:
            return httpx.Response(302, headers={"location": FORGED_URL})
        return httpx.Response(200, content=PNG_BYTES, headers={"content-type": "image/png"})

    transport._response_for = respond
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers)

    assert transport.urls == [SOURCE_URL]
    assert response.status_code == 422
    assert list(manual_root.rglob("*")) == []


# --- mount-path containment -------------------------------------------------


@pytest.mark.parametrize(
    "source, why",
    [
        ("../secret.png", "a relative escape"),
        ("nested/../../secret.png", "an escape that only appears mid-path"),
        ("/etc/passwd", "an absolute path outside the mount"),
        ("", "the empty string, which resolves to the mount root itself"),
        (".", "the mount root by another name"),
        ("missing.png", "a path inside the mount with no file at it"),
        ("subdir", "a directory rather than a file"),
    ],
)
async def test_a_source_path_outside_the_mount_is_refused(
    client, auth_headers, session, manual_root, outside, transport, source, why
):
    """Containment is on the resolved target, not the string. Nothing is
    fetched either -- a path source must never reach the network."""
    (manual_root / "subdir").mkdir()
    (manual_root / "nested").mkdir()
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers, source=source)

    assert response.status_code == 422, why
    assert transport.urls == []
    assert not _mirror(manual_root).exists()


async def test_a_symlink_out_of_the_mount_is_refused_on_its_target(
    client, auth_headers, session, manual_root, outside, transport
):
    """The case a lexical check cannot catch. ``inside.png`` is a path within
    the mount by every string test there is; only ``realpath`` on both sides
    reveals that it points at a file outside it. This is why the containment
    is the double-realpath idiom and not ``normpath`` or a ``startswith``."""
    link = manual_root / "inside.png"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform will not create symlinks for an unprivileged user")
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers, source="inside.png")

    assert response.status_code == 422
    assert transport.urls == []
    assert not _mirror(manual_root).exists()


async def test_the_refusal_does_not_echo_the_path(client, auth_headers, session, manual_root):
    """An endpoint that repeats its input back is a reflector, and the path
    tells the operator nothing they did not just type."""
    item_id = await _item(session)

    response = await _install(
        client, item_id, "poster", auth_headers, source="../../../etc/shadow"
    )

    assert response.status_code == 422
    assert "shadow" not in response.text
    assert ".." not in response.text


# --- the item endpoint's happy paths ----------------------------------------


async def test_a_url_source_writes_the_transcoded_jpeg_mirror(
    client, auth_headers, session, manual_root, transport
):
    """The pipeline stats ONLY the ``.jpg`` mirror name (render/naming.py), so
    a PNG that stayed a PNG would be written and then never looked at. Magic
    bytes rather than the suffix: naming the file ``.jpg`` is exactly the
    mistake this catches."""
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "installed", "queued": True}
    assert transport.urls == [SOURCE_URL]
    mirror = _mirror(manual_root)
    assert mirror.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(mirror) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (4, 6)


async def test_a_mount_path_source_writes_the_same_mirror_without_fetching(
    client, auth_headers, session, manual_root, transport
):
    """Posterizarr's ``-PicturePath``: a file the operator already put on the
    mount. Nothing is fetched -- the request log is what proves the URL branch
    was not taken for a string that is not a URL."""
    (manual_root / "incoming").mkdir()
    (manual_root / "incoming" / "chosen.png").write_bytes(PNG_BYTES)
    item_id = await _item(session)

    response = await _install(
        client, item_id, "poster", auth_headers, source="incoming/chosen.png"
    )

    assert response.status_code == 200
    assert transport.urls == []
    assert _mirror(manual_root).read_bytes()[:3] == b"\xff\xd8\xff"


async def test_a_manual_install_nulls_both_fingerprints(
    client, auth_headers, session, manual_root
):
    """The fingerprint short-circuit (render/pipeline.py) sits ABOVE the
    override lookup, so an install with the fingerprints left alone reports
    "unchanged" and the operator's image never reaches a pixel."""
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None


async def test_a_manual_install_queues_a_reprocess(client, auth_headers, session):
    item_id = await _item(session)

    await _install(client, item_id, "poster", auth_headers)

    job = (await session.execute(select(Job).where(Job.dedupe_key == DEDUPE_KEY))).scalar_one()
    assert job.kind == "process_item"
    assert job.payload["tmdb_id"] == TMDB_ID


async def test_the_events_row_records_the_form_of_the_source_not_the_source(
    client, auth_headers, session
):
    """Under an override the render row stamps ``provider="manual"`` and a
    filesystem path, so this row is the record that an operator chose the
    image at all. Not even the host goes in: a candidate's host belonged to a
    provider, this string is the operator's own and can carry credentials."""
    item_id = await _item(session)
    credentialed = f"https://user:hunter2@{PUBLIC}/art.png"

    response = await _install(client, item_id, "poster", auth_headers, source=credentialed)

    assert response.status_code == 200
    event = (
        await session.execute(select(EventLog).where(EventLog.source == "manual"))
    ).scalar_one()
    assert event.event_type == "manual_source_installed"
    assert event.outcome == f"{LIBRARY}/A Movie poster from a manual source"
    assert event.payload["source_kind"] == "url"
    assert "hunter2" not in repr(event.payload)
    assert PUBLIC not in repr(event.payload)


# --- logo coupling ----------------------------------------------------------


async def test_a_manual_logo_is_installed_untranscoded_beside_the_poster(
    client, auth_headers, session, manual_root
):
    """No transcode: a logo is composited over the poster and needs the alpha
    channel a JPEG cannot carry."""
    item_id = await _item(session)

    response = await _install(client, item_id, "logo", auth_headers, source=LOGO_URL)

    assert response.status_code == 200
    logo = _mirror(manual_root, "logo.png")
    assert logo.read_bytes() == PNG_BYTES
    with Image.open(logo) as image:
        assert image.mode == "RGBA"
    assert not _mirror(manual_root).exists()


async def test_a_manual_logo_nulls_the_poster_row(client, auth_headers, session):
    """There is no ``logo`` render row and never will be -- a logo rides into
    the POSTER's fingerprint as ``logo_sha``. Nulling nothing leaves the
    poster reporting "unchanged" forever."""
    item_id = await _item(session)

    response = await _install(client, item_id, "logo", auth_headers, source=LOGO_URL)

    assert response.json() == {"status": "installed", "queued": True}
    render = (
        await session.execute(
            select(Render).where(Render.item_id == item_id, Render.art_kind == "poster")
        )
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None


async def test_a_new_logo_suffix_replaces_the_old_one(
    client, auth_headers, session, manual_root, transport
):
    """``find_logo_override`` returns the FIRST existing suffix in
    ``LOGO_OVERRIDE_SUFFIXES`` order (.png first), so a ``logo.png`` left from
    an earlier install would keep winning over a newer ``logo.webp``."""
    from autoposter.config.loader import load_config
    from autoposter.plex.client import ResolvedItem
    from autoposter.render.pipeline import find_logo_override

    item_id = await _item(session)
    first = await _install(client, item_id, "logo", auth_headers, source=LOGO_URL)
    assert first.status_code == 200
    assert _mirror(manual_root, "logo.png").exists()

    transport._response_for = _ok(PNG_BYTES, "image/webp")
    second = await _install(
        client, item_id, "logo", auth_headers, source=f"https://{PUBLIC}/logo.webp"
    )

    assert second.status_code == 200
    assert not _mirror(manual_root, "logo.png").exists()
    assert _mirror(manual_root, "logo.webp").exists()
    config = load_config(EXAMPLE).model_copy(update={"manual_assets_root": manual_root})
    resolved = ResolvedItem(
        rating_key="rk1", library=LIBRARY, kind="movie", title="A Movie", year=1999,
        season_number=None, episode_number=None, root_folder=ROOT_FOLDER, file_path=None,
        art_url=None, tmdb_id=TMDB_ID, tvdb_id=660, imdb_id="tt0137523",
    )
    assert find_logo_override(config, resolved) == _mirror(manual_root, "logo.webp")


async def test_a_logo_source_with_no_usable_suffix_defaults_to_png(
    client, auth_headers, session, manual_root
):
    """The suffix is constrained to the ones ``find_logo_override`` looks for,
    so an installed logo is a findable one -- and a query string must not be
    mistaken for an extension."""
    item_id = await _item(session)

    response = await _install(
        client, item_id, "logo", auth_headers, source=f"https://{PUBLIC}/logo?v=2"
    )

    assert response.status_code == 200
    assert _mirror(manual_root, "logo.png").read_bytes() == PNG_BYTES


# --- refusals that are not about the source ---------------------------------


async def test_requires_a_session(client, session, transport):
    item_id = await _item(session)

    response = await client.post(
        f"/api/items/{item_id}/renders/poster/manual", json={"source": SOURCE_URL}
    )

    assert response.status_code == 401
    assert transport.urls == []


async def test_the_collection_endpoint_requires_a_session(client, session, transport):
    collection_id = await _collection(session)

    response = await client.post(
        f"/api/collections/{collection_id}/poster", json={"source": SOURCE_URL}
    )

    assert response.status_code == 401
    assert transport.urls == []


async def test_an_unknown_item_is_404(client, auth_headers, transport):
    response = await _install(client, 999999, "poster", auth_headers)

    assert response.status_code == 404
    assert transport.urls == []


async def test_an_unknown_collection_is_404(client, auth_headers, transport, assets_root):
    response = await _set_poster(client, 999999, auth_headers)

    assert response.status_code == 404
    assert transport.urls == []
    assert list(assets_root.rglob("*")) == []


@pytest.mark.parametrize("art_kind", ["season_poster", "title_card", "bogus"])
async def test_an_art_kind_the_item_cannot_have_is_404(
    client, auth_headers, session, transport, manual_root, art_kind
):
    """Validated before anything is fetched or read -- the clear-override
    idiom, and the request log is what proves the order."""
    item_id = await _item(session)

    response = await _install(client, item_id, art_kind, auth_headers)

    assert response.status_code == 404
    assert transport.urls == []
    assert list(manual_root.rglob("*")) == []


@pytest.mark.parametrize("kind", ["season", "episode"])
async def test_a_logo_is_404_for_a_season_or_episode(client, auth_headers, session, kind):
    """Only the poster render composites a logo, so only the kinds that have
    a poster can carry one."""
    item_id = await _item(
        session, kind=kind, with_render=False, rating_key="rk3",
        season_number=1, episode_number=1,
    )

    response = await _install(client, item_id, "logo", auth_headers, source=LOGO_URL)

    assert response.status_code == 404


async def test_an_item_with_no_root_folder_is_409(
    client, auth_headers, session, manual_root, transport
):
    """``root_folder`` is nullable and the whole mirror layout is rooted at
    it -- checked before anything is fetched or written."""
    item_id = await _item(session, root_folder=None)

    response = await _install(client, item_id, "poster", auth_headers)

    assert response.status_code == 409
    assert transport.urls == []
    assert list(manual_root.rglob("*")) == []
    assert (await session.execute(select(Job))).scalars().all() == []


# --- the failure table ------------------------------------------------------


@pytest.mark.parametrize("art_kind", ["poster", "logo"])
@pytest.mark.parametrize(
    "response_for, why, detail_contains",
    [
        (lambda request: httpx.Response(404), "an upstream 404", "status 404"),
        (
            _ok(b"<!doctype html>", "text/html"),
            "a content type outside the allowlist",
            "content type",
        ),
        (_ok(b"", "image/png"), "an empty body", "empty body"),
        (_ok(b"not actually a png", "image/png"), "bytes no decoder accepts", "undecodable image"),
    ],
)
async def test_a_failed_url_source_writes_nothing_and_leaves_the_row_alone(
    client, auth_headers, session, manual_root, transport, response_for, why, detail_contains,
    art_kind,
):
    """File-success-first, as clear-override orders it: nulled fingerprints
    with no file behind them re-render straight back to the automatic pick
    while the UI claims the operator's choice took.

    Both kinds, because they are defended differently: a poster is decoded and
    re-encoded, a logo is written through untouched, so for a logo the status,
    Content-Type and size checks are the only thing between an HTML error page
    and a file called ``logo.png`` handed to the compositor.

    The detail is the plan's promise, not just the status code: a guard
    reason ("status 404", "content type outside the artwork allowlist",
    "empty body") is URL-free by construction and actionable, unlike the
    generic "could not fetch the image from that URL" a raw transport
    failure gets instead.
    """
    transport._response_for = response_for
    item_id = await _item(session)
    source = SOURCE_URL if art_kind == "poster" else LOGO_URL

    response = await _install(client, item_id, art_kind, auth_headers, source=source)

    assert response.status_code == 502, why
    assert detail_contains in response.json()["detail"], why
    assert list(manual_root.rglob("*")) == []
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert render.badge_fingerprint == "b" * 64
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_a_failed_url_fetch_does_not_log_the_url(
    client, auth_headers, session, transport, caplog
):
    """The response detail already withholds the URL (the test above); the
    server log must too. An httpx exception's message -- and the traceback
    ``exc_info`` would attach -- can embed the full request URL, including
    userinfo credentials, which is the one thing this endpoint's own
    discipline keeps out of responses and event rows precisely because it may
    carry secrets. A server log is read by the same people who read a ticket
    the response text got pasted into."""
    def refused(request):
        raise httpx.ConnectError(f"connection refused: {request.url}")

    transport._response_for = refused
    item_id = await _item(session)

    with caplog.at_level("WARNING"):
        response = await _install(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    for record in caplog.records:
        text = record.getMessage() + (record.exc_text or "")
        assert "http" not in text.lower()


async def test_an_undecodable_file_on_the_mount_is_the_callers_fault(
    client, auth_headers, session, manual_root
):
    """422 rather than 502 for a file source: nothing upstream failed, the
    operator pointed at a file that is not an image. A URL that answers with
    junk is the 502 above -- the far end is what went wrong there."""
    (manual_root / "broken.png").write_bytes(b"not actually a png")
    item_id = await _item(session)

    response = await _install(client, item_id, "poster", auth_headers, source="broken.png")

    assert response.status_code == 422
    assert not _mirror(manual_root).exists()


async def test_a_failed_mount_write_is_503_and_leaks_no_path(
    client, auth_headers, session, manual_root, monkeypatch
):
    """A read-only mount, simulated as clear-override's test does. The detail
    is sanitized: ``os.replace``'s ``OSError`` carries the destination's full
    filesystem path."""
    item_id = await _item(session)

    def refuse(src, dst):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)

    response = await _install(client, item_id, "poster", auth_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "could not write to the override mount"
    assert not _mirror(manual_root).exists()
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert (await session.execute(select(Job))).scalars().all() == []


# --- the collection endpoint ------------------------------------------------


async def test_a_collection_poster_is_written_where_the_reconciler_looks(
    client, auth_headers, session, assets_root, transport
):
    """``local_poster_path`` is what ``apply_poster`` calls, so the file this
    endpoint writes has to be one that lookup returns -- two spellings of the
    layout is a file written successfully and never looked at."""
    from autoposter.collections.posters import local_poster_path

    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "installed", "applies": "next reconcile"}
    assert transport.urls == [SOURCE_URL]
    poster = _collection_poster(assets_root)
    assert poster.read_bytes()[:3] == b"\xff\xd8\xff"
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
    assert local_poster_path(config, LIBRARY, COLLECTION_TITLE) == poster


async def test_the_collection_directory_is_created(client, auth_headers, session, assets_root):
    """A collection that has never had a local poster has no directory under
    ``assets_root`` at all, so the write has to make one."""
    collection_id = await _collection(session)
    assert not _collection_poster(assets_root).parent.exists()

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.status_code == 200
    assert _collection_poster(assets_root).is_file()


async def test_a_new_collection_poster_is_not_shadowed_by_an_older_format(
    client, auth_headers, session, assets_root
):
    """``local_poster_path`` probes ``jpg`` FIRST, so a ``poster.png`` an
    operator placed by hand earlier cannot outrank the JPEG written here. The
    older file is left alone rather than deleted: it is the operator's own,
    and nothing reads it while the jpg exists."""
    from autoposter.collections.posters import local_poster_path

    stale = _collection_poster(assets_root, "poster.png")
    stale.parent.mkdir(parents=True)
    stale.write_bytes(PNG_BYTES)
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.status_code == 200
    config = load_config(EXAMPLE).model_copy(update={"assets_root": assets_root})
    assert local_poster_path(config, LIBRARY, COLLECTION_TITLE) == _collection_poster(
        assets_root
    )
    assert stale.exists()


async def test_a_collection_poster_from_the_mount(
    client, auth_headers, session, assets_root, manual_root, transport
):
    """The path branch reads the MANUAL mount and writes the ASSETS tree --
    two different roots, and swapping them would have the endpoint write into
    the directory it reads sources from."""
    (manual_root / "chosen.png").write_bytes(PNG_BYTES)
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers, source="chosen.png")

    assert response.status_code == 200
    assert transport.urls == []
    assert _collection_poster(assets_root).read_bytes()[:3] == b"\xff\xd8\xff"


async def test_a_collection_poster_source_outside_the_mount_is_refused(
    client, auth_headers, session, assets_root, outside, transport
):
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers, source="../secret.png")

    assert response.status_code == 422
    assert transport.urls == []
    assert list(assets_root.rglob("*")) == []


async def test_a_failed_collection_fetch_writes_nothing(
    client, auth_headers, session, assets_root, transport
):
    transport._response_for = _ok(b"<!doctype html>", "text/html")
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.status_code == 502
    assert list(assets_root.rglob("*")) == []


async def test_the_collection_endpoint_does_not_upload_to_plex(
    client, auth_headers, session, assets_root
):
    """Deliberate: ``apply_poster`` hashes what it uploads into
    ``poster_sha256`` and locks the field on the Plex side. A second path to
    Plex from here would do neither, so the row is left untouched and the
    response says when it will apply."""
    collection_id = await _collection(session)

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.json()["applies"] == "next reconcile"
    collection = (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.id == collection_id)
        )
    ).scalar_one()
    await session.refresh(collection)
    assert collection.poster_sha256 is None


async def test_a_collection_title_that_escapes_the_assets_root_is_refused(
    client, auth_headers, session, assets_root, transport
):
    """Row 124. ``ManagedCollection.title`` is interpolated into the write
    path, and titles come from operator config and from collections adopted
    out of Plex -- so a title carrying ``..`` would steer this endpoint's
    write (``_install`` creates parents) outside ``assets_root``. Refused with
    422 and nothing written, the same posture ``_mount_source`` takes on the
    read side."""
    collection_id = await _collection(session, title="../../escaped")

    response = await _set_poster(client, collection_id, auth_headers)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "the title steers its path outside the assets root"
    )
    assert not (assets_root.parent / "escaped").exists()
    assert list(assets_root.rglob("poster.jpg")) == []
