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
import os
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
from autoposter.render.pipeline import RENDER_MAX_BYTES, SourceRefused, render_artifact
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
        rating_key="1", library="Movies", kind="movie", title="Inside Out 2", year=2024,
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
    """
    buffer = io.BytesIO()
    noise = Image.frombytes("RGB", (256, 256), os.urandom(256 * 256 * 3))
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


async def test_a_corrupt_clearlogo_is_refused_and_says_so(
    session, tmp_path, monkeypatch
):
    """The logo is a second download, and it is the one that actually failed.

    Its refusal has to name the clearlogo rather than the poster, or the pod
    log points an operator at the wrong file.
    """
    config = _config(tmp_path)
    assert config.artwork.use_logo is True
    _stub_out_imagemagick(monkeypatch)
    healthy = decodable_png()
    broken = corrupt_png()

    async def handler(request):
        return httpx.Response(200, content=broken if "logo" in str(request.url) else healthy)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(SourceRefused) as caught:
            await render_artifact(
                session, config, http, _item(), "poster",
                [_Provider(logo_url="https://img/logo.png")],
            )

    assert "clearlogo" in str(caught.value)


def test_the_refusal_serves_its_class_name_and_nothing_else():
    """Roadmap row 209: ``job.last_error`` is a SERVED column.

    ``SourceRefused`` carries no ``served_detail``, so the queue's
    ``_served_reason`` reduces it to a bare class name; the message -- which
    names the art kind, and could name a provider path -- reaches the pod log
    instead, at ``run_once``'s ``logger.warning(..., exc_info=True)``.
    """
    exc = SourceRefused("the poster source did not decode after download (OSError)")

    assert _served_reason(exc) == "SourceRefused"
    assert getattr(exc, "served_detail", False) is False


@pytest.mark.imagemagick
def test_the_corrupt_fixture_is_exactly_what_a_header_check_misses(tmp_path):
    """The premise, asserted rather than assumed.

    If a future Pillow or ImageMagick changed any of these, the guard above
    would still pass its own tests while protecting against nothing: a fixture
    ``identify`` rejected would prove only that the cheap check was enough, and
    a fixture ``magick`` composited happily would prove there was nothing to
    refuse. Both halves are pinned here, on a real binary.
    """
    source = tmp_path / "bad.png"
    source.write_bytes(corrupt_png())

    identified = subprocess.run(
        ["magick", "identify", str(source)], capture_output=True, text=True
    )
    assert identified.returncode == 0, "a header parse was expected to accept this file"

    with Image.open(source) as image:
        image.verify()  # also a header parse, and it must not raise

    with pytest.raises(OSError):
        with Image.open(source) as image:
            image.load()

    with pytest.raises(RuntimeError, match="magick failed"):
        compositor.run(compositor.build_stamp_argv("magick", str(source)))


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
