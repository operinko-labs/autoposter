"""EXIF provenance: writing it into badged WebPs and reading it back from Plex.

``read_exif_from_tail``/``fetch_exif_tail`` are exercised against
``tests/fixtures/oracle/All_Souls_plex_overlaid.jpg`` -- a real production
WebP -- so recovering the overlay marker from it proves the parser against
the real thing, not a synthetic fixture.
"""
import io
import os
import threading
from pathlib import Path

import httpx
from PIL import Image

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.values import MediaInfo
from autoposter.plex.artwork import artwork_provenance
from autoposter.plex.exif import (
    OVERLAY_TAG,
    PROVENANCE_TAG,
    fetch_exif_tail,
    format_provenance,
    parse_provenance,
    probe_exif,
    read_exif_from_tail,
    webp_declares_exif,
)

ORACLE = Path("tests/fixtures/oracle")


def _inputs(**over):
    base = dict(
        media=MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), 1, 1),
        critic_rating=4.9, audience_rating=6.3, content_rating="17", video_format="WEB",
    )
    base.update(over)
    return BadgeInputs(**base)


# --- read_exif_from_tail -----------------------------------------------------


def test_recovers_the_overlay_marker_from_a_real_production_webp():
    data = (ORACLE / "All_Souls_plex_overlaid.jpg").read_bytes()
    tail = data[-4096:]
    assert read_exif_from_tail(tail) == {OVERLAY_TAG: "overlay"}


def test_returns_empty_for_random_bytes():
    assert read_exif_from_tail(os.urandom(512)) == {}


def test_returns_empty_for_a_truncated_chunk():
    # The "EXIF" fourcc is present, but there isn't even a full 4-byte
    # size field after it.
    tail = b"padding-before" + b"EXIF" + b"\x01\x00"
    assert read_exif_from_tail(tail) == {}


def test_returns_empty_when_declared_size_exceeds_what_remains():
    tail = b"padding-before" + b"EXIF" + (10_000).to_bytes(4, "little") + b"short-payload"
    assert read_exif_from_tail(tail) == {}


def test_returns_empty_when_no_exif_marker_is_present():
    assert read_exif_from_tail(b"no marker in here at all") == {}


# --- format_provenance / parse_provenance ------------------------------------


def test_format_and_parse_round_trip():
    value = format_provenance("abc123")
    assert parse_provenance(value) == "abc123"


def test_parse_returns_none_for_a_foreign_description():
    assert parse_provenance("some other tool wrote this") is None


def test_parse_returns_none_for_absent_value():
    assert parse_provenance(None) is None


# --- compose() stamps provenance, and the chunk stays last -------------------


def test_compose_round_trip_recovers_overlay_and_fingerprint():
    data = compose(
        ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs(), fingerprint="fp-abc123"
    )
    tags = read_exif_from_tail(data[-4096:])
    assert tags[OVERLAY_TAG] == "overlay"
    assert parse_provenance(tags[PROVENANCE_TAG]) == "fp-abc123"


def test_compose_without_a_fingerprint_stamps_no_provenance_tag():
    data = compose(ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs())
    tags = read_exif_from_tail(data[-4096:])
    assert tags[OVERLAY_TAG] == "overlay"
    assert PROVENANCE_TAG not in tags


def test_compose_output_keeps_the_exif_chunk_last():
    """Regression guard: if a Pillow change ever moved the EXIF chunk off the
    end, a tail read would silently start returning {} and every provenance
    check would answer "not ours" without any test failing to say why."""
    data = compose(
        ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs(), fingerprint="fp-abc123"
    )
    index = data.rfind(b"EXIF")
    assert index != -1
    size = int.from_bytes(data[index + 4:index + 8], "little")
    payload_end = index + 8 + size
    # RIFF chunks pad odd-length payloads to an even boundary with at most
    # one pad byte -- never more real content after the chunk.
    assert payload_end in (len(data), len(data) - 1)


# --- fetch_exif_tail ----------------------------------------------------------


def _oracle_bytes():
    return (ORACLE / "All_Souls_plex_overlaid.jpg").read_bytes()


async def test_fetch_exif_tail_sends_a_suffix_range_and_parses_a_206_response():
    seen_requests = []

    async def handler(request):
        seen_requests.append(request)
        data = _oracle_bytes()
        tail = data[-4096:]
        return httpx.Response(
            206,
            content=tail,
            headers={"Content-Range": f"bytes {len(data) - len(tail)}-{len(data) - 1}/{len(data)}"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tags = await fetch_exif_tail(
            http, "http://plex.local/library/metadata/1/thumb/1", {"X-Plex-Token": "tok"}
        )

    assert tags == {OVERLAY_TAG: "overlay"}
    assert len(seen_requests) == 1
    assert seen_requests[0].headers["Range"] == "bytes=-4096"
    assert seen_requests[0].headers["X-Plex-Token"] == "tok"


async def test_fetch_exif_tail_handles_a_server_that_ignores_range():
    async def handler(request):
        return httpx.Response(200, content=_oracle_bytes())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tags = await fetch_exif_tail(http, "http://plex.local/x", {})

    assert tags == {OVERLAY_TAG: "overlay"}


async def test_fetch_exif_tail_returns_empty_on_404():
    async def handler(request):
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tags = await fetch_exif_tail(http, "http://plex.local/x", {})

    assert tags == {}


# --- probe_exif: one request when a second cannot possibly help --------------


def _ranged(data, request):
    """Serve a byte range out of ``data`` the way Plex does."""
    header = request.headers.get("Range")
    if header is None:
        return httpx.Response(200, content=data)
    spec = header.removeprefix("bytes=")
    if spec.startswith("-"):
        chunk = data[-int(spec[1:]):]
    else:
        start, end = spec.split("-")
        chunk = data[int(start):int(end) + 1]
    return httpx.Response(206, content=chunk)


def _webp_without_exif():
    """A minimal plain WebP: ``VP8 `` at offset 12, so no VP8X, so no EXIF."""
    body = b"VP8 " + (8).to_bytes(4, "little") + b"\x00" * 8
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


def _webp_vp8x_flags(flags):
    """A VP8X-headed WebP whose flags byte is ``flags`` and which has no tail."""
    chunk = b"VP8X" + (10).to_bytes(4, "little") + bytes([flags]) + b"\x00" * 9
    return b"RIFF" + (len(chunk) + 4).to_bytes(4, "little") + b"WEBP" + chunk


def _jpeg_with_provenance():
    """A real JPEG carrying our ImageDescription in its header, where JPEGs put it."""
    exif = Image.Exif()
    exif[PROVENANCE_TAG] = format_provenance("fp-jpeg")
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


async def _probe(data, extra_handler=None):
    seen = []

    async def handler(request):
        seen.append(request)
        if extra_handler is not None:
            return extra_handler(request)
        return _ranged(data, request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tags = await probe_exif(http, "http://plex.local/thumb", {"X-Plex-Token": "tok"})
    return tags, seen


async def test_probe_reads_a_webp_tail_only_when_the_header_declares_exif():
    data = _oracle_bytes()
    assert data[12:16] == b"VP8X" and data[20] & 0x08  # the measured production case
    tags, seen = await _probe(data)

    assert tags == {OVERLAY_TAG: "overlay"}
    assert [r.headers["Range"] for r in seen] == ["bytes=0-4095", "bytes=-4096"]


async def test_probe_skips_the_tail_request_for_a_webp_with_the_exif_bit_clear():
    tags, seen = await _probe(_webp_vp8x_flags(0x00))

    assert tags == {}
    assert len(seen) == 1


async def test_probe_skips_the_tail_request_for_a_webp_with_no_vp8x_chunk():
    tags, seen = await _probe(_webp_without_exif())

    assert tags == {}
    assert len(seen) == 1


async def test_probe_reads_a_jpeg_straight_from_the_prefix_with_no_second_request():
    tags, seen = await _probe(_jpeg_with_provenance())

    assert parse_provenance(tags[PROVENANCE_TAG]) == "fp-jpeg"
    assert len(seen) == 1


async def test_probe_returns_empty_for_an_unknown_format():
    tags, seen = await _probe(b"GIF89a" + os.urandom(200))

    assert tags == {}
    assert len(seen) == 1


async def test_probe_returns_empty_for_a_truncated_webp_header():
    tags, _ = await _probe(b"RIFF\x04\x00\x00\x00WEBP")
    assert tags == {}


async def test_probe_returns_empty_on_an_error_status():
    tags, seen = await _probe(b"", extra_handler=lambda request: httpx.Response(404))

    assert tags == {}
    assert len(seen) == 1


async def test_probe_returns_empty_rather_than_raising_when_the_request_fails():
    def boom(request):
        raise httpx.ConnectError("connection refused")

    tags, _ = await _probe(b"", extra_handler=boom)
    assert tags == {}


async def test_probe_handles_a_server_that_ignores_range_entirely():
    data = _oracle_bytes()

    async def handler(request):
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        tags = await probe_exif(http, "http://plex.local/thumb", {})

    assert tags == {OVERLAY_TAG: "overlay"}


def test_webp_declares_exif_needs_the_flags_byte_to_be_present():
    assert webp_declares_exif(_webp_vp8x_flags(0x08)) is True
    assert webp_declares_exif(_webp_vp8x_flags(0x00)) is False
    assert webp_declares_exif(_webp_without_exif()) is False
    assert webp_declares_exif(b"RIFF\x00\x00\x00\x00WEBPVP8X") is False
    assert webp_declares_exif(b"") is False


# --- artwork_provenance -------------------------------------------------------


class _FakeItem:
    def __init__(self, thumb):
        self.thumb = thumb


async def test_artwork_provenance_reads_a_fingerprint_we_actually_stamped():
    """The positive path. This used to assert ``is None`` against the oracle
    fixture -- which predates provenance stamping and so has no
    ImageDescription at all -- meaning a read that always returned None would
    have passed."""
    data = compose(
        ORACLE / "All_Souls_base_no_overlay.jpg", "poster", _inputs(), fingerprint="fp-abc123"
    )

    async def handler(request):
        assert request.url.path == "/library/metadata/1/thumb/1"
        return _ranged(data, request)

    item = _FakeItem(thumb="/library/metadata/1/thumb/1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {"X-Plex-Token": "tok"})

    assert result == "fp-abc123"


async def test_artwork_provenance_is_none_for_artwork_nobody_stamped():
    data = _oracle_bytes()  # a real production WebP, overlaid but never stamped

    async def handler(request):
        return _ranged(data, request)

    item = _FakeItem(thumb="/library/metadata/1/thumb/1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {})

    assert result is None


async def test_artwork_provenance_returns_none_when_the_request_fails():
    """The docstring promises None "if the request fails"; nothing used to
    catch a transport error, so a Plex hiccup raised out of the badge stage."""

    async def handler(request):
        raise httpx.ConnectError("connection refused")

    item = _FakeItem(thumb="/library/metadata/1/thumb/1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {})

    assert result is None


async def test_artwork_provenance_returns_none_when_reading_the_thumb_raises():
    class _Exploding:
        @property
        def thumb(self):
            raise RuntimeError("plexapi reload failed")

    async def handler(request):
        raise AssertionError("must not request when the thumb cannot be read")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await artwork_provenance(http, _Exploding(), "http://plex.local", {}) is None


async def test_artwork_provenance_reads_the_thumb_off_the_event_loop():
    """``.thumb`` on a partial plexapi object can trigger a blocking reload GET,
    and this runs once per item across the library."""
    reads = []

    class _Recording:
        @property
        def thumb(self):
            reads.append(threading.current_thread())
            return None

    async def handler(request):
        raise AssertionError("must not request when there is no thumb")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await artwork_provenance(http, _Recording(), "http://plex.local", {}) is None

    assert reads and all(t is not threading.current_thread() for t in reads)


async def test_artwork_provenance_returns_none_without_a_thumb():
    item = _FakeItem(thumb=None)

    async def handler(request):
        raise AssertionError("must not request when there is no thumb")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {})

    assert result is None
