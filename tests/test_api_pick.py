"""POST /api/items/{item_id}/candidates/{art_kind}/pick.

The one thing this endpoint must never do is fetch a URL a caller invented.
The body carries ``{provider, url}``, but the URL is a *claim*: the handler
re-runs the browse fan-out server-side and refuses anything that pair does not
match exactly, so the only URLs this service ever requests are ones a provider
client put in front of it. Every test below drives that through a
``MockTransport`` whose request log is asserted directly -- "the response was
422" would also be true of an implementation that fetched the attacker's URL
first and rejected it afterwards, which is the whole vulnerability.

``manual_assets_root`` is ``tmp_path`` throughout, so the only files any of
this can write are inside the test's own directory, and ``app.state.providers``
is fakes, so nothing reaches a network by any route.
"""
import hashlib
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
from autoposter.db.models import EventLog, Job, Render

from conftest import seed_media_item
from autoposter.providers.base import ArtCandidate
from autoposter.queue.jobs import enqueue

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
PASSWORD = "correct horse battery staple"

LIBRARY = "Movies"
ROOT_FOLDER = "A Movie (1999)"
TMDB_ID = 550
DEDUPE_KEY = f"process_item:movie:tmdb{TMDB_ID}"

OFFERED_URL = "https://image.tmdb.org/t/p/original/offered.png"
OFFERED_LOGO_URL = "https://assets.fanart.tv/logo/offered.png"
# What an SSRF attempt through this endpoint would look like: a URL no provider
# ever returned, aimed at something only the container can reach.
FORGED_URL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


def _png(color=(10, 20, 30), size=(4, 6), mode: str = "RGBA") -> bytes:
    """A real PNG, so the transcode has something Pillow can actually decode."""
    buffer = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else (*color, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg(color=(10, 20, 30), size=(4, 6)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


PNG_BYTES = _png()
JPEG_BYTES = _jpeg()


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash=hash_password(PASSWORD),
    )


class _FakeProvider:
    """One provider client's whole surface as the pick endpoint uses it."""

    def __init__(self, name: str, candidates=None):
        self.name = name
        self._candidates = candidates or []
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        for art_kind, candidates in self._candidates:
            if art_kind == request.art_kind:
                return list(candidates)
        return []


def _offering(name: str, art_kind: str, candidates) -> _FakeProvider:
    return _FakeProvider(name, [(art_kind, candidates)])


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


def _ok(content: bytes, content_type: str = "image/png"):
    return lambda request: httpx.Response(
        200, content=content, headers={"content-type": content_type}
    )


@pytest.fixture
def manual_root(tmp_path) -> Path:
    root = tmp_path / "manualassets"
    root.mkdir()
    return root


@pytest.fixture
def transport() -> _RecordingTransport:
    return _RecordingTransport(_ok(PNG_BYTES))


@pytest_asyncio.fixture
async def app(session_factory, manual_root, transport):
    config = load_config(EXAMPLE).model_copy(update={"manual_assets_root": manual_root})
    app = create_app(config, session_factory, _secrets())
    async with transport.client() as http:
        app.state.http = http
        app.state.providers = [
            _FakeProvider("TMDB", [
                ("poster", [ArtCandidate("TMDB", OFFERED_URL, "en", 1000, 1500, 9.0)]),
                ("logo", [ArtCandidate("TMDB", OFFERED_LOGO_URL, "en", 800, 300, 9.0)]),
            ]),
        ]
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
    rating_key = defaults.pop("rating_key")
    item = await seed_media_item(session, rating_key, **defaults)
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


def _pick(client, item_id: int, art_kind: str, headers, provider="TMDB", url=OFFERED_URL):
    return client.post(
        f"/api/items/{item_id}/candidates/{art_kind}/pick",
        json={"provider": provider, "url": url},
        headers=headers,
    )


def _mirror(manual_root: Path, name: str = "poster.jpg") -> Path:
    return manual_root / LIBRARY / ROOT_FOLDER / name


# --- refusals ---------------------------------------------------------------


async def test_requires_a_session(client, session, transport):
    item_id = await _item(session)

    response = await client.post(
        f"/api/items/{item_id}/candidates/poster/pick",
        json={"provider": "TMDB", "url": OFFERED_URL},
    )

    assert response.status_code == 401
    assert transport.requests == []


async def test_an_unknown_item_is_404(client, auth_headers, transport):
    response = await _pick(client, 999999, "poster", auth_headers)

    assert response.status_code == 404
    assert transport.requests == []


@pytest.mark.parametrize("art_kind", ["season_poster", "title_card", "bogus"])
async def test_an_art_kind_the_item_cannot_have_is_404(
    client, auth_headers, session, app, transport, art_kind
):
    """Checked against the item's own kind before any provider is asked and
    before anything touches the mount -- the same idiom as clear-override, and
    the fake provider's request log is what proves the order."""
    item_id = await _item(session)
    probe = _offering("Probe", art_kind, [])
    app.state.providers = [probe]

    response = await _pick(client, item_id, art_kind, auth_headers)

    assert response.status_code == 404
    assert probe.requests == []
    assert transport.requests == []


@pytest.mark.parametrize("kind", ["season", "episode"])
async def test_a_logo_pick_is_404_for_a_season_or_episode(
    client, auth_headers, session, transport, kind
):
    item_id = await _item(
        session, kind=kind, with_render=False, rating_key="rk3",
        season_number=1, episode_number=1,
    )

    response = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert response.status_code == 404
    assert transport.requests == []


async def test_an_item_with_no_root_folder_is_409(
    client, auth_headers, session, manual_root, transport
):
    """``root_folder`` is nullable, and the whole mirror layout is rooted at
    it -- checked before any provider is asked, so nothing is fetched, written
    or queued for an item this endpoint has nowhere to write."""
    item_id = await _item(session, root_folder=None)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 409
    assert transport.requests == []
    assert list(manual_root.rglob("*")) == []
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_an_item_with_no_plex_ref_is_409(
    client, auth_headers, session, manual_root, transport
):
    """A row this service has never resolved against Plex (or one a re-key/
    prune left behind) has no native id a ``ResolvedItem`` could be rebuilt
    from -- refused before any provider is asked, same as the no-root-folder
    case, and with the same nothing-happened proof."""
    item_id = await _item(session, plex_ref=False)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "this item has no Plex id"
    assert transport.requests == []
    assert list(manual_root.rglob("*")) == []
    assert (await session.execute(select(Job))).scalars().all() == []


# --- the security invariant -------------------------------------------------


async def test_a_url_no_provider_offered_is_refused_and_never_fetched(
    client, auth_headers, session, manual_root, transport
):
    """The phase's security invariant.

    The body is a claim, not an instruction. A 422 alone would also be the
    answer from an implementation that downloaded the forged URL and rejected
    it afterwards -- by which time the request has already left the container
    and hit the link-local metadata service. The transport's request log is
    therefore the assertion that matters here.
    """
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers, url=FORGED_URL)

    assert [str(request.url) for request in transport.requests] == []
    assert response.status_code == 422
    assert not _mirror(manual_root).exists()


async def test_the_pair_must_match_exactly_not_just_the_url(
    client, auth_headers, session, app, transport
):
    """``(provider, url)``, both halves. A URL that one client offered claimed
    on behalf of another is a mismatch the browse response could never have
    produced, so it is refused rather than quietly accepted."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers, provider="Fanart")

    assert response.status_code == 422
    assert transport.requests == []


async def test_the_refusal_detail_carries_no_url(client, auth_headers, session):
    """The response body is read out of a browser console and pasted into
    tickets; echoing back what was asked for makes this endpoint a reflector."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers, url=FORGED_URL)

    assert FORGED_URL not in response.text
    assert "169.254" not in response.text


# --- the happy pick ---------------------------------------------------------


async def test_a_pick_writes_the_transcoded_jpeg_mirror(
    client, auth_headers, session, manual_root, transport
):
    """The pipeline stats ONLY the ``.jpg`` mirror name (render/naming.py), so
    a picked PNG that stayed a PNG would be written and then never looked at.
    Magic bytes rather than the suffix: naming the file ``.jpg`` is exactly the
    mistake this is here to catch."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    assert response.json() == {"status": "picked", "queued": True}
    assert [str(request.url) for request in transport.requests] == [OFFERED_URL]
    mirror = _mirror(manual_root)
    assert mirror.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(mirror) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (4, 6)


async def test_a_jpeg_source_is_written_through_unchanged(
    client, auth_headers, session, manual_root, transport
):
    """Re-encoding a JPEG that is already a JPEG costs a generation of quality
    for nothing -- the transcode exists to change the container, not to make a
    second pass over pixels that are already in it."""
    transport._response_for = _ok(JPEG_BYTES, "image/jpeg")
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    assert _mirror(manual_root).read_bytes() == JPEG_BYTES


async def test_a_pick_nulls_both_fingerprints_so_the_next_pass_regenerates(
    client, auth_headers, session, manual_root
):
    """The fingerprint short-circuit (render/pipeline.py) sits ABOVE the
    override lookup, so a pick with the fingerprints left alone reports
    "unchanged" and the operator's choice never reaches a pixel."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None


async def test_a_pick_queues_a_reprocess(client, auth_headers, session):
    item_id = await _item(session)

    await _pick(client, item_id, "poster", auth_headers)

    job = (await session.execute(select(Job).where(Job.dedupe_key == DEDUPE_KEY))).scalar_one()
    assert job.kind == "process_item"
    assert job.payload["tmdb_id"] == TMDB_ID


async def test_the_enqueue_dedupes_like_clear_override(
    client, auth_headers, session, manual_root
):
    """Same shared ``_enqueue_reprocess``: a pending job on the same key means
    nothing is inserted and ``queued`` is false. The file is still written --
    the dedupe is about the queue, not the mount."""
    item_id = await _item(session)
    await enqueue(session, kind="process_item", payload={"kind": "movie"}, dedupe_key=DEDUPE_KEY)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.json() == {"status": "picked", "queued": False}
    assert _mirror(manual_root).exists()


async def test_a_pick_on_an_item_with_no_render_row_still_writes_and_queues(
    client, auth_headers, session, manual_root
):
    """Nothing has rendered this item yet, so there is no row to null. The file
    and the enqueue still have to happen."""
    item_id = await _item(session, with_render=False)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 200
    assert _mirror(manual_root).exists()


async def test_the_events_row_records_the_pick_without_the_url(
    client, auth_headers, session
):
    """Under an override the render row stamps ``provider="manual"`` and a
    filesystem path, so which provider's image this actually is survives
    nowhere else. The URL does not go in: an events row is read casually and
    copied into tickets, and Fanart's URLs carry its API key."""
    item_id = await _item(session)

    await _pick(client, item_id, "poster", auth_headers)

    event = (
        await session.execute(select(EventLog).where(EventLog.source == "picker"))
    ).scalar_one()
    assert event.event_type == "candidate_picked"
    assert event.outcome == f"{LIBRARY}/A Movie poster from TMDB"
    assert OFFERED_URL not in repr(event.payload)
    assert "/offered.png" not in repr(event.payload)
    assert event.payload["provider"] == "TMDB"
    assert event.payload["art_kind"] == "poster"


# --- download failures ------------------------------------------------------


@pytest.mark.parametrize("art_kind", ["poster", "logo"])
@pytest.mark.parametrize(
    "response_for, why",
    [
        (lambda request: httpx.Response(404), "an upstream 404"),
        (_ok(b"<!doctype html>", "text/html"), "a content type outside the allowlist"),
        (_ok(b"", "image/png"), "an empty body"),
        (_ok(b"not actually a png", "image/png"), "bytes no decoder accepts"),
    ],
)
async def test_a_failed_download_is_502_and_writes_nothing(
    client, auth_headers, session, manual_root, transport, response_for, why, art_kind
):
    """File-success-first, exactly as clear-override orders it: nulled
    fingerprints with no file behind them would re-render straight back to the
    provider's automatic pick while the UI claimed the operator's choice took.

    Both kinds, because they are defended differently. A picked poster is
    decoded and re-encoded, so bad bytes are caught by the transcode whatever
    the response headers said; a picked logo is written through untouched, so
    the status, Content-Type and size checks in ``_download_artwork`` are the
    only thing standing between an HTML error page and a file called
    ``logo.png`` that the compositor will later be handed.
    """
    transport._response_for = response_for
    item_id = await _item(session)
    url = OFFERED_URL if art_kind == "poster" else OFFERED_LOGO_URL

    response = await _pick(client, item_id, art_kind, auth_headers, url=url)

    assert response.status_code == 502, why
    assert not _mirror(manual_root).exists()
    assert list(manual_root.rglob("*")) == []
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert render.badge_fingerprint == "b" * 64
    assert (await session.execute(select(Job))).scalars().all() == []


async def test_a_perfectly_good_image_served_with_an_error_status_is_refused(
    client, auth_headers, session, manual_root, transport
):
    """The status check earns its keep only here. Every other failure in the
    table above is also caught by the decode, so a 404 whose body happens to be
    a real image -- an upstream's own "not found" graphic, which is a thing
    image hosts serve -- is the case that separates checking the status from
    trusting the bytes."""
    transport._response_for = lambda request: httpx.Response(
        404, content=PNG_BYTES, headers={"content-type": "image/png"}
    )
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    assert list(manual_root.rglob("*")) == []


async def test_a_decodable_image_outside_the_allowlist_is_refused(
    client, auth_headers, session, manual_root, transport
):
    """And this is the case that separates the Content-Type allowlist from the
    decode: a GIF decodes perfectly well, and a picked logo is written through
    untranscoded, so without the allowlist a GIF lands on the mount named
    ``logo.png`` -- a file whose name and contents disagree, which is exactly
    what api/artwork.py's allowlist exists downstream to prevent."""
    buffer = io.BytesIO()
    Image.new("P", (4, 6)).save(buffer, format="GIF")
    transport._response_for = _ok(buffer.getvalue(), "image/gif")
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert response.status_code == 502
    assert list(manual_root.rglob("*")) == []


async def test_a_zero_length_body_is_refused_by_the_download_itself(tmp_path):
    """The size check, at the helper, because nothing above it can reach it:
    an empty file also fails to decode, so an endpoint-level test would pass
    against an implementation that had no size check at all."""
    from autoposter.api.candidates import DownloadRefused, _download_artwork

    async def handler(request):
        return httpx.Response(200, content=b"", headers={"content-type": "image/png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(DownloadRefused):
            await _download_artwork(http, OFFERED_URL, tmp_path / "picked")


async def test_the_502_names_the_provider_and_never_the_url(
    client, auth_headers, session, transport
):
    """httpx puts the full request URL in an ``HTTPStatusError``'s message and
    Fanart passes its API key as a query parameter, so ``str(exc)`` in a detail
    is a credential leak. The provider's name is the whole message."""
    transport._response_for = _ok(b"<!doctype html>", "text/html")
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "TMDB" in detail
    assert "image.tmdb.org" not in detail
    assert "offered.png" not in detail


async def test_a_stream_beyond_the_size_cap_is_502_and_writes_nothing(
    client, auth_headers, session, manual_root, transport
):
    """The stream is now reachable from an authenticated request rather than
    only from a provider client, so it needs its own ceiling: a body that
    never stops arriving must not fill the container's tmpdir."""
    from autoposter.api.candidates import PICK_MAX_BYTES

    transport._response_for = _ok(b"\x00" * (PICK_MAX_BYTES + 1), "image/png")
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    assert list(manual_root.rglob("*")) == []


async def test_a_decompression_bomb_is_refused_not_500ed(
    client, auth_headers, session, manual_root, monkeypatch
):
    """``Image.DecompressionBombError`` subclasses ``Exception`` directly, not
    ``OSError`` or ``ValueError``, so it escapes the transcode's except clause
    unless named explicitly -- and an uncaught exception here would 500 an
    authenticated operator request instead of refusing the image."""
    import autoposter.api.candidates as candidates_module

    def explode(*args, **kwargs):
        raise Image.DecompressionBombError("image too large")

    monkeypatch.setattr(candidates_module.Image, "open", explode)
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    assert list(manual_root.rglob("*")) == []


async def test_a_failed_mount_write_is_503_and_leaks_no_path(
    client, auth_headers, session, manual_root, monkeypatch
):
    """A read-only mount, simulated the same way clear-override's test does.
    The detail is sanitized: ``os.replace``'s ``OSError`` carries the
    destination's full filesystem path, and this is the one response in this
    file that would otherwise leak the server's directory layout."""
    item_id = await _item(session)

    def refuse(src, dst):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(os, "replace", refuse)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 503
    assert response.json()["detail"] == "could not write to the override mount"
    assert not _mirror(manual_root).exists()
    render = (
        await session.execute(select(Render).where(Render.item_id == item_id))
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint == "f" * 64
    assert render.badge_fingerprint == "b" * 64
    assert (await session.execute(select(Job))).scalars().all() == []


# --- logos ------------------------------------------------------------------


async def test_a_logo_pick_writes_the_original_bytes_beside_the_poster(
    client, auth_headers, session, manual_root, transport
):
    """No transcode: a logo is composited over the poster and needs its alpha
    channel, which a JPEG cannot carry. The suffix comes from the URL, with
    ``.png`` as the default -- the same rule the render path already uses when
    it stages a fetched logo (render/pipeline.py)."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert response.status_code == 200
    logo = _mirror(manual_root, "logo.png")
    assert logo.read_bytes() == PNG_BYTES
    with Image.open(logo) as image:
        assert image.mode == "RGBA"


async def test_a_logo_pick_nulls_the_poster_row_because_a_logo_is_a_poster_input(
    client, auth_headers, session
):
    """There is no ``logo`` render row and there never will be -- the logo
    rides into the POSTER's fingerprint as ``logo_sha``. Nulling nothing (or
    nulling a row that does not exist) leaves the poster reporting
    "unchanged" for ever."""
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert response.json() == {"status": "picked", "queued": True}
    render = (
        await session.execute(
            select(Render).where(Render.item_id == item_id, Render.art_kind == "poster")
        )
    ).scalar_one()
    await session.refresh(render)
    assert render.fingerprint is None
    assert render.badge_fingerprint is None


async def test_a_logo_pick_does_not_write_the_poster_mirror(
    client, auth_headers, session, manual_root
):
    """``logo.png`` beside the poster, not ``poster.jpg`` -- a logo written to
    the poster's own override path replaces the artwork with its own logo."""
    item_id = await _item(session)

    await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert not _mirror(manual_root).exists()


async def test_a_logo_url_with_no_suffix_defaults_to_png(
    client, auth_headers, session, app, manual_root
):
    bare = "https://assets.fanart.tv/logo/12345"
    app.state.providers = [
        _offering("Fanart", "logo", [ArtCandidate("Fanart", bare, "en", 800, 300, 9.0)]),
    ]
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, provider="Fanart", url=bare)

    assert response.status_code == 200
    assert _mirror(manual_root, "logo.png").read_bytes() == PNG_BYTES


async def test_a_logo_pick_keeps_a_webp_suffix(client, auth_headers, session, app, manual_root):
    webp = "https://assets.fanart.tv/logo/12345.webp"
    app.state.providers = [
        _offering("Fanart", "logo", [ArtCandidate("Fanart", webp, "en", 800, 300, 9.0)]),
    ]
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, provider="Fanart", url=webp)

    assert response.status_code == 200
    assert _mirror(manual_root, "logo.webp").exists()


async def test_the_picked_logo_is_what_the_pipeline_will_hash(
    client, auth_headers, session, manual_root
):
    """The bridge to the render side: the file this endpoint writes is the one
    ``render_artifact`` stages as the logo, and its sha is what rides into the
    poster fingerprint (see tests/test_pipeline.py)."""
    item_id = await _item(session)

    await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    logo = _mirror(manual_root, "logo.png")
    assert hashlib.sha256(logo.read_bytes()).hexdigest() == (
        hashlib.sha256(PNG_BYTES).hexdigest()
    )


async def test_a_new_logo_suffix_replaces_the_old_one_instead_of_being_shadowed(
    client, auth_headers, session, app, manual_root
):
    """``find_logo_override`` returns the FIRST existing suffix in
    ``LOGO_OVERRIDE_SUFFIXES`` order (.png first). Picking a PNG logo and then
    a WebP one must not leave the stale ``logo.png`` on the mount -- it would
    keep winning over the operator's newer pick forever."""
    from autoposter.config.loader import load_config
    from autoposter.plex.client import ResolvedItem
    from autoposter.render.pipeline import find_logo_override

    item_id = await _item(session)

    first = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)
    assert first.status_code == 200
    assert _mirror(manual_root, "logo.png").exists()

    webp_url = "https://assets.fanart.tv/logo/offered.webp"
    app.state.providers = [
        _offering("Fanart", "logo", [ArtCandidate("Fanart", webp_url, "en", 800, 300, 9.0)]),
    ]
    second = await _pick(client, item_id, "logo", auth_headers, provider="Fanart", url=webp_url)
    assert second.status_code == 200

    assert not _mirror(manual_root, "logo.png").exists()
    assert _mirror(manual_root, "logo.webp").exists()

    config = load_config(EXAMPLE).model_copy(update={"manual_assets_root": manual_root})
    resolved = ResolvedItem(
        server="plex", native_id="rk1", library=LIBRARY, kind="movie", title="A Movie", year=1999,
        season_number=None, episode_number=None, root_folder=ROOT_FOLDER, file_path=None,
        art_url=None, tmdb_id=TMDB_ID, tvdb_id=660, imdb_id="tt0137523",
    )
    assert find_logo_override(config, resolved) == _mirror(manual_root, "logo.webp")


async def test_a_malformed_claimed_url_is_422_not_500(
    client, auth_headers, session
):
    """The refusal path logs ``httpx.URL(body.url).host`` before rejecting an
    unoffered claim. ``httpx.InvalidURL`` (e.g. an unparsable IPv6 host like
    "http://[::g]") must not escape as a 500 -- and the caller's string must
    not reach the response."""
    item_id = await _item(session)
    malformed = "http://[::g]"

    response = await _pick(client, item_id, "poster", auth_headers, url=malformed)

    assert response.status_code == 422
    assert malformed not in response.text
    assert "[" not in response.text


async def test_a_poison_shape_logo_is_refused_not_written_through(
    client, auth_headers, session, manual_root, transport
):
    """The picked-logo path's own guard, not the transcode's.

    A picked logo is written through untouched (no ``_prepare_jpeg`` decode),
    so this endpoint's only defence against the 'Inside Out 2' poison shape --
    a PNG whose header parses cleanly and whose IDAT stream contradicts it --
    is ``_verify_image``. Pillow's ``Image.verify()`` is proven (by this same
    branch's own fixture, ``tests/test_render_input_guard.py``) to pass this
    exact shape; only a full decode via ``load()`` catches it. Refused here
    means it never reaches ``logo.png`` on the manual mount, from which
    ``_stage_override`` would hand it to the compositor untouched on the next
    render.
    """
    from test_render_input_guard import corrupt_png

    transport._response_for = _ok(corrupt_png())
    item_id = await _item(session)

    response = await _pick(client, item_id, "logo", auth_headers, url=OFFERED_LOGO_URL)

    assert response.status_code == 502
    assert list(manual_root.rglob("*")) == []


async def test_a_truncated_jpeg_is_502_not_written_through(
    client, auth_headers, session, manual_root, transport
):
    """Every non-JPEG branch of ``_prepare_jpeg`` fully decodes via
    ``image.load()``/``convert()``; the JPEG branch only parsed a header
    before this fix, so a real JPEG header glued to a truncated body would
    pass through untouched and reach the mirror as a broken file.

    Chopped off the end rather than the middle: cutting into the header
    segments themselves fails ``Image.open`` outright, which every branch
    already handles -- the gap this guards is a header that parses fine
    followed by entropy-coded data that stops short."""
    real_jpeg = _jpeg(size=(200, 200))
    truncated = real_jpeg[:-50]
    transport._response_for = _ok(truncated, "image/jpeg")
    item_id = await _item(session)

    response = await _pick(client, item_id, "poster", auth_headers)

    assert response.status_code == 502
    assert not _mirror(manual_root).exists()
    assert list(manual_root.rglob("*")) == []
