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
    full-decode validation (see `resolve_image_path`'s
    `_validate_overlay_image` step, now run on every rung -- M1). A real
    image is needed here so this test still proves what it says it proves: a
    cache hit that skips the second request, not a decode refusal that
    happens to also skip it.
    """
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_a_file_source_resolves_under_overlays_root(overlays_root):
    """M1: `file:` now full-decode validates like the `url:` rung, so this
    needs a genuinely decodable image -- see `_tiny_png`'s own docstring for
    why `b"x"` used to pass here."""
    (overlays_root / "mine.png").write_bytes(_tiny_png())
    path = await resolve_image_path(
        OverlayDefinition(name="o", file="mine.png"),
        overlays_root=overlays_root, http=None, max_bytes=1000,
    )
    assert path == overlays_root / "mine.png"


async def test_a_file_source_that_is_not_a_decodable_image_is_refused(overlays_root):
    """M1: the `file:` rung used to hand a path straight to `compose.py`'s
    `_load` undecoded -- a truncated upload, a saved HTML error page, a
    `.png` that is really something else -- raising deep inside the compose
    worker thread instead of a clean per-definition refusal here."""
    (overlays_root / "mine.png").write_bytes(b"x")
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", file="mine.png"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_file_source_cannot_escape_overlays_root(overlays_root):
    """An operator-typed path is not a licence to read the filesystem."""
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", file="../../etc/passwd"),
            overlays_root=overlays_root, http=None, max_bytes=1000,
        )


async def test_a_file_source_naming_the_root_itself_is_refused(overlays_root):
    """`_confined`'s only earlier escape hatch was `candidate != root`, so
    `file: "."` (or any directory under the root) passed containment and
    `.exists()`, returning a directory that `_load` then failed on deep
    inside the compose thread instead of a clean refusal here."""
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", file="."),
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


async def test_a_builtin_source_that_is_not_a_decodable_image_is_refused(
    overlays_root, monkeypatch, tmp_path
):
    """M1: the `builtin:` rung had the same gap as `file:` -- no decode check
    before the path reaches compose.py's `_load`. The bundled tree is source,
    not a fixture, so the tree itself is monkeypatched to a scratch directory
    carrying an undecodable stub, the same shape `_confined`'s own tests use
    for `IMAGES`."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "Broken.png").write_bytes(b"x")
    monkeypatch.setattr("autoposter.overlays.sources.IMAGES", bundled)
    with pytest.raises(OverlaySourceError):
        await resolve_image_path(
            OverlayDefinition(name="o", builtin="Broken"),
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
    """Probe section 1.2 step 6. M1: the fallback rung is now decode-validated
    too, so this needs a real image the same way the `file:` rung's own test
    does."""
    (overlays_root / "mystamp.png").write_bytes(_tiny_png())
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


async def test_a_transport_failure_leaves_no_partial_file_and_is_not_fatal_to_the_stage(
    overlays_root, monkeypatch
):
    """MEDIUM 3: `guarded_download` does not wrap transport errors --
    `http.stream(...)` raising `httpx.ConnectError` mid-request propagates
    raw. `_download` short-circuits on `destination.exists()` *before* any
    validation, so a partial file left at the permanent cache path would be
    a permanent poisoned cache entry for every later run. It also has to
    surface as `OverlaySourceError`: `apply_badges`'s per-definition loop
    only catches that class, and an untranslated transport exception would
    fail the whole badge stage for every item over one definition's flaky
    CDN.
    """
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: ["93.184.216.34"]
    )

    def handler(request):
        raise httpx.ConnectError("connection refused")

    definition = OverlayDefinition(name="o", url="https://example.com/flaky.png")
    async with _client(handler) as http:
        with pytest.raises(OverlaySourceError):
            await resolve_image_path(
                definition, overlays_root=overlays_root, http=http, max_bytes=1000
            )
    cache = overlays_root / ".cache"
    assert not cache.exists() or not list(cache.iterdir()), (
        "a transport failure must not leave a temp or partial file behind"
    )
