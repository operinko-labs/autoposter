"""The image-source ladder -- ours, per the plan's ladder table.

Kometa's precedence (probe section 1.2) is file > default/pmm > git > repo >
url > name fallback. `git` and `repo` do not exist for us; `default`/`pmm` is
our `builtin`, resolving against the tree probe section 6 corrects to
Kometa's OWN `defaults/overlays/images/` rather than the Default-Images repo.
"""
import io

import httpx
import pytest
from PIL import Image

from autoposter.overlays.schema import OverlayDefinition
from autoposter.overlays.sources import OverlaySourceError, resolve_image_path


@pytest.fixture
def overlays_root(tmp_path):
    root = tmp_path / "overlays"
    root.mkdir()
    return root


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _tiny_png() -> bytes:
    """A genuinely decodable PNG body.

    Deviation from the brief: its own fixture (a PNG signature followed by
    64 zero bytes) has no valid IHDR chunk and never decoded -- it only
    passed because the brief's ladder predates render/pipeline.py's #131
    full-decode validation (see `resolve_image_path`'s new
    `_validate_downloaded_image` step). A real image is needed here so this
    test still proves what it says it proves: a cache hit that skips the
    second request, not a decode refusal that happens to also skip it.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_a_file_source_resolves_under_overlays_root(overlays_root):
    (overlays_root / "mine.png").write_bytes(b"x")
    path = await resolve_image_path(
        OverlayDefinition(name="o", file="mine.png"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path == overlays_root / "mine.png"


async def test_a_file_source_cannot_escape_overlays_root(overlays_root):
    """An operator-typed path is not a licence to read the filesystem."""
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", file="../../etc/passwd"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_builtin_source_resolves_against_the_bundled_tree(overlays_root):
    """Probe section 6: this tree is Kometa's own defaults/overlays/images/,
    NOT the Default-Images repo. `.png` is appended when missing (probe
    section 1.2)."""
    path = await resolve_image_path(
        OverlayDefinition(name="o", builtin="Commonsense"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path is not None and path.name == "Commonsense.png"
    assert path.exists()


async def test_a_builtin_source_cannot_escape_the_bundled_tree(overlays_root):
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", builtin="../../../etc/passwd"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_missing_builtin_is_an_error_not_a_silent_skip(overlays_root):
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", builtin="no-such-stamp"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_url_source_is_downloaded_and_cached_by_url(overlays_root, monkeypatch):
    """Adjudication A7: content-addressed, so a second call makes no request.

    `guarded_download` resolves the host for real before any request
    (`net/guard.py::validate_target` -> `resolve_host`), and `no_outbound_network`
    (conftest.py) only patches `httpx.AsyncHTTPTransport.handle_async_request`,
    not `getaddrinfo` -- so `example.com` would otherwise hit real DNS. Patched
    per `tests/test_fetch_guard.py`'s own precedent (its `resolve_host` patches).
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, content=_tiny_png(), headers={"content-type": "image/png"},
        )

    definition = OverlayDefinition(name="o", url="https://example.com/a.png")
    async with _client(handler) as http:
        first = await resolve_image_path(
            definition, overlays_root=overlays_root, http=http, max_bytes=1000
        )
        second = await resolve_image_path(
            definition, overlays_root=overlays_root, http=http, max_bytes=1000
        )
    assert first == second
    assert first.parent == overlays_root / ".cache"
    assert len(calls) == 1, "the second resolve must not re-request"


async def test_a_url_answering_with_the_wrong_content_type_is_refused(overlays_root, monkeypatch):
    """Probe section 1.2: Kometa validates Content-Type == image/png and
    rejects anything else. net/guard.py's content_types allowlist carries it.

    Same DNS concern and same fix as the test above: `example.com` is
    resolved for real by `validate_target` before the request, so
    `guard.resolve_host` is patched per `tests/test_fetch_guard.py`'s
    precedent.
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )

    def handler(request):
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                OverlayDefinition(name="o", url="https://example.com/a.png"),
                overlays_root=overlays_root, http=http, max_bytes=1000,
            )


async def test_a_url_pointing_at_a_private_address_is_never_requested(overlays_root):
    """net/guard.py's SSRF guard. A definition is operator-typed config, so
    this is the same threat model api/candidates.py already has."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x", headers={"content-type": "image/png"})

    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                OverlayDefinition(name="o", url="http://169.254.169.254/latest/meta-data"),
                overlays_root=overlays_root, http=http, max_bytes=1000,
            )
    assert calls == [], "the guard must refuse before any request is made"


async def test_no_source_falls_back_to_the_name_keyed_file(overlays_root):
    """Probe section 1.2 step 6."""
    (overlays_root / "mystamp.png").write_bytes(b"x")
    path = await resolve_image_path(
        OverlayDefinition(name="mystamp"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path == overlays_root / "mystamp.png"


async def test_a_text_overlay_with_no_source_and_no_file_resolves_to_nothing(overlays_root):
    """A text overlay need not carry an image at all."""
    assert await resolve_image_path(
        OverlayDefinition(name="text(hello)"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    ) is None


async def test_a_downloaded_image_that_fails_to_decode_is_refused(overlays_root, monkeypatch):
    """Deviation from the brief: the plan predates render/pipeline.py's #131
    full-decode validation, and its ladder steps only checked Content-Type.
    A PNG-signed body whose IDAT stream is nonsense passes that check and
    would otherwise reach badges/compose.py's `_load` -- straight into
    Pillow, since overlay images never go through magick at all -- so this
    proves the decode runs before the file is trusted, and that a refusal
    does not leave a corrupt file behind for the next call to treat as a
    cache hit.
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )

    def handler(request):
        return httpx.Response(
            200, content=b"\x89PNG\r\n\x1a\nnot a real png stream",
            headers={"content-type": "image/png"},
        )

    definition = OverlayDefinition(name="o", url="https://example.com/bad.png")
    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                definition, overlays_root=overlays_root, http=http, max_bytes=1000
            )
    assert not (overlays_root / ".cache").exists() or not list(
        (overlays_root / ".cache").iterdir()
    ), "a failed decode must not leave a corrupt file cached under its URL hash"
