"""What the render path refuses to download, and what it refuses to composite.

Until this guard existed, ``render/pipeline._download`` streamed a provider's
body to disk with no byte cap, no decode, and no ceiling on the pixel
dimensions that reached ImageMagick -- and ``api/candidates._download_artwork``
said so in its own docstring, since it was written as "``pipeline._download``'s
streaming shape, **plus the checks it lacks**".

The live case is production job 40478, the movie 'Inside Out 2'. Its clearlogo
downloads as a PNG whose header is valid and whose IDAT stream contradicts it;
ImageMagick's report is ``IDAT: Too much image data``. Before the infra layer's
``MAGICK_*`` caps that image allocated ~6 GiB inside ``magick`` and OOMKilled
the pod; after them it failed as an opaque ``RuntimeError: magick failed (1)``
from ``compositor.run``, blaming the compositor for a file it should never have
been handed. Both are the same defect: a poisoned input accepted at download
time.

So the assertions here are about *where* and *how* a bad source is refused --
at the download, by name, before any ``magick`` process starts -- and, just as
importantly, that a good source is entirely unaffected: the fingerprint law
says a healthy render's bytes must be identical to what it produced before any
of this existed.
"""

import hashlib
import io
import logging
import random
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import httpx
import pytest
from conftest import decodable_png
from PIL import Image

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.queue.worker import _served_reason
from autoposter.render import compositor
from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import (
    RENDER_MAX_BYTES,
    SourceRefused,
    _stage_override,
    render_artifact,
)
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
GOLDEN = Path(__file__).parent / "fixtures" / "golden"


def _config(tmp_path):
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    return config


def _item():
    return ResolvedItem(
        server="plex", native_id="1", library="Movies", kind="movie",
        title="Inside Out 2", year=2024,
        season_number=None, episode_number=None, root_folder="Inside Out 2 (2024)",
        file_path="/mnt/Media/Movies/Inside Out 2 (2024)/x.mkv", art_url=None,
        tmdb_id=1022789, tvdb_id=None, imdb_id="tt22022452",
    )


class _Provider:
    """A poster candidate always; a logo candidate at whatever URL is asked for."""

    name = "TMDB"

    def __init__(self, logo_url: str | None = None):
        self._logo_url = logo_url

    async def fetch(self, request):
        if request.art_kind == "poster":
            return [ArtCandidate("TMDB", "https://img/poster.png", None, 2000, 3000, 5.0)]
        if request.art_kind == "logo" and self._logo_url is not None:
            return [ArtCandidate("TMDB", self._logo_url, "en", 800, 300, 5.0)]
        return []


def corrupt_png() -> bytes:
    """A PNG whose header parses cleanly and whose IDAT stream contradicts it.

    This is 'Inside Out 2's shape, not an approximation of it: a well-formed
    IHDR declaring one size over a compressed pixel stream carrying another,
    which is precisely the condition libpng reports as ``IDAT: Too much image
    data``. Built by rewriting the IHDR of a healthy image to claim a smaller
    one and fixing the chunk CRC, so nothing about the file is malformed --
    only inconsistent.

    Measured against the runtime image's own ImageMagick 7 and Pillow, this
    file behaves as follows, which is the whole argument for the guard being a
    full decode rather than a header check:

    ==========================  =======================================
    ``magick identify``         exit 0, reports ``PNG 200x200``
    Pillow ``Image.verify()``   passes
    Pillow ``Image.load()``     raises ``OSError``
    ``magick ... -set comment`` exit 1, ``bad adaptive filter value``
    ==========================  =======================================

    The last row is the production symptom (``RuntimeError: magick failed
    (1)`` from ``compositor.run``); the third is the only check that sees it
    coming.

    Noise pixels rather than a flat colour, so the IDAT stream is genuinely
    longer than the shrunken header claims -- a solid image compresses to a few
    hundred bytes and would decode into the smaller frame without complaint.

    Seeded rather than ``os.urandom``, so a red run is byte-reproducible.
    """
    buffer = io.BytesIO()
    noise = Image.frombytes(
        "RGB", (256, 256), random.Random(0).randbytes(256 * 256 * 3)
    )
    noise.save(buffer, format="PNG")
    healthy = bytearray(buffer.getvalue())

    # IHDR is the first chunk: [4-byte length][b"IHDR"][13-byte data][4-byte CRC],
    # and its data opens with the width and height as big-endian uint32s. The
    # CRC covers the type and the data, not the length.
    ihdr = healthy.index(b"IHDR")
    struct.pack_into(">II", healthy, ihdr + 4, 200, 200)
    crc = zlib.crc32(bytes(healthy[ihdr:ihdr + 4 + 13])) & 0xFFFFFFFF
    struct.pack_into(">I", healthy, ihdr + 4 + 13, crc)
    return bytes(healthy)


def _http_serving(body: bytes):
    async def handler(request):
        return httpx.Response(200, content=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _stub_out_imagemagick(monkeypatch):
    """Record every argv handed to ``compositor.run`` without running magick.

    Recording rather than discarding: several tests below assert that the
    refusal happened *before* the first magick call, and an empty list is the
    only honest way to say so.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(pipeline_module.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline_module, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    return calls


# --- the corrupt source, through the real render path ------------------------


async def test_a_corrupt_source_is_refused_before_any_magick_process_starts(
    session, tmp_path, monkeypatch
):
    """The 'Inside Out 2' failure, reproduced through ``render_artifact``.

    The distinction this pins is not "the job fails" -- it failed before, as
    ``RuntimeError: magick failed (1)``. It is that it fails *as a refusal of
    the download*, with nothing composited, which is what makes the error
    attributable to the provider's bytes instead of to the compositor.
    """
    config = _config(tmp_path)
    calls = _stub_out_imagemagick(monkeypatch)

    async with _http_serving(corrupt_png()) as http:
        with pytest.raises(SourceRefused):
            await render_artifact(
                session, config, http, _item(), "poster", [_Provider()],
            )

    assert calls == []


async def test_the_refusal_names_the_reason_and_the_stage(
    session, tmp_path, monkeypatch
):
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)

    async with _http_serving(corrupt_png()) as http:
        with pytest.raises(SourceRefused) as caught:
            await render_artifact(
                session, config, http, _item(), "poster", [_Provider()],
            )

    message = str(caught.value)
    assert "poster" in message
    assert "decode" in message


async def test_a_corrupt_clearlogo_is_skipped_and_the_poster_renders_without_one(
    session, tmp_path, monkeypatch, caplog
):
    """The logo is a second download, and it is the one that fails -- but a
    logo failing no longer refuses the whole poster (Task 1, ``_pick_logo``):
    the ladder is re-asked, this provider has nothing else to offer, and the
    poster renders WITHOUT a logo rather than being refused. The refusal
    still names the clearlogo rather than the poster -- just in a WARNING
    now, not in the outcome, which is what still points an operator at the
    right file.
    """
    config = _config(tmp_path)
    assert config.artwork.use_logo is True
    calls = _stub_out_imagemagick(monkeypatch)
    monkeypatch.setattr(
        pipeline_module.compositor, "build_logo_argv",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no logo should have been composited")
        ),
    )
    healthy = decodable_png()
    broken = corrupt_png()

    async def handler(request):
        return httpx.Response(200, content=broken if "logo" in str(request.url) else healthy)

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            render = await render_artifact(
                session, config, http, _item(), "poster",
                [_Provider(logo_url="https://img/logo.png")],
            )

    assert render.status == "rendered"
    assert calls != [], "the poster itself must still be composited"
    refusal = next(
        record.getMessage() for record in caplog.records
        if "clearlogo candidate refused" in record.getMessage()
    )
    assert "clearlogo" in refusal
    assert "decode" in refusal
    assert "https://" not in refusal


def test_the_refusal_serves_its_full_reason_class_prefixed():
    """Roadmap row 213 (the Failures-naming fix): ``job.last_error`` is a
    SERVED column, and an operator reading it could until now tell only that
    *something* was refused, never what or why.

    ``SourceRefused`` now carries ``served_detail = True`` -- every raise site
    was audited and interpolates only a stage label and facts about the
    downloaded bytes (dimensions, byte count, the decode exception's class
    name), never a URL, a filesystem path or a token -- so the queue's
    ``_served_reason`` serves the class-prefixed message whole. The full text
    reaches the pod log too, at ``run_once``'s ``logger.warning(...,
    exc_info=True)``.
    """
    exc = SourceRefused("the poster source did not decode after download (OSError)")

    assert (
        _served_reason(exc)
        == "SourceRefused: the poster source did not decode after download (OSError)"
    )
    assert getattr(exc, "served_detail", False) is True


@pytest.mark.imagemagick
def test_the_corrupt_fixture_is_exactly_what_a_header_check_misses(tmp_path):
    """The premise, asserted rather than assumed.

    If a future Pillow or ImageMagick changed any of these, the guard above
    would still pass its own tests while protecting against nothing: a fixture
    ``identify`` rejected would prove only that the cheap check was enough, and
    a fixture ``magick`` composited happily would prove there was nothing to
    refuse. Both halves are pinned here, on a real binary.
    """
    config = load_config(EXAMPLE)
    source = tmp_path / "bad.png"
    source.write_bytes(corrupt_png())

    identified = subprocess.run(
        [config.magick_binary, "identify", str(source)], capture_output=True, text=True
    )
    assert identified.returncode == 0, "a header parse was expected to accept this file"

    with Image.open(source) as image:
        image.verify()  # also a header parse, and it must not raise

    with pytest.raises(OSError):
        with Image.open(source) as image:
            image.load()

    with pytest.raises(RuntimeError, match="magick failed"):
        compositor.run(compositor.build_stamp_argv(config.magick_binary, str(source)))


# --- the byte cap ------------------------------------------------------------


async def test_an_oversized_body_is_aborted_mid_stream_and_leaves_no_file(tmp_path):
    """The cap is counted chunk by chunk, and the partial file does not survive.

    ``Content-Length`` is not consulted on purpose: it is the other end's
    claim, and a body that simply never stops arriving sends none. So the
    proof that this is a real abort is that the server was never asked for all
    of its chunks.
    """
    chunk = b"\0" * (1024 * 1024)
    total_chunks = (RENDER_MAX_BYTES // len(chunk)) * 4
    served: list[int] = []

    async def handler(request):
        async def body():
            for index in range(total_chunks):
                served.append(index)
                yield chunk

        return httpx.Response(200, content=body())

    destination = tmp_path / "poster.png"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SourceRefused) as caught:
            await pipeline_module._download(
                http, "https://img/huge.png", destination, stage="the poster source"
            )

    assert "cap" in str(caught.value)
    assert not destination.exists()
    assert len(served) * len(chunk) <= RENDER_MAX_BYTES + len(chunk)


async def test_a_body_inside_the_cap_is_kept(tmp_path):
    """The cap has to be a ceiling, not a policy against large artwork."""
    destination = tmp_path / "poster.png"
    body = decodable_png(size=(64, 96))

    async with _http_serving(body) as http:
        digest = await pipeline_module._download(
            http, "https://img/poster.png", destination, stage="the poster source"
        )

    assert destination.read_bytes() == body
    assert digest == hashlib.sha256(body).hexdigest()


def test_an_oversized_manual_override_is_refused_before_it_is_read(tmp_path):
    """Roadmap row 238, surface 3. The one door into the render path with no
    byte cap on it at all.

    ``RENDER_MAX_BYTES`` bounds what a PROVIDER may hand ImageMagick and
    ``PICK_MAX_BYTES`` bounds what the API's own upload may write, but a file
    an operator drops straight onto the ``manual_assets_root`` NFS mount
    reaches ``_stage_override`` past both -- ``manual_override_path`` and
    ``find_logo_override`` do a bare ``.exists()``. Held to the same ceiling a
    downloaded source is held to, and refused with the same exception, so the
    render fails with a served reason instead of costing the pod its memory.

    The file is SPARSE (``truncate``, no bytes written), which is also the
    proof that the check is a ``stat()`` and not a read: a 50 MiB read would
    not care that the extents are holes, and this test would be slow.

    Row 213: the served detail names the stage and the byte counts, and must
    never name the operator's path.
    """
    override = tmp_path / "poster.jpg"
    with override.open("wb") as handle:
        handle.truncate(RENDER_MAX_BYTES + 1)
    working = tmp_path / "working.jpg"

    with pytest.raises(SourceRefused) as excinfo:
        _stage_override(override, working, stage="the poster source")

    detail = str(excinfo.value)
    assert "the poster source" in detail
    assert str(RENDER_MAX_BYTES) in detail
    assert str(override) not in detail
    assert override.name not in detail
    assert not working.exists(), "a refused override must not have been copied"


# --- the pixel ceiling (M1) ---------------------------------------------------


def _oversized_declared_png(width: int, height: int) -> bytes:
    """A PNG whose IHDR declares ``width``x``height`` over a tiny real image.

    Same technique as ``corrupt_png``: ``Image.open`` reads only the header
    lazily, and ``_validate_image``'s pixel ceiling is checked from that
    header *before* ``.load()`` ever decodes a pixel -- so this fixture never
    needs to actually contain that many pixels to prove the ceiling refuses
    them. Rewriting a 4x4 PNG's IHDR keeps this cheap regardless of how large
    a ceiling it needs to exceed.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buffer, format="PNG")
    raw = bytearray(buffer.getvalue())
    ihdr = raw.index(b"IHDR")
    struct.pack_into(">II", raw, ihdr + 4, width, height)
    crc = zlib.crc32(bytes(raw[ihdr:ihdr + 4 + 13])) & 0xFFFFFFFF
    struct.pack_into(">I", raw, ihdr + 4 + 13, crc)
    return bytes(raw)


def test_a_source_declaring_more_than_the_pixel_ceiling_is_refused(tmp_path):
    """The declared ceiling refuses before ``.load()`` is ever reached: this
    fixture's declared size (100,000,000px) does not match its real 4x4
    content, so a refusal that fell through to the decode would raise for the
    wrong reason (a row-stride mismatch) rather than naming the pixel count.
    """
    source = tmp_path / "huge.png"
    source.write_bytes(_oversized_declared_png(10_000, 10_000))

    with pytest.raises(SourceRefused) as caught:
        pipeline_module._validate_image(source, "the poster source")

    assert "10000x10000" in str(caught.value)
    assert str(pipeline_module._ARTWORK_MAX_PIXELS) in str(caught.value)


def test_the_pixel_ceiling_is_checked_before_load(tmp_path, monkeypatch):
    """``image.size`` is header-derived and read before ``.load()`` allocates a
    decode buffer -- the whole point of checking it here rather than after.
    Pinned by making ``.load()`` itself fail the test if it is ever called.

    The fixture is built (which itself calls ``.load()`` internally, via
    ``Image.save``) before the patch is installed, so only the call inside
    ``_validate_image`` is under test.
    """
    source = tmp_path / "huge.png"
    source.write_bytes(_oversized_declared_png(10_000, 10_000))

    def explode(self):
        raise AssertionError("load() must not run above the pixel ceiling")

    monkeypatch.setattr(Image.Image, "load", explode)

    with pytest.raises(SourceRefused):
        pipeline_module._validate_image(source, "the poster source")


def test_a_source_comfortably_under_the_pixel_ceiling_is_accepted(tmp_path):
    """The other half: a real, decodable image well under the ceiling -- large
    next to any real poster, tiny in bytes because a solid colour compresses
    to almost nothing -- must not be refused by it.
    """
    source = tmp_path / "big.png"
    buffer = io.BytesIO()
    Image.new("RGB", (4000, 4000), (10, 20, 30)).save(buffer, format="PNG")
    source.write_bytes(buffer.getvalue())

    pipeline_module._validate_image(source, "the poster source")  # must not raise


# --- the deliberate hole ------------------------------------------------------


async def test_an_svg_clearlogo_is_not_refused(session, tmp_path, monkeypatch):
    """Pillow has no SVG decoder and fanart.tv genuinely serves SVG clearlogos.

    ``compositor.build_logo_argv`` has a ``-density 300`` branch for exactly
    those, so refusing what Pillow cannot read would refuse artwork that
    renders correctly today. The rasterisation of an SVG remains unbounded --
    that is named in the OOM investigation and is not this layer's fix.
    """
    config = _config(tmp_path)
    calls = _stub_out_imagemagick(monkeypatch)
    healthy = decodable_png()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="300"/>'

    async def handler(request):
        return httpx.Response(200, content=svg if "logo" in str(request.url) else healthy)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        render = await render_artifact(
            session, config, http, _item(), "poster",
            [_Provider(logo_url="https://img/logo.svg")],
        )

    assert render.status == "rendered"
    assert any("-density" in call for call in calls)


async def test_a_corrupt_body_at_an_svg_clearlogo_url_is_skipped_not_composited(
    session, tmp_path, monkeypatch, caplog
):
    """SVG-ness is decided by the bytes, not by a substring of the provider's
    URL (H1). Job 40478, the incident this whole guard exists for, was a
    clearlogo -- so any body a provider serves at a URL ending ``.svg`` must
    still be decoded and refused if it is not an SVG document, rather than
    skipping validation on the strength of a filename the provider chose.

    A refused clearlogo no longer refuses the poster (Task 1): it is skipped,
    the ladder has nothing else to offer, and the poster renders without a
    logo -- so ``calls`` is no longer empty, only free of a logo composite.
    """
    config = _config(tmp_path)
    calls = _stub_out_imagemagick(monkeypatch)
    monkeypatch.setattr(
        pipeline_module.compositor, "build_logo_argv",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("no logo should have been composited")
        ),
    )
    healthy = decodable_png()
    broken = corrupt_png()

    async def handler(request):
        return httpx.Response(200, content=broken if "logo" in str(request.url) else healthy)

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            render = await render_artifact(
                session, config, http, _item(), "poster",
                [_Provider(logo_url="https://img/logo.svg")],
            )

    assert render.status == "rendered"
    assert calls != [], "the poster itself must still be composited"
    refusal = next(
        record.getMessage() for record in caplog.records
        if "clearlogo candidate refused" in record.getMessage()
    )
    assert "clearlogo" in refusal
    assert "decode" in refusal
    assert "https://" not in refusal


# --- the fingerprint law ------------------------------------------------------


async def test_a_healthy_render_is_byte_for_byte_what_it_always_was(
    session, tmp_path, monkeypatch
):
    """Nothing above may touch the pixels of a source that passes.

    The validation decodes into a throwaway Pillow image and writes nothing
    back, so the published artifact is still exactly the bytes the provider
    served -- which is what keeps every stored ``render.fingerprint`` valid
    across this change instead of triggering a library-wide re-render.
    """
    config = _config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    body = decodable_png(size=(120, 180))

    async with _http_serving(body) as http:
        render = await render_artifact(
            session, config, http, _item(), "poster", [_Provider()],
        )

    assert render.status == "rendered"
    published = Path(render.asset_path)
    assert hashlib.sha256(published.read_bytes()).hexdigest() == (
        hashlib.sha256(body).hexdigest()
    )


@pytest.mark.imagemagick
def test_the_stamp_bound_leaves_a_normal_sized_source_byte_identical(tmp_path):
    """``-resize WxH>`` on a source already inside the box is a no-op.

    Run against the harvested production poster the golden test uses, so this
    is a claim about real artwork rather than a synthetic image: the stamped
    output must be byte-identical to what the pre-bound argv produced, or
    ``tests/test_golden.py``'s byte-exact parity is no longer true and every
    asset in the existing library would re-render.
    """
    config = load_config(EXAMPLE)
    source = GOLDEN / "source_textless.jpg"

    bounded = tmp_path / "bounded.jpg"
    unbounded = tmp_path / "unbounded.jpg"
    shutil.copy(source, bounded)
    shutil.copy(source, unbounded)

    compositor.run(compositor.build_stamp_argv(config.magick_binary, str(bounded)))
    # The argv exactly as it stood before the bound was added.
    compositor.run([
        config.magick_binary, str(unbounded),
        "-set", "comment", compositor.PROVENANCE_COMMENT,
        str(unbounded),
    ])

    assert bounded.read_bytes() == unbounded.read_bytes()


@pytest.mark.imagemagick
def test_the_stamp_bound_shrinks_a_source_larger_than_every_canvas(tmp_path):
    """The other half: something above the box really is brought inside it.

    Without this the ``>`` could be a typo that silently disables the bound and
    the byte-identity test above would still pass.
    """
    config = load_config(EXAMPLE)
    oversized = tmp_path / "huge.png"
    subprocess.run(
        [config.magick_binary, "-size", "8000x1000", "xc:red", str(oversized)],
        check=True, capture_output=True,
    )

    compositor.run(compositor.build_stamp_argv(config.magick_binary, str(oversized)))

    identified = subprocess.run(
        [config.magick_binary, "identify", "-format", "%wx%h", str(oversized)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    width, height = (int(n) for n in identified.split("x"))
    limit_w, limit_h = (int(n) for n in compositor.STAMP_MAX_GEOMETRY.split("x"))
    assert width <= limit_w
    assert height <= limit_h
