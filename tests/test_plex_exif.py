"""EXIF provenance: writing it into badged WebPs and reading it back from Plex.

``read_exif_from_tail``/``fetch_exif_tail`` are exercised against
``tests/fixtures/oracle/All_Souls_plex_overlaid.jpg`` -- a real production
WebP -- so recovering the overlay marker from it proves the parser against
the real thing, not a synthetic fixture.
"""
import os
from pathlib import Path

import httpx

from autoposter.badges.compose import BadgeInputs, compose
from autoposter.badges.values import MediaInfo
from autoposter.plex.artwork import artwork_provenance
from autoposter.plex.exif import (
    OVERLAY_TAG,
    PROVENANCE_TAG,
    fetch_exif_tail,
    format_provenance,
    parse_provenance,
    read_exif_from_tail,
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


# --- artwork_provenance -------------------------------------------------------


class _FakeItem:
    def __init__(self, thumb):
        self.thumb = thumb


async def test_artwork_provenance_reads_the_fingerprint_off_the_selected_thumb():
    async def handler(request):
        assert request.url.path == "/library/metadata/1/thumb/1"
        data = _oracle_bytes()
        return httpx.Response(206, content=data[-4096:])

    item = _FakeItem(thumb="/library/metadata/1/thumb/1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {"X-Plex-Token": "tok"})

    # The oracle fixture predates provenance stamping, so it carries the
    # overlay marker but no ImageDescription -- correctly None.
    assert result is None


async def test_artwork_provenance_returns_none_without_a_thumb():
    item = _FakeItem(thumb=None)

    async def handler(request):
        raise AssertionError("must not request when there is no thumb")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await artwork_provenance(http, item, "http://plex.local", {})

    assert result is None
